from pathlib import Path

import pytest

from rappel.contrats import valider
from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import LLMEnquete, MemoireEnquete
from rappel.enquete.modeles import ContexteIncident
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.interfaces.llm import FakeLLM


RACINE = Path(__file__).resolve().parents[1]


def charger(nom):
    chemin = RACINE / "fixtures" / f"contexte_{nom}.json"
    contexte = ContexteIncident.model_validate_json(chemin.read_text(encoding="utf-8"))
    valider("contexte_incident", contexte.model_dump(mode="json", exclude_none=True))
    return contexte


def json_contrat(objet):
    return objet.model_dump(mode="json", exclude_none=True)


@pytest.fixture(scope="module")
def resultats(tmp_path_factory):
    dossier = tmp_path_factory.mktemp("memoire")
    partagee = MemoireEnquete.json(dossier / "partagee.json")
    vide = MemoireEnquete.json(dossier / "vide.json")
    sorties = {}
    for nom, memoire in (("p1", partagee), ("p2", partagee), ("p3", vide)):
        # FakeLLM du squelette directement, via l'adaptateur du moteur.
        sorties[nom] = (enqueter(charger(nom), memoire, LLMEnquete(FakeLLM()), AbstentionSeuil(),
                                 mode_preuves="sql"), memoire)
    return sorties


@pytest.mark.parametrize("nom", ["p1", "p2", "p3"])
def test_evenements_conformes(resultats, nom):
    resultat, _ = resultats[nom]
    assert resultat.evenements
    for evenement in resultat.evenements:
        valider("evenement_enquete", json_contrat(evenement))
    assert any(evenement.requete_sql for evenement in resultat.evenements if evenement.type == "preuve")
    assert {evenement.modele for evenement in resultat.evenements} - {None} <= {"faux-rapide", "faux-profond"}


@pytest.mark.parametrize("nom", ["p1", "p2", "p3"])
def test_hypotheses_conformes(resultats, nom):
    resultat, _ = resultats[nom]
    assert resultat.hypotheses
    for hypothese in resultat.hypotheses:
        valider("hypothese", json_contrat(hypothese))


@pytest.mark.parametrize("nom", ["p1", "p2", "p3"])
def test_correctifs_et_enregistrements_conformes(resultats, nom):
    resultat, memoire = resultats[nom]
    if nom == "p3":
        assert resultat.correctif is None and resultat.enregistrement is None
        assert resultat.decision_abstention.abstient
        return
    valider("correctif_propose", json_contrat(resultat.correctif))
    valider("enregistrement_memoire", json_contrat(resultat.enregistrement))
    # Ce qui est relu par le backend JSON du squelette est aussi conforme.
    for enregistrement in memoire.backend.chercher(resultat.enregistrement.signature):
        valider("enregistrement_memoire", enregistrement)


def test_hypotheses_ecrites_conformes(resultats):
    _, memoire = resultats["p2"]
    lignes = memoire.lister_hypotheses()
    assert {ligne["incident_id"] for ligne in lignes} == {"p1", "p2"}
    for ligne in lignes:
        valider("hypothese", {cle: ligne[cle] for cle in
                              ("id", "incident_id", "texte", "statut", "preuves", "confiance", "raison_rejet")})
