"""Audit R1 : non-régression des défauts corrigés (sécurité, fiabilité, exactitude, portabilité, docs)."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from unittest.mock import MagicMock

import pytest
import requests

from rappel.action.executeur import REFUSE, executer_correctif
from rappel.config import Configuration
from rappel.contrats import valider
from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import LLMFaux, MemoireEnquete
from rappel.enquete.modeles import ContexteIncident, CorrectifPropose, EnregistrementMemoire
from rappel.interfaces import llm, tools_zetaris
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.interfaces.run_sql import TABLES, valider_sql
from rappel.orchestration import boucle
from rappel.pipeline_demo import dossier_demo, empreinte_dossier, injecter
from rappel.securite import masquer_secrets, url_service
from scripts._essai_commun import nettoyer

RACINE = Path(__file__).resolve().parents[1]
JETON = "jeton-" + "factice-audit-123"


def contexte(nom):
    return ContexteIncident.model_validate_json((RACINE / "fixtures" / f"contexte_{nom}.json").read_text(encoding="utf-8"))


# (a) Sécurité : secrets


def test_masquage_valeurs_url_et_porteur(monkeypatch):
    monkeypatch.setenv("ZETARIS_API_KEY", JETON)
    texte = (f"cle {JETON} ; postgresql://rappel:motdepasse-factice@hote:5432/b ; "
             "Authorization: Bearer abcdefghijklmnop")
    for masque in (masquer_secrets(texte), nettoyer(texte), boucle.assainir(texte)):
        assert JETON not in masque and "motdepasse-factice" not in masque and "abcdefghijklmnop" not in masque
        assert "postgresql://[MASQUE]@hote:5432/b" in masque


# (a) Sécurité : URL des services (SSRF, identifiants, normalisation)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://hote/v1", "gopher://hote", "http://", "",
                                 "http://utilisateur:secret@hote/v1", "http://hote/v1?cle=x", "http://hote:99999/v1"])
def test_url_refusee(url):
    with pytest.raises(ValueError) as erreur:
        url_service(url, "METERLESS_BASE_URL", ("/chat/completions",), "/v1")
    assert "secret" not in str(erreur.value)


@pytest.mark.parametrize("url, attendu", [
    ("127.0.0.1:8088", "http://127.0.0.1:8088/v1"),
    ("localhost:8088/v1/", "http://localhost:8088/v1"),
    ("[::1]:8088", "http://[::1]:8088/v1"),
    ("http://127.0.0.1:8088/v1/chat/completions", "http://127.0.0.1:8088/v1"),
    ("http://127.0.0.1:8088/v1/models", "http://127.0.0.1:8088/v1"),
    ("http://127.0.0.1:8088/healthz", "http://127.0.0.1:8088/v1"),
    ("http://127.0.0.1:8088/v1/v1", "http://127.0.0.1:8088/v1"),
    ("HTTP://Nightshift.Exemple:443//v1", "http://nightshift.exemple:443/v1"),
    ("nightshift.exemple", "https://nightshift.exemple/v1"),
])
def test_url_normalisee(url, attendu):
    assert url_service(url, "METERLESS_BASE_URL", ("/chat/completions", "/models", "/healthz"), "/v1") == attendu


def reponse_ok(donnees):
    resultat = MagicMock(status_code=200)
    resultat.json.return_value = donnees
    return resultat


def test_meterless_jeton_vers_l_hote_configure_seulement(monkeypatch):
    appels = []
    monkeypatch.setattr(requests, "post", lambda url, **p: appels.append((url, p)) or reponse_ok(
        {"model": "m", "choices": [{"message": {"content": "ok"}}]}))
    configuration = Configuration(meterless_runtime_token=JETON, meterless_base_url="127.0.0.1:8088/v1/chat/completions")
    llm.MeterlessLLM(configuration).completer("p", "hypotheses")
    url, parametres = appels[0]
    assert url == "http://127.0.0.1:8088/v1/chat/completions" and parametres["allow_redirects"] is False
    appels.clear()
    with pytest.raises(ValueError) as erreur:
        llm.MeterlessLLM(Configuration(meterless_runtime_token=JETON, meterless_base_url="file:///x")).completer("p", "hypotheses")
    assert appels == [] and JETON not in str(erreur.value)


def test_zetaris_cle_vers_l_hote_configure_seulement(monkeypatch):
    appels = []
    monkeypatch.setattr(requests, "post", lambda url, **p: appels.append((url, p)) or reponse_ok({"rows": [{"one": 1}]}))
    configuration = Configuration(source_provider="zetaris", zetaris_api_key=JETON,
                                  zetaris_base_url="http://localhost:8888/api/v1.0/sql-editor/sqls/run")
    tools_zetaris.run_sql("SELECT 1 AS one", configuration=configuration)
    assert appels[0][0] == "http://localhost:8888/api/v1.0/sql-editor/sqls/run"
    assert appels[0][1]["allow_redirects"] is False
    appels.clear()
    with pytest.raises(ValueError):
        tools_zetaris.run_sql("SELECT 1", configuration=Configuration(
            source_provider="zetaris", zetaris_api_key=JETON, zetaris_base_url="http://u:p@hote/api"))
    assert appels == []


# (a) Sécurité : garde-fous SQL


@pytest.mark.parametrize("sql", [
    "SELECT 1\uff1b DROP TABLE logs",  # point-virgule pleine chasse
    "SELECT\u00a01",  # espace insécable
    "SELECT 1\x00",  # octet nul
    "SELECT * FROM l\u043egs",  # homoglyphe cyrillique
    "SELECT pg_sleep(10)", "SELECT sleep(5)", "SELECT load_file('/etc/passwd')",
    "SELECT * FROM logs WHERE 1 = benchmark(1000, 1)", "SELECT pg_read_file('x')",
    "SELECT 1 /* ; */ ; DROP TABLE logs", "sElEcT * fRoM logs; DeLeTe FrOm logs",
    "SELECT * FROM information_schema.tables", "SELECT * FROM sqlite_master",
    "WITH x AS (SELECT 1) INSERT INTO logs SELECT * FROM x",
    "SELECT " + "1," * 3000 + "1",
])
def test_sql_refuse(sql):
    assert valider_sql(sql, TABLES).erreur is not None


@pytest.mark.parametrize("sql", ["SeLeCt template FrOm LOGS", "SELECT 'é ; DROP' AS texte FROM logs",
                                 "SELECT template FROM logs -- commentaire ; DROP"])
def test_sql_accepte(sql):
    assert valider_sql(sql, TABLES).erreur is None


# (a) Sécurité : traversée de chemin et liens dans l'action


def correctif():
    patch = json.dumps({"avant": ["order_id:integer", "total:decimal"], "apres": ["order_id:integer", "amount:decimal"]})
    return CorrectifPropose(description="Restaurer", patch=patch, niveau_risque="auto", elements_touches=["etl"],
                            reversible=True, justification="q1", confiance=0.95)


def test_action_refuse_traversee(tmp_path):
    injecter("p1")
    for dossier in (dossier_demo() / ".." / "ailleurs", str(dossier_demo()) + "\\..\\..", tmp_path):
        assert executer_correctif(correctif(), dossier=dossier)["statut"] == REFUSE


def test_action_refuse_lien_symbolique(tmp_path):
    injecter("p1")
    exterieur = tmp_path / "exterieur"
    exterieur.mkdir()
    (exterieur / "precieux.txt").write_text("ne pas toucher", encoding="utf-8")
    try:
        os.symlink(exterieur, dossier_demo() / "lien", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("création de lien symbolique non autorisée sur ce poste")
    resultat = executer_correctif(correctif())
    assert resultat["statut"] == REFUSE and "lien symbolique" in resultat["message"]
    assert (exterieur / "precieux.txt").read_text(encoding="utf-8") == "ne pas toucher"


# (b) Fiabilité : registre « non confirmé » atomique, concurrent, sans orphelins


def souvenir(numero):
    return EnregistrementMemoire(signature="s:c", cause_confirmee=f"cause {numero}", hypotheses_refutees=[],
                                 correctif_essaye=f"corriger {numero}", resultat="reussi", date="2026-10-24")


def test_non_confirmes_concurrents_atomiques_et_orphelins(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "m.json")
    souvenirs = [souvenir(numero) for numero in range(12)]
    for element in souvenirs:
        memoire.ecrire(element)
    fils = [threading.Thread(target=memoire.marquer_non_confirme, args=(element,)) for element in souvenirs]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join()
    cles = json.loads(memoire.chemin_non_confirmes.read_text(encoding="utf-8"))
    assert len(cles) == 12
    assert not [chemin for chemin in tmp_path.iterdir() if chemin.name.startswith("tmp")]
    memoire.backend.vider()  # mémoire effacée sans passer par l'adaptateur : clés orphelines
    nouveau = souvenir(99)
    memoire.ecrire(nouveau)
    memoire.marquer_non_confirme(nouveau)
    assert json.loads(memoire.chemin_non_confirmes.read_text(encoding="utf-8")) == [memoire._cle(nouveau)]


def test_journal_illisible_tolere(tmp_path):
    chemin = tmp_path / "journal.jsonl"
    chemin.write_text('{"cycle": "c1", "agent": "ingestion", "etape": "contexte", "statut": "ok", '
                      '"duree_ms": 1, "mode": "simulé", "horodatage": "t"}\n{"cycle": "c1", "agent": "ret',
                      encoding="utf-8")
    assert boucle.lire_dernier_cycle(chemin)["cycle"] == "c1"


# (c) Exactitude


def test_metriques_et_regle_60pc_recalculees(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "m.json")
    llm_p1, llm_p2 = LLMFaux(), LLMFaux()
    p1 = enqueter(contexte("p1"), memoire, llm_p1, AbstentionSeuil())
    p2 = enqueter(contexte("p2"), memoire, llm_p2, AbstentionSeuil())
    for resultat, faux in ((p1, llm_p1), (p2, llm_p2)):
        assert resultat.metriques.nb_evenements == len(resultat.evenements)
        assert resultat.metriques.nb_appels_llm == faux.usage.nb_appels
        assert resultat.metriques.nb_appels_rapide + resultat.metriques.nb_appels_profonde == faux.usage.nb_appels
        assert resultat.metriques.nb_requetes_sql == sum(1 for e in resultat.evenements if e.requete_sql and "non executee" not in e.texte)
    ratio = len(p2.evenements) / len(p1.evenements)
    assert ratio <= 0.6 and p2.metriques.nb_appels_llm < p1.metriques.nb_appels_llm
    assert abs(ratio - 12 / 21) < 1e-9


class VerdictSurRequeteVide(LLMFaux):
    """Le modèle retient une hypothèse en citant une requête exécutée mais sans ligne."""

    def completer(self, prompt, role="detective", tools=None):
        texte = super().completer(prompt, role, tools)
        if role == "hypotheses":
            return json.dumps([{"texte": "cause inventee", "categorie": "schema",
                                "sql": "SELECT template FROM logs WHERE compte < 0"},
                               {"texte": "autre cause", "categorie": "reseau",
                                "sql": "SELECT nom FROM metriques WHERE valeur < -1"}])
        if role == "verdict":
            return json.dumps({"verdicts": [{"id": "p1:hyp-1", "statut": "retenue", "requetes_citees": ["q1"],
                                             "raison": "le modele l'affirme"}], "requetes_suivi": []})
        return texte


def test_aucune_conclusion_sans_requete_non_vide(tmp_path):
    resultat = enqueter(contexte("p1"), MemoireEnquete.json(tmp_path / "m.json"), VerdictSurRequeteVide(), AbstentionSeuil())
    assert resultat.correctif is None and resultat.decision_abstention.abstient
    assert not [h for h in resultat.hypotheses if h.statut == "retenue"]


@pytest.mark.parametrize("options", [{}, {"max_tours": 2}, {"war_room_active": True, "war_room_max_tours": 1}])
@pytest.mark.parametrize("nom", ["p1", "p3"])
def test_tous_les_evenements_valides(tmp_path, options, nom):
    resultat = enqueter(contexte(nom), MemoireEnquete.json(tmp_path / "m.json"), LLMFaux(), AbstentionSeuil(), **options)
    assert [e.etape for e in resultat.evenements] == list(range(1, len(resultat.evenements) + 1))
    for evenement in resultat.evenements:
        valider("evenement_enquete", evenement.model_dump(mode="json", exclude_none=True))


def test_seuil_abstention_et_rayon_bornes():
    from rappel.action.rayon_impact import evaluer_rayon_impact
    from rappel.enquete.modeles import Hypothese

    seuil = AbstentionSeuil(0.6)
    hypothese = lambda confiance: Hypothese(id="h", incident_id="i", texte="t", statut="retenue",
                                            preuves=["logs: x"], confiance=confiance, raison_rejet=None)
    assert not seuil.decider(hypothese(0.6), nb_sources=1, incident_similaire=False).abstient
    assert seuil.decider(hypothese(0.59), nb_sources=1, incident_similaire=False).abstient
    base = correctif()
    assert evaluer_rayon_impact(base.model_copy(update={"confiance": 0.9})) == "auto"
    assert evaluer_rayon_impact(base.model_copy(update={"confiance": 0.89})) == "validation"
    assert evaluer_rayon_impact(base.model_copy(update={"confiance": 0.59})) == "refus"
    assert evaluer_rayon_impact(base.model_copy(update={"reversible": False})) == "refus"


# (d) Portabilité Windows : sorties console cp1252


SCRIPTS_ARGPARSE = sorted(chemin.name for chemin in (RACINE / "scripts").glob("*.py")
                          if "argparse" in chemin.read_text(encoding="utf-8"))


@pytest.mark.parametrize("script", SCRIPTS_ARGPARSE)
def test_aide_des_scripts_en_cp1252(script):
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    resultat = subprocess.run([sys.executable, str(RACINE / "scripts" / script), "--help"], cwd=RACINE, env=env,
                              capture_output=True, timeout=60)
    assert resultat.returncode == 0, resultat.stderr.decode("cp1252", "replace")[-500:]


# (e) Cohérence documentation / code


AUTRES_LECTEURS = {"POSTGRES_LOCAL": ".env.example", "LANGFUSE_PUBLIC_KEY": "src/rappel/enquete/traceur.py",
                   "LANGFUSE_SECRET_KEY": "src/rappel/enquete/traceur.py", "LANGFUSE_HOST": "src/rappel/enquete/traceur.py",
                   "LANGFUSE_ENVOYER_TEXTES": "src/rappel/enquete/traceur.py",
                   "WAR_ROOM_ACTIVE": "src/rappel/enquete/war_room.py", "WAR_ROOM_MAX_TOURS": "src/rappel/enquete/war_room.py",
                   "WAR_ROOM_MAX_APPELS_LLM": "src/rappel/enquete/war_room.py"}


def test_variables_env_exemple_lues_et_documentees():
    exemple = (RACINE / ".env.example").read_text(encoding="utf-8")
    noms = re.findall(r"^([A-Z][A-Z0-9_]*)=", exemple, re.MULTILINE)
    champs = {nom.upper() for nom in Configuration.__dataclass_fields__} | {"LLM_APPEL_OUTILS"}
    docs = "".join(chemin.read_text(encoding="utf-8") for chemin in (RACINE / "docs").glob("*.md"))
    for nom in noms:
        if nom in champs:
            continue
        assert nom in AUTRES_LECTEURS, f"{nom} n'est lu ni par config.py ni par un module connu"
        assert nom in (RACINE / AUTRES_LECTEURS[nom]).read_text(encoding="utf-8")
    non_documentees = [nom for nom in noms if nom not in docs and nom != "POSTGRES_LOCAL"]
    assert non_documentees == []


def test_commandes_citees_dans_docs_existent():
    for document in (RACINE / "docs").glob("*.md"):
        for ligne in document.read_text(encoding="utf-8").splitlines():
            for script, reste in re.findall(r"python\s+scripts[/\\]([a-z_]+\.py)([^`|#]*)", ligne):
                chemin = RACINE / "scripts" / script
                assert chemin.exists(), f"{document.name} cite scripts/{script} absent"
                source = chemin.read_text(encoding="utf-8")
                for option in re.findall(r"(?<!\w)(--[a-z][a-z-]*)", reste):
                    assert option in source, f"{document.name} : {script} {option} inconnue"


def test_chiffres_documentes_produits_par_la_boucle(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "m.json")
    p1, p2, p3 = (cycle.resume for cycle in boucle.executer_sequence(["p1", "p2", "p3"], memoire=memoire))
    texte = (RACINE / "docs" / "module-enquete.md").read_text(encoding="utf-8")
    attendu = (f"P1 {p1['enquete']['nb_evenements']} evenements / {p1['enquete']['nb_appels_llm']} appels, "
               f"P2 {p2['enquete']['nb_evenements']} / {p2['enquete']['nb_appels_llm']}")
    assert attendu in texte
    assert f"P3 abstention ({p3['enquete']['nb_evenements']} evenements, {p3['enquete']['nb_appels_llm']} appels)" in texte


@pytest.mark.skipif(os.name != "nt", reason="jonctions NTFS : Windows seulement")
def test_action_refuse_jonction_windows(tmp_path):
    injecter("p1")
    exterieur = tmp_path / "exterieur_jonction"
    exterieur.mkdir()
    lien = dossier_demo() / "jonction"
    creation = subprocess.run(["cmd", "/c", "mklink", "/J", str(lien), str(exterieur)], capture_output=True)
    if creation.returncode != 0:
        pytest.skip("jonction impossible sur ce poste")
    try:
        resultat = executer_correctif(correctif())
        assert resultat["statut"] == REFUSE and "jonction" in resultat["message"]
        assert list(exterieur.iterdir()) == []
    finally:
        os.rmdir(lien)
