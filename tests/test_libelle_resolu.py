"""« Résolu par » seulement si le résultat de l'action est enregistré « reussi » et confirmé."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from rappel import api
from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import LLMFaux, MemoireEnquete
from rappel.enquete.graphe import decrire_souvenir, statut_souvenir
from rappel.enquete.modeles import ContexteIncident, EnregistrementMemoire
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.orchestration import boucle

RACINE = Path(__file__).resolve().parents[1]


def souvenir(resultat="reussi"):
    return EnregistrementMemoire(signature="sig:composant", cause_confirmee="cause", hypotheses_refutees=["h"],
                                 correctif_essaye="Correctif X", resultat=resultat, date="2026-10-24")


def contexte(nom):
    return ContexteIncident.model_validate_json((RACINE / "fixtures" / f"contexte_{nom}.json").read_text(encoding="utf-8"))


def textes_memoire(resultat):
    return " ".join(evenement.texte for evenement in resultat.evenements if "incident similaire" in evenement.texte)


@pytest.mark.parametrize("statut, attendu, interdit", [
    ("reussi", "incident similaire résolu par : Correctif X", None),
    ("echoue", "échec du correctif déjà essayé (échoué) : Correctif X", "résolu par"),
    ("non_confirme", "essayé : Correctif X (résultat non confirmé)", "résolu par"),
])
def test_trois_libelles(statut, attendu, interdit):
    texte = decrire_souvenir(souvenir("echoue" if statut == "echoue" else "reussi"), statut)
    assert attendu in texte and "2026-10-24" in texte
    assert interdit is None or interdit not in texte


def test_registre_non_confirme(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "m.json")
    element = souvenir()
    memoire.ecrire(element)
    assert statut_souvenir(memoire, element) == "reussi"
    memoire.marquer_non_confirme(element)
    assert statut_souvenir(memoire, memoire.chercher("sig:composant")[-1]) == "non_confirme"
    assert memoire.mettre_a_jour_resultat("sig:composant", "echoue")
    assert statut_souvenir(memoire, memoire.chercher("sig:composant")[-1]) == "echoue"
    memoire.marquer_non_confirme(memoire.chercher("sig:composant")[-1])
    memoire.vider()
    assert json.loads(memoire.chemin_non_confirmes.read_text(encoding="utf-8")) == []


def test_memoire_sans_registre_utilise_le_resultat_enregistre():
    assert statut_souvenir(object(), souvenir("echoue")) == "echoue"


def test_boucle_sans_action_p2_non_confirme(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "m.json")
    boucle.executer_boucle("p1", memoire=memoire)
    p2 = boucle.executer_boucle("p2", memoire=memoire)
    texte = textes_memoire(p2.resultat) + p2.resultat.correctif.justification
    assert "(résultat non confirmé)" in texte and "résolu par" not in texte


def test_boucle_avec_action_reussie_p2_resolu_par(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTION_REELLE_DEMO", "1")
    memoire = MemoireEnquete.json(tmp_path / "m.json")
    boucle.executer_boucle("p1", memoire=memoire)
    p2 = boucle.executer_boucle("p2", memoire=memoire)
    assert "incident similaire résolu par :" in textes_memoire(p2.resultat)
    assert "non confirmé" not in textes_memoire(p2.resultat)


def test_action_echouee_libelle_echoue(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "m.json")
    enqueter(contexte("p1"), memoire, LLMFaux(), AbstentionSeuil(), resultat_confirme=False)
    assert memoire.mettre_a_jour_resultat("schema_mismatch:" + "orders_etl", "echoue")
    p2 = enqueter(contexte("p2"), memoire, LLMFaux(), AbstentionSeuil())
    texte = " ".join(evenement.texte for evenement in p2.evenements)
    assert "(échoué)" in texte and "résolu par" not in texte


def test_api_non_confirme_puis_confirme(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "m.json")
    application = api.creer_app(memoire, executeur_enquete=lambda tache: tache())
    p1 = json.loads((RACINE / "fixtures" / "contexte_p1.json").read_text(encoding="utf-8"))
    with TestClient(application) as client:
        client.post("/incidents", json=p1)
        assert client.get("/memoire").json()["statuts_resultat"] == ["non_confirme"]
        client.post("/incidents", json={**p1, "incident_id": "p1-bis"})
        fil = client.get("/incidents/p1-bis/enquete/evenements").json()
        assert any("(résultat non confirmé)" in evenement["texte"] for evenement in fil)
        assert not any("résolu par" in evenement["texte"] for evenement in fil)
        assert client.post("/incidents/p1-bis/resultat-action", json={"resultat": "reussi"}).status_code == 202
        statuts = client.get("/memoire").json()["statuts_resultat"]
        assert statuts[-1] == "reussi"
        client.post("/incidents", json={**p1, "incident_id": "p1-ter"})
        fil = client.get("/incidents/p1-ter/enquete/evenements").json()
        assert any("résolu par" in evenement["texte"] for evenement in fil)
