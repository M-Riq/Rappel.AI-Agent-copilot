import json

from fastapi.testclient import TestClient
import pytest

from rappel import api
from rappel.action.executeur import (
    ECHEC_RESTAURE, EN_ATTENTE, EXECUTE, NON_APPLICABLE, REFUSE, executer_correctif, mapping_depuis_patch,
)
from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import LLMFaux, MemoireEnquete
from rappel.ingestion.contexte_demo import construire_contexte
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.pipeline_demo import empreinte_dossier, injecter, lire_etat
from rappel.pipeline_demo.pipeline import dossier_demo


def enquete(scenario, memoire):
    injecter(scenario)
    contexte, _ = construire_contexte()
    return enqueter(contexte, memoire, LLMFaux(), AbstentionSeuil())


@pytest.fixture
def memoire(tmp_path):
    return MemoireEnquete.json(tmp_path / "m.json")


def test_mapping_depuis_patch():
    patch = json.dumps({"avant": ["id:integer", "total:decimal", "n:text"], "apres": ["id:integer", "amount:decimal", "n:integer"]})
    assert mapping_depuis_patch(patch) == {"renommages": {"total": "amount"}, "conversions": {"n": "integer"}}
    assert mapping_depuis_patch("pas du json") is None
    assert mapping_depuis_patch(json.dumps({"avant": ["a:int"], "apres": ["a:int"]})) is None


def test_p1_auto_repare_et_vert(memoire):
    resultat = enquete("p1", memoire)
    assert resultat.correctif.niveau_risque == "auto"
    action = executer_correctif(resultat.correctif)
    assert action["statut"] == EXECUTE and action["pipeline"] == "vert" and not action["inchange"]
    assert lire_etat()["statut"] == "vert"
    assert (dossier_demo() / "sauvegardes" / action["sauvegarde"]).is_dir()
    assert memoire.chercher(resultat.enregistrement.signature)


def test_p2_validation_attend_puis_applique(memoire):
    enquete("p1", memoire)
    resultat = enquete("p2", memoire)
    assert resultat.correctif.niveau_risque == "validation"
    attente = executer_correctif(resultat.correctif)
    assert attente["statut"] == EN_ATTENTE and attente["inchange"] and lire_etat()["statut"] == "rouge"
    action = executer_correctif(resultat.correctif, "appliquer")
    assert action["statut"] == EXECUTE and lire_etat()["statut"] == "vert"


def test_echec_simule_restaure_a_l_identique(memoire):
    resultat = enquete("p1", memoire)
    avant = empreinte_dossier()
    action = executer_correctif(resultat.correctif, simuler_echec=True)
    assert action["statut"] == ECHEC_RESTAURE and "restauré" in action["message"]
    assert action["empreinte_apres"] == avant == empreinte_dossier()
    assert lire_etat()["statut"] == "rouge"


def test_refus_aucune_modification(memoire):
    resultat = enquete("p1", memoire)
    avant = empreinte_dossier()
    refus = executer_correctif(resultat.correctif, "refuser")
    assert refus["statut"] == REFUSE and refus["inchange"] and empreinte_dossier() == avant
    irreversible = resultat.correctif.model_copy(update={"reversible": False})
    refus = executer_correctif(irreversible, "appliquer")
    assert refus["statut"] == REFUSE and refus["niveau"] == "refus" and empreinte_dossier() == avant


def test_chemin_hors_demo_refuse(memoire, tmp_path):
    resultat = enquete("p1", memoire)
    autre = tmp_path / "ailleurs"
    autre.mkdir()
    action = executer_correctif(resultat.correctif, dossier=autre)
    assert action["statut"] == REFUSE and "pipeline de démo" in action["message"]
    assert list(autre.iterdir()) == []
    action = executer_correctif(resultat.correctif, dossier=tmp_path / "pipeline_demo" / ".." / "ailleurs")
    assert action["statut"] == REFUSE


def test_non_applicable_si_deja_vert_ou_autre_composant(memoire):
    resultat = enquete("p1", memoire)
    autre = resultat.correctif.model_copy(update={"patch": json.dumps({
        "composant": "autre_service", "avant": ["a:int"], "apres": ["b:int"]})})
    assert executer_correctif(autre)["statut"] == NON_APPLICABLE
    executer_correctif(resultat.correctif)
    assert executer_correctif(resultat.correctif)["statut"] == NON_APPLICABLE


@pytest.mark.parametrize("reelle", [False, True])
def test_api_decision_et_action_reelle_demo(memoire, monkeypatch, reelle):
    if reelle:
        monkeypatch.setenv("ACTION_REELLE_DEMO", "1")
    injecter("p1")
    contexte, _ = construire_contexte(incident_id="demo-p1")
    application = api.creer_app(memoire, executeur_enquete=lambda tache: tache())
    with TestClient(application) as client:
        assert client.post("/incidents", json=contexte).status_code == 201
        avant = empreinte_dossier()
        reponse = client.post("/correctifs/demo-p1/decision", json={"decision": "appliquer"}).json()
    assert reponse["execute"] is reelle
    if reelle:
        assert reponse["action"]["statut"] == EXECUTE and lire_etat()["statut"] == "vert"
    else:
        assert empreinte_dossier() == avant and lire_etat()["statut"] == "rouge"
