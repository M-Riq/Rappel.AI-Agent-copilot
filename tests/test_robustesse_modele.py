"""Robustesse du moteur face aux reponses imparfaites d'un vrai modele (fournisseur scripte)."""

import json
from pathlib import Path

import pytest

from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import LLMEnquete, MemoireEnquete
from rappel.enquete.boucle_sql import REQUETES_TYPES
from rappel.enquete.modeles import ContexteIncident
from rappel.interfaces import _faux_sql
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.interfaces.llm import FakeLLM
from rappel.interfaces.run_sql import lister_constructions_non_portables


RACINE = Path(__file__).resolve().parents[1]


class FournisseurScripte:
    """FakeLLM dont la reponse d'un role est transformee par une fonction (texte -> texte)."""

    def __init__(self, role, transformer):
        self.base = FakeLLM()
        self.role = role
        self.transformer = transformer
        self.appels = []

    def completer(self, prompt, role, tools=None):
        self.appels.append(role)
        reponse = self.base.completer(prompt, role)
        if role == self.role:
            return {**reponse, "texte": self.transformer(reponse["texte"])}
        return reponse


def lancer(tmp_path, role, transformer):
    contexte = ContexteIncident.model_validate_json(
        (RACINE / "fixtures" / "contexte_p1.json").read_text(encoding="utf-8"))
    fournisseur = FournisseurScripte(role, transformer)
    resultat = enqueter(contexte, MemoireEnquete.json(tmp_path / "m.json"), LLMEnquete(fournisseur),
                        AbstentionSeuil(), mode_preuves="sql")
    return resultat, fournisseur


def modifier_candidats(modification):
    def transformer(texte):
        candidats = json.loads(texte)
        modification(candidats)
        return json.dumps(candidats)
    return transformer


def verifier_abstention_propre(resultat, motif):
    assert resultat.decision_abstention.abstient and resultat.correctif is None
    assert any(motif in element for element in resultat.decision_abstention.manque), resultat.decision_abstention.manque
    assert resultat.evenements[-1].type == "abstention"


@pytest.mark.parametrize("transformer", [
    lambda texte: f"Voici mes hypotheses :\n{texte}\nBonne enquete !",
    lambda texte: f"```json\n{texte}\n```",
], ids=["texte-autour", "bloc-json"])
def test_json_entoure_accepte(tmp_path, transformer):
    resultat, fournisseur = lancer(tmp_path, "hypotheses", transformer)
    assert resultat.correctif is not None
    assert fournisseur.appels.count("hypotheses") == 1


@pytest.mark.parametrize("transformer", [
    lambda texte: texte[: len(texte) // 2],
    lambda texte: "Je pense que le schema a change, verifiez les colonnes.",
    lambda texte: "[]",
    modifier_candidats(lambda candidats: [candidat.pop("sql") for candidat in candidats]),
], ids=["json-tronque", "sans-json", "liste-vide", "sans-sql"])
def test_hypotheses_invalides_une_relance_puis_abstention(tmp_path, transformer):
    resultat, fournisseur = lancer(tmp_path, "hypotheses", transformer)
    assert fournisseur.appels == ["hypotheses", "hypotheses"]
    assert resultat.metriques.nb_tours == 2
    verifier_abstention_propre(resultat, "reponse hypotheses invalide apres une nouvelle tentative")


def test_sql_avec_point_virgule_final(tmp_path):
    def ajouter(candidats):
        for candidat in candidats:
            candidat["sql"] += " ;"
    resultat, _ = lancer(tmp_path, "hypotheses", modifier_candidats(ajouter))
    assert resultat.correctif is not None
    assert all(not requete["sql"].rstrip().endswith(";") for requete in resultat.requetes_sql)


def test_sql_en_bloc_markdown(tmp_path):
    def entourer(candidats):
        for candidat in candidats:
            candidat["sql"] = f"```sql\n{candidat['sql']}\n```"
    resultat, _ = lancer(tmp_path, "hypotheses", modifier_candidats(entourer))
    assert resultat.correctif is not None
    executees = [requete["sql"] for requete in resultat.requetes_sql if requete["executee"]]
    assert executees and all("`" not in sql for sql in executees)
    preuves = [evenement.requete_sql for evenement in resultat.evenements if evenement.type == "preuve"]
    assert preuves == [sql for sql in executees]


def test_table_hors_liste_blanche(tmp_path):
    def remplacer(candidats):
        candidats[1]["sql"] = "SELECT * FROM utilisateurs"
    resultat, _ = lancer(tmp_path, "hypotheses", modifier_candidats(remplacer))
    refus = [evenement for evenement in resultat.evenements
             if evenement.type == "rejet" and "table hors liste blanche : utilisateurs" in evenement.texte]
    assert len(refus) == 1 and refus[0].requete_sql is None
    assert resultat.correctif is not None


def test_verdict_cite_une_requete_inexistante(tmp_path):
    def tricher(texte):
        verdict = json.loads(texte)
        for element in verdict["verdicts"]:
            element["requetes_citees"] = ["q99"]
            element["statut"] = "retenue"
        verdict["requetes_suivi"] = []
        return json.dumps(verdict)
    resultat, _ = lancer(tmp_path, "verdict", tricher)
    assert all(hypothese.raison_rejet == "aucune preuve SQL" for hypothese in resultat.hypotheses)
    verifier_abstention_propre(resultat, "hypothese etayee par une requete SQL a resultat non vide")


def test_verdict_statut_inconnu(tmp_path):
    def inconnu(texte):
        verdict = json.loads(texte)
        for element in verdict["verdicts"]:
            element["statut"] = "probable"
        return json.dumps(verdict)
    resultat, fournisseur = lancer(tmp_path, "verdict", inconnu)
    assert fournisseur.appels.count("verdict") == 2
    assert all(hypothese.statut == "refutee" for hypothese in resultat.hypotheses)
    verifier_abstention_propre(resultat, "reponse verdict invalide apres une nouvelle tentative")


def test_requetes_types_et_du_faux_portables():
    requetes = list(REQUETES_TYPES.values()) + [sql for _, _, sql in _faux_sql.CANDIDATS] + [
        _faux_sql.sql_commits(mots) for mots in _faux_sql.MOTS_PAR_CATEGORIE.values()]
    for sql in requetes:
        assert [element for element in lister_constructions_non_portables(sql)
                if element["remplacement"] is not None] == [], sql


@pytest.mark.parametrize(("sql", "construction"), [
    ("SELECT ifnull(valeur, 0) FROM metriques", "IFNULL"),
    ("SELECT group_concat(nom) FROM metriques", "GROUP_CONCAT"),
    ("SELECT strftime('%Y', date) FROM commits", "STRFTIME"),
    ("SELECT substr(message, 1, 5) FROM commits", "SUBSTR"),
    ("SELECT instr(message, 'a') FROM commits", "INSTR"),
    ("SELECT nom FROM metriques WHERE nom != 'x'", "!="),
    ("SELECT template FROM logs WHERE template GLOB '*x*'", "GLOB"),
    ("SELECT rowid FROM logs", "ROWID"),
])
def test_constructions_sqlite_detectees(sql, construction):
    trouvees = {element["construction"] for element in lister_constructions_non_portables(sql)}
    assert construction in trouvees


def test_litteraux_ignores_et_limit_a_confirmer():
    assert lister_constructions_non_portables("SELECT 'ifnull(' AS texte FROM logs") == []
    limite = lister_constructions_non_portables("SELECT nom FROM metriques LIMIT 50")
    assert [(element["construction"], element["remplacement"]) for element in limite] == [("LIMIT", None)]
