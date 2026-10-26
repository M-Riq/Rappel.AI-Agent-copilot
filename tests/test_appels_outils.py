import json
from pathlib import Path

import pytest

from rappel.config import Configuration, charger
from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import LLMEnquete, MemoireEnquete
from rappel.enquete.boucle_sql import analyser_appels_outils
from rappel.enquete.modeles import ContexteIncident
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.interfaces.llm import FakeLLM
from rappel.interfaces.tools_zetaris import RUN_SQL_TOOL


RACINE = Path(__file__).resolve().parents[1]


def charger_contexte(nom="p1"):
    return ContexteIncident.model_validate_json((RACINE / "fixtures" / f"contexte_{nom}.json").read_text(encoding="utf-8"))


def appel(sql, nom="run_sql", brut=False):
    return {"id": "appel", "type": "function",
            "function": {"name": nom, "arguments": sql if brut else json.dumps({"sql": sql})}}


class FournisseurOutils:
    """Rend les requetes par tool_calls au lieu du champ sql du texte, comme un modele natif."""

    def __init__(self, appels_supplementaires=(), sans_texte=False):
        self.base = FakeLLM()
        self.outils_recus = []
        self.appels_supplementaires = list(appels_supplementaires)
        self.sans_texte = sans_texte

    def completer(self, prompt, role, tools=None):
        self.outils_recus.append((role, tools))
        reponse = self.base.completer(prompt, role)
        if role == "hypotheses":
            candidats = json.loads(reponse["texte"])
            appels = [appel(candidat.pop("sql")) for candidat in candidats] + self.appels_supplementaires
            texte = "" if self.sans_texte else json.dumps(candidats)
            return {**reponse, "texte": texte, "tool_calls": appels}
        if role == "verdict":
            verdict = json.loads(reponse["texte"])
            appels = [appel(suivi.pop("sql")) for suivi in verdict.get("requetes_suivi", [])]
            return {**reponse, "texte": json.dumps(verdict), "tool_calls": appels}
        return reponse


def lancer(tmp_path, fournisseur, **options):
    return enqueter(charger_contexte(), MemoireEnquete.json(tmp_path / "m.json"), LLMEnquete(fournisseur),
                    AbstentionSeuil(), mode_preuves="sql", **options)


def test_configuration_appel_outils(monkeypatch):
    assert Configuration().appel_outils is False and charger().appel_outils is False
    monkeypatch.setenv("LLM_APPEL_OUTILS", "1")
    assert charger().appel_outils is True
    monkeypatch.setenv("LLM_APPEL_OUTILS", "peut-etre")
    with pytest.raises(ValueError, match="LLM_APPEL_OUTILS"):
        charger()


def test_mode_par_defaut_sans_outils(tmp_path):
    fournisseur = FournisseurOutils()
    fournisseur.completer = lambda prompt, role, tools=None, base=FakeLLM(): (
        fournisseur.outils_recus.append((role, tools)) or base.completer(prompt, role))
    resultat = lancer(tmp_path, fournisseur)
    assert resultat.correctif is not None
    assert all(outils is None for _, outils in fournisseur.outils_recus)


def test_tool_calls_fournissent_les_requetes(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_APPEL_OUTILS", "1")
    fournisseur = FournisseurOutils()
    resultat = lancer(tmp_path, fournisseur)
    assert resultat.correctif is not None and "colonne renommée" in resultat.correctif.justification
    for role, outils in fournisseur.outils_recus:
        assert outils == ([RUN_SQL_TOOL] if role in ("hypotheses", "verdict") else None)
    origines = [requete["origine"] for requete in resultat.requetes_sql]
    assert origines.count("hypothese") == 4 and origines.count("suivi") == 1
    preuves = [evenement for evenement in resultat.evenements if evenement.type == "preuve"]
    assert all(evenement.requete_sql for evenement in preuves)


def test_tool_calls_invalides_rejetes_sans_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_APPEL_OUTILS", "1")
    # Appels invalides places apres les appels valides des hypotheses.
    mauvais = [appel("SELECT 1", nom="autre_outil"), appel("{pas du json", brut=True), appel("   "),
               {"type": "function"}]
    fournisseur = FournisseurOutils(appels_supplementaires=mauvais)
    resultat = lancer(tmp_path, fournisseur, appel_outils=True)
    rejets = [evenement.texte for evenement in resultat.evenements if evenement.texte.startswith("Appel d'outil")]
    assert rejets == [
        "Appel d'outil run_sql rejete : outil inconnu : autre_outil",
        "Appel d'outil run_sql rejete : arguments JSON invalides",
        "Appel d'outil run_sql rejete : requete vide",
        "Appel d'outil run_sql rejete : appel d'outil illisible",
    ]
    assert resultat.correctif is not None


def test_plusieurs_instructions_refusees_par_valider_sql(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_APPEL_OUTILS", "1")
    fournisseur = FournisseurOutils(appels_supplementaires=[appel("SELECT 1; DROP TABLE logs")])
    resultat = lancer(tmp_path, fournisseur)
    refus = [evenement for evenement in resultat.evenements
             if evenement.type == "rejet" and "une seule instruction" in evenement.texte]
    assert len(refus) == 1 and refus[0].requete_sql is None
    assert not any("DROP" in (requete["sql"] or "") and requete["executee"] for requete in resultat.requetes_sql)


def test_uniquement_des_tool_calls_sans_texte(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_APPEL_OUTILS", "1")
    resultat = lancer(tmp_path, FournisseurOutils(sans_texte=True))
    textes = [hypothese.texte for hypothese in resultat.hypotheses]
    assert textes[0] == "Hypothese du modele n1 (appel run_sql)" and len(textes) == 4
    assert resultat.metriques.nb_requetes_sql >= 4


def test_analyser_appels_outils_pur():
    requetes, rejets = analyser_appels_outils([appel("SELECT nom FROM metriques"), None, appel("")])
    assert requetes == ["SELECT nom FROM metriques"]
    assert rejets == ["appel d'outil illisible", "requete vide"]
