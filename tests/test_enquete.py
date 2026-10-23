import ast
import json
from pathlib import Path

import pytest

from src.rappel.contrats import ContexteIncident, CorrectifPropose, EnregistrementMemoire, EvenementEnquete
from src.rappel.enquete import enqueter, ecrire_evenements_jsonl
from src.rappel.interfaces.abstention import AbstentionSeuil
from src.rappel.interfaces.llm import LLMFaux
from src.rappel.interfaces.memoire import MemoireJSON


def charger(nom):
    chemin = Path(__file__).resolve().parents[1] / "fixtures" / nom
    return ContexteIncident.model_validate_json(chemin.read_text(encoding="utf-8"))


@pytest.fixture
def enquete_initiale(tmp_path):
    contexte = charger("contexte_p1.json")
    memoire = MemoireJSON(tmp_path / "memoire.json")
    resultat = enqueter(contexte, memoire, LLMFaux(), AbstentionSeuil(), mode_preuves="outils")
    return contexte, memoire, resultat


def valider_evenements(resultat):
    assert resultat.evenements
    etapes = [evenement.etape for evenement in resultat.evenements]
    assert etapes == list(range(1, len(etapes) + 1))
    for evenement in resultat.evenements:
        assert EvenementEnquete.model_validate(evenement.model_dump()) == evenement


def test_a1_hypotheses(enquete_initiale):
    resultat = enquete_initiale[2]
    assert len(resultat.hypotheses) >= 2
    assert all(hypothese.statut in {"ouverte", "retenue", "refutee"} for hypothese in resultat.hypotheses)


def test_a2_preuves_retenues(enquete_initiale):
    retenues = [hypothese for hypothese in enquete_initiale[2].hypotheses if hypothese.statut == "retenue"]
    assert retenues
    assert all(hypothese.preuves and all(preuve.strip() for preuve in hypothese.preuves) for hypothese in retenues)


def test_a3_evenements(enquete_initiale):
    valider_evenements(enquete_initiale[2])


def test_callback_evenements_dans_ordre(tmp_path):
    recus = []
    resultat = enqueter(
        charger("contexte_p1.json"), MemoireJSON(tmp_path / "memoire.json"),
        LLMFaux(), AbstentionSeuil(), sur_evenement=recus.append, mode_preuves="outils",
    )
    assert recus == resultat.evenements
    assert [element.etape for element in recus] == list(range(1, len(recus) + 1))


def test_a4_correctif(enquete_initiale):
    correctif = enquete_initiale[2].correctif
    assert correctif is not None
    assert CorrectifPropose.model_validate(correctif.model_dump()) == correctif
    assert "renomm" in correctif.justification
    assert correctif.niveau_risque in {"auto", "validation", "refus"}


def test_a5_memoire(enquete_initiale):
    contexte, memoire, resultat = enquete_initiale
    souvenirs = memoire.chercher(contexte.signature)
    assert souvenirs == [resultat.enregistrement]
    assert souvenirs[0].hypotheses_refutees


def test_anti_triche_schema_sans_ecart(tmp_path):
    donnees = charger("contexte_p1.json").model_dump()
    donnees["schema"]["observe"] = list(donnees["schema"]["attendu"])
    donnees["commits_recents"] = []
    resultat = enqueter(donnees, MemoireJSON(tmp_path / "memoire.json"), LLMFaux(), AbstentionSeuil(), mode_preuves="outils")
    assert resultat.correctif is None
    assert resultat.enregistrement is None


@pytest.mark.parametrize("nom", ["contexte_p2.json", "contexte_p3.json"])
def test_autres_contextes(tmp_path, nom):
    resultat = enqueter(charger(nom), MemoireJSON(tmp_path / "memoire.json"), LLMFaux(), AbstentionSeuil(), mode_preuves="outils")
    valider_evenements(resultat)


def test_evenements_jsonl(tmp_path, enquete_initiale):
    resultat = enquete_initiale[2]
    chemin = tmp_path / "data" / "evenements.jsonl"
    ecrire_evenements_jsonl(resultat.evenements, chemin)
    evenements = [EvenementEnquete.model_validate_json(ligne) for ligne in chemin.read_text(encoding="utf-8").splitlines()]
    assert evenements == resultat.evenements


def test_memoire_filtre_candidats(tmp_path):
    contexte = charger("contexte_p1.json")
    memoire = MemoireJSON(tmp_path / "memoire.json")
    premier = enqueter(contexte, memoire, LLMFaux(), AbstentionSeuil(), mode_preuves="outils")
    refutees = premier.enregistrement.hypotheses_refutees
    second = enqueter(contexte, memoire, LLMFaux(), AbstentionSeuil(), mode_preuves="outils")
    assert not any(hypothese.texte in refutees for hypothese in second.hypotheses)
    assert sum(evenement.type == "rejet" and "Deja refutee" in evenement.texte for evenement in second.evenements) == len(refutees)


def test_resultat_action_echoue(tmp_path):
    resultat = enqueter(charger("contexte_p1.json"), MemoireJSON(tmp_path / "memoire.json"),
                        LLMFaux(), AbstentionSeuil(), resultat="echoue", mode_preuves="outils")
    assert resultat.enregistrement.resultat == "echoue"


def test_noms_dynamiques(tmp_path):
    donnees = charger("contexte_p1.json").model_dump()
    donnees.update(signature="mismatch:autre_composant", logs=[], commits_recents=[])
    donnees["schema"] = {"attendu": ["ancien:float"], "observe": ["nouveau:float"]}
    resultat = enqueter(donnees, MemoireJSON(tmp_path / "memoire.json"), LLMFaux(), AbstentionSeuil(), mode_preuves="outils")
    assert resultat.correctif is not None
    patch = json.loads(resultat.correctif.patch)
    assert patch["apres"] == ["ancien:float"]
    assert patch["avant"] == ["nouveau:float"]
    assert resultat.correctif.elements_touches == ["autre_composant"]


def test_sources_distinctes_et_injection(tmp_path):
    class AbstentionEspion(AbstentionSeuil):
        def __init__(self):
            super().__init__()
            self.appels = []

        def decider(self, hypothese, nb_sources, incident_similaire):
            self.appels.append((nb_sources, incident_similaire))
            return super().decider(hypothese, nb_sources, incident_similaire)

    abstention = AbstentionEspion()
    memoire = MemoireJSON(tmp_path / "memoire.json")
    contexte = charger("contexte_p1.json")
    enqueter(contexte, memoire, LLMFaux(), abstention, mode_preuves="outils")
    enqueter(contexte, memoire, LLMFaux(), abstention, mode_preuves="outils")
    assert abstention.appels == [(3, False), (3, True)]


def test_tous_candidats_refutes(tmp_path):
    memoire = MemoireJSON(tmp_path / "memoire.json")
    contexte = charger("contexte_p1.json")
    candidats = json.loads(LLMFaux().completer(""))
    memoire.ecrire(EnregistrementMemoire(
        signature=contexte.signature, cause_confirmee="Autre cause",
        hypotheses_refutees=[candidat["texte"] for candidat in candidats],
        correctif_essaye="Autre correction", resultat="reussi", date="2026-10-15",
    ))
    resultat = enqueter(contexte, memoire, LLMFaux(), AbstentionSeuil(), mode_preuves="outils")
    assert resultat.hypotheses == []
    assert resultat.decision_abstention.abstient and resultat.decision_abstention.manque
    assert resultat.correctif is None and resultat.enregistrement is None
    assert len(memoire.chercher(contexte.signature)) == 1
    valider_evenements(resultat)


def test_aucune_valeur_scenario_dans_source():
    racine = Path(__file__).resolve().parents[1] / "src"
    interdits = ("amount", "total", "created_at", "P1", "P2", "P3",
                 "schema_mismatch:orders_etl", "latency_anomaly:orders_etl")
    for chemin in racine.rglob("*.py"):
        contenu = chemin.read_text(encoding="utf-8")
        for noeud in ast.walk(ast.parse(contenu)):
            if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
                for interdit in interdits:
                    assert interdit not in noeud.value, (chemin, interdit)