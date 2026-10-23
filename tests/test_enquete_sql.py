import ast
import json
from pathlib import Path

from unittest.mock import MagicMock

import pytest

from rappel.enquete.modeles import ContexteIncident, CorrectifPropose, EnregistrementMemoire, EvenementEnquete
from rappel.enquete import enqueter
from rappel.enquete.boucle_sql import RAISON_SANS_PREUVE
from rappel.interfaces import _faux_sql
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.enquete.adaptateurs import LLMFaux, creer_llm_enquete
from rappel.enquete.protocoles import lire_donnees
from rappel.interfaces import llm as llm_squelette
from rappel.enquete.adaptateurs import MemoireEnquete
from rappel.interfaces.run_sql import RunSqlSQLite


RACINE = Path(__file__).resolve().parents[1]


def charger(nom):
    return ContexteIncident.model_validate_json((RACINE / "fixtures" / f"contexte_{nom}.json").read_text(encoding="utf-8"))


class RunSqlEspion:
    def __init__(self, contexte):
        self.interne = RunSqlSQLite(contexte)
        self.mode = self.interne.mode
        self.executees = []

    def executer(self, sql):
        resultat = self.interne.executer(sql)
        self.executees.append(resultat)
        return resultat


def lancer(contexte, memoire, llm=None, **options):
    return enqueter(contexte, memoire, llm or LLMFaux(), AbstentionSeuil(), mode_preuves="sql", **options)


@pytest.fixture
def p1(tmp_path):
    contexte = charger("p1")
    espion = RunSqlEspion(contexte)
    memoire = MemoireEnquete.json(tmp_path / "memoire.json")
    return contexte, memoire, espion, lancer(contexte, memoire, run_sql=espion)


def valider_evenements(resultat):
    etapes = [evenement.etape for evenement in resultat.evenements]
    assert etapes == list(range(1, len(etapes) + 1))
    for evenement in resultat.evenements:
        assert EvenementEnquete.model_validate(evenement.model_dump()) == evenement


def test_a1_au_moins_deux_hypotheses_avec_statut(p1):
    resultat = p1[3]
    assert len(resultat.hypotheses) >= 2
    assert all(hypothese.statut in {"retenue", "refutee"} for hypothese in resultat.hypotheses)
    assert sum(hypothese.statut == "retenue" for hypothese in resultat.hypotheses) == 1


def test_a2_retenue_cite_requete_executee_non_vide(p1):
    contexte, _, _, resultat = p1
    requetes = {requete["id"]: requete for requete in resultat.requetes_sql}
    for hypothese in resultat.hypotheses:
        if hypothese.statut != "retenue":
            continue
        assert hypothese.preuves
        for preuve in hypothese.preuves:
            requete = requetes[preuve.split(" ", 1)[0]]
            assert requete["executee"] and requete["erreur"] is None and requete["nb_lignes"] > 0
            assert requete["hypothese_id"] == hypothese.id
            assert RunSqlSQLite(contexte).executer(requete["sql"]).nb_lignes == requete["nb_lignes"]


def test_a3_preuve_porte_le_sql_reellement_execute(p1):
    _, _, espion, resultat = p1
    preuves = [evenement for evenement in resultat.evenements if evenement.type == "preuve"]
    executees = [execution.requete for execution in espion.executees if execution.erreur is None]
    assert preuves and [evenement.requete_sql for evenement in preuves] == executees
    assert all(evenement.acteur == "detective" and evenement.modele == "faux-rapide" for evenement in preuves)
    assert resultat.metriques.nb_requetes_sql == len(espion.executees)


def test_a4_evenements_valides_et_modeles(p1):
    resultat = p1[3]
    valider_evenements(resultat)
    for evenement in resultat.evenements:
        if evenement.type in {"hypothese", "preuve"}:
            assert evenement.acteur == "detective" and evenement.modele == "faux-rapide"
        else:
            assert evenement.acteur == "systeme"
        if evenement.type != "preuve":
            assert evenement.requete_sql is None
    modeles = {evenement.modele for evenement in resultat.evenements if evenement.texte.startswith(("Cause confirmee", "Correctif"))}
    assert modeles == {"faux-profond"}
    assert any(evenement.texte.startswith("Verdict") and evenement.modele == "faux-rapide" for evenement in resultat.evenements)
    assert any(evenement.modele is None for evenement in resultat.evenements)


def test_a5_colonne_renommee_et_correctif_valide(p1):
    contexte, _, _, resultat = p1
    correctif = resultat.correctif
    assert correctif is not None
    assert CorrectifPropose.model_validate(correctif.model_dump()) == correctif
    assert "colonne renommée" in correctif.justification
    assert correctif.niveau_risque == "validation" and correctif.reversible
    patch = json.loads(correctif.patch)
    assert patch["apres"] == contexte.schema.attendu and patch["avant"] == contexte.schema.observe
    assert correctif.elements_touches == [contexte.signature.partition(":")[2]]
    retenue = next(hypothese for hypothese in resultat.hypotheses if hypothese.statut == "retenue")
    assert all(preuve.split(" ", 1)[0] in correctif.justification for preuve in retenue.preuves)
    assert resultat.enregistrement is not None and resultat.mode_preuves == "sql"
    assert resultat.source_preuves == "simule-sqlite"


def test_metriques_p1(p1):
    metriques = p1[3].metriques
    assert metriques.nb_tours == metriques.nb_appels_llm == metriques.nb_appels_rapide + metriques.nb_appels_profonde
    assert metriques.nb_appels_profonde == 2 and metriques.nb_tours <= 6
    assert metriques.nb_evenements == len(p1[3].evenements)


@pytest.mark.parametrize("max_tours", [1, 2, 4])
def test_a6_plafond_de_tours(tmp_path, max_tours):
    resultat = lancer(charger("p1"), MemoireEnquete.json(tmp_path / "memoire.json"), max_tours=max_tours)
    assert any(evenement.type == "decision" and evenement.texte.startswith(f"Plafond de {max_tours} tours atteint")
               for evenement in resultat.evenements)
    assert resultat.correctif is None and resultat.enregistrement is None
    assert resultat.decision_abstention.abstient
    assert resultat.evenements[-1].type == "abstention"
    assert f"plafond de {max_tours} tours atteint avant la conclusion" in resultat.decision_abstention.manque
    assert resultat.metriques.nb_tours == max_tours
    valider_evenements(resultat)


def test_plafond_par_defaut_six(tmp_path):
    class Insistant(LLMFaux):
        def completer(self, prompt, role="detective", tools=None):
            texte = super().completer(prompt, role)
            if role != "verdict":
                return texte
            donnees = lire_donnees(prompt)
            return json.dumps({"verdicts": [], "requetes_suivi": [
                {"hypothese_id": donnees["hypotheses"][0]["id"], "sql": "SELECT nom FROM metriques"}]})

    resultat = lancer(charger("p1"), MemoireEnquete.json(tmp_path / "memoire.json"), Insistant())
    # Suivis plafonnes a 2, puis verdict vide : rien n'est retenu, la boucle s'arrete proprement.
    assert sum(requete["origine"] == "suivi" for requete in resultat.requetes_sql) == 2
    assert resultat.correctif is None and resultat.metriques.nb_tours <= 6


class VerdictMensonger(LLMFaux):
    def completer(self, prompt, role="detective", tools=None):
        texte = super().completer(prompt, role)
        if role != "verdict":
            return texte
        hypotheses = lire_donnees(prompt)["hypotheses"]
        ids = [hypothese["id"] for hypothese in hypotheses]
        vide = hypotheses[2]["requetes"][0]["id"]
        assert hypotheses[2]["requetes"][0]["nb_lignes"] == 0
        emprunt = hypotheses[0]["requetes"][0]["id"]
        verdicts = [{"id": ids[0], "statut": "refutee", "requetes_citees": [], "raison": "par jeu"},
                    {"id": ids[1], "statut": "retenue", "requetes_citees": ["q99"], "raison": "invente"},
                    {"id": ids[2], "statut": "retenue", "requetes_citees": [vide], "raison": "vide"},
                    {"id": ids[3], "statut": "retenue", "requetes_citees": [emprunt], "raison": "emprunt"}]
        return json.dumps({"verdicts": verdicts})


def test_verdict_mensonger_refute_par_le_code(tmp_path):
    resultat = lancer(charger("p1"), MemoireEnquete.json(tmp_path / "memoire.json"), VerdictMensonger())
    assert not any(hypothese.statut == "retenue" for hypothese in resultat.hypotheses)
    menteuses = [hypothese for hypothese in resultat.hypotheses if hypothese.raison_rejet == RAISON_SANS_PREUVE]
    assert len(menteuses) == 3
    assert resultat.correctif is None and resultat.decision_abstention.abstient
    assert any("q99" in evenement.texte and RAISON_SANS_PREUVE in evenement.texte for evenement in resultat.evenements)


class HypothesesDangereuses(LLMFaux):
    def completer(self, prompt, role="detective", tools=None):
        texte = super().completer(prompt, role)
        if role != "hypotheses":
            return texte
        return json.dumps([
            {"texte": "Destruction", "sql": "DROP TABLE logs"},
            {"texte": "Double instruction", "sql": "SELECT * FROM logs; DELETE FROM logs"},
            {"texte": "Catalogue", "sql": "SELECT name FROM sqlite_master"},
            {"texte": "Lecture saine", "sql": "SELECT template FROM logs WHERE compte > 1000"},
        ])


def test_sql_dangereux_refuse_sans_execution(tmp_path):
    contexte = charger("p1")
    espion = RunSqlEspion(contexte)
    resultat = lancer(contexte, MemoireEnquete.json(tmp_path / "memoire.json"), HypothesesDangereuses(), run_sql=espion)
    assert [execution.requete for execution in espion.executees] == [
        "SELECT template FROM logs WHERE compte > 1000 LIMIT 50"]
    refus = [evenement for evenement in resultat.evenements if evenement.type == "rejet" and "requete refusee" in evenement.texte]
    assert len(refus) == 3 and all(evenement.requete_sql is None for evenement in refus)
    assert not any(evenement.type == "preuve" and "DROP" in (evenement.requete_sql or "") for evenement in resultat.evenements)
    assert espion.interne.executer("SELECT count(*) FROM logs").lignes == [[len(contexte.logs)]]
    valider_evenements(resultat)


def test_p2_apres_p1_economie_et_aucun_retest(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "partagee.json")
    premier = lancer(charger("p1"), memoire)
    espion = RunSqlEspion(charger("p2"))
    second = lancer(charger("p2"), memoire, run_sql=espion)
    assert second.correctif is not None and not second.decision_abstention.abstient
    assert second.metriques.nb_evenements <= 0.6 * premier.metriques.nb_evenements
    assert second.metriques.nb_appels_llm <= 0.6 * premier.metriques.nb_appels_llm
    assert second.metriques.nb_appels_llm < premier.metriques.nb_appels_llm
    refutees = premier.enregistrement.hypotheses_refutees
    assert refutees and not any(hypothese.texte in refutees for hypothese in second.hypotheses)
    sql_refutees = {sql for texte, _, sql in _faux_sql.CANDIDATS if texte in refutees}
    assert not any(execution.requete.removesuffix(" LIMIT 50") in sql_refutees for execution in espion.executees)
    assert second.metriques.nb_ecartees_par_memoire == len(refutees)
    assert "type de colonne modifié" in second.correctif.justification


def test_memoire_contraire_aucun_correctif_de_schema(tmp_path):
    contexte = charger("p1")
    schema = next(texte for texte, categorie, _ in _faux_sql.CANDIDATS if categorie == "schema")
    memoire = MemoireEnquete.json(tmp_path / "contraire.json")
    memoire.ecrire(EnregistrementMemoire(
        signature=contexte.signature, cause_confirmee="Diagnostic errone", hypotheses_refutees=[schema],
        correctif_essaye="Autre correction", resultat="echoue", date="2026-10-14",
    ))
    resultat = lancer(contexte, memoire)
    assert resultat.correctif is None and resultat.decision_abstention.abstient
    assert schema not in [hypothese.texte for hypothese in resultat.hypotheses]
    assert resultat.metriques.nb_ecartees_par_memoire == 1


def test_p3_abstention_elements_manquants(tmp_path):
    resultat = lancer(charger("p3"), MemoireEnquete.json(tmp_path / "memoire.json"))
    assert resultat.decision_abstention.abstient
    assert resultat.correctif is None and resultat.enregistrement is None
    manque = resultat.decision_abstention.manque
    # P3 de l'equipe : un indice de timeout isole est retenu puis rejete par l'abstention.
    assert any("deuxième source indépendante" in element for element in manque)
    assert "incident similaire en mémoire" in manque
    abstention = [evenement for evenement in resultat.evenements if evenement.type == "abstention"]
    assert len(abstention) == 1 and all(element in abstention[0].texte for element in manque)
    assert resultat.metriques.nb_appels_profonde == 0
    valider_evenements(resultat)


def test_hypotheses_ecrites_en_memoire(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "memoire.json")
    premier = lancer(charger("p1"), memoire)
    lancer(charger("p3"), memoire)
    lignes = memoire.lister_hypotheses()
    assert [ligne["statut"] for ligne in lignes[:4]] == [hypothese.statut for hypothese in premier.hypotheses]
    assert {ligne["signature"] for ligne in lignes} == {charger("p1").signature, charger("p3").signature}
    assert len(memoire.lister_hypotheses(charger("p3").signature)) == 4
    assert memoire.chemin_hypotheses != memoire.chemin


def test_reponse_hypotheses_invalide_abstient(tmp_path):
    class Bavard(LLMFaux):
        def completer(self, prompt, role="detective", tools=None):
            texte = super().completer(prompt, role)
            return "pas de JSON" if role == "hypotheses" else texte

    resultat = lancer(charger("p1"), MemoireEnquete.json(tmp_path / "memoire.json"), Bavard())
    assert resultat.metriques.nb_tours == 2 and resultat.hypotheses == []
    assert resultat.decision_abstention.abstient and resultat.correctif is None


def test_mode_inconnu_refuse(tmp_path):
    with pytest.raises(ValueError, match="mode_preuves"):
        enqueter(charger("p1"), MemoireEnquete.json(tmp_path / "m.json"), LLMFaux(), AbstentionSeuil(), mode_preuves="autre")
    with pytest.raises(ValueError, match="max_tours"):
        lancer(charger("p1"), MemoireEnquete.json(tmp_path / "m.json"), max_tours=0)


def test_mode_sql_par_defaut(tmp_path):
    resultat = enqueter(charger("p1"), MemoireEnquete.json(tmp_path / "m.json"), LLMFaux(), AbstentionSeuil())
    assert resultat.mode_preuves == "sql" and resultat.metriques.nb_requetes_sql > 0


def test_meterless_simule_modele_reel_dans_les_evenements(tmp_path, monkeypatch):
    for nom, valeur in (("LLM_PROVIDER", "meterless"), ("METERLESS_RUNTIME_TOKEN", "jeton-factice"),
                        ("METERLESS_MODEL_RAPIDE", "voie-rapide"), ("METERLESS_MODEL_PROFOND", "voie-profonde"),
                        ("LLM_MAX_APPELS", "20")):
        monkeypatch.setenv(nom, valeur)
    servis = {"voie-rapide": "nemotron-mock-lightning", "voie-profonde": "nemotron-mock-super"}
    requetes = []

    def poster(url, **parametres):
        requetes.append((url, parametres))
        corps = parametres["json"]
        prompt = corps["messages"][0]["content"]
        role = prompt.split(".", 1)[0].removeprefix("Role: ")
        reponse = MagicMock(status_code=200)
        reponse.json.return_value = {
            "model": servis[corps["model"]],
            "choices": [{"message": {"content": _faux_sql.repondre(role, lire_donnees(prompt))}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }
        return reponse

    monkeypatch.setattr(llm_squelette.requests, "post", poster)
    llm = creer_llm_enquete()
    resultat = lancer(charger("p1"), MemoireEnquete.json(tmp_path / "m.json"), llm)
    modeles = {evenement.modele for evenement in resultat.evenements} - {None}
    assert modeles == {"nemotron-mock-lightning", "nemotron-mock-super"}
    assert resultat.correctif is not None
    metriques = resultat.metriques
    assert (metriques.nb_appels_rapide, metriques.nb_appels_profonde) == (
        llm.usage.par_voie["rapide"].nb_appels, llm.usage.par_voie["profonde"].nb_appels)
    assert metriques.nb_tokens_entree == 3 * metriques.nb_appels_llm
    assert all(parametres["headers"]["meterless-app"] == "rappel" for _, parametres in requetes)
    assert "jeton-factice" not in repr(llm) and "jeton-factice" not in str(llm.derniere_reponse_info)


def test_non_triche_couvre_les_nouveaux_fichiers():
    # Perimetre : le moteur et ses interfaces SQL ; le squelette de l'equipe (api, sources de
    # fixtures, champ total_tokens de l'API OpenAI) n'est pas un code de decision du moteur.
    racine = RACINE / "src"
    moteur = {"boucle_sql.py", "graphe.py", "outils.py", "war_room.py", "traceur.py", "adaptateurs.py",
              "protocoles.py", "modeles.py", "run_sql.py", "_faux_sql.py", "abstention.py"}
    nouveaux = {"boucle_sql.py", "run_sql.py", "_faux_sql.py", "adaptateurs.py"}
    interdits = ("amount", "total", "created_at", "P1", "P2", "P3", "orders_etl",
                 "schema_mismatch:orders_etl", "latency_anomaly:orders_etl")
    vus = set()
    for chemin in racine.rglob("*.py"):
        if chemin.name not in moteur:
            continue
        if chemin.name in nouveaux:
            vus.add(chemin.name)
        for noeud in ast.walk(ast.parse(chemin.read_text(encoding="utf-8"))):
            if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
                assert not any(interdit in noeud.value for interdit in interdits), chemin
    assert vus == nouveaux
