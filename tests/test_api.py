from fastapi.testclient import TestClient

from rappel import api
from rappel.contrats import valider
from rappel.interfaces.source import lire


def test_api_cycle_local():
    api._INCIDENTS.clear()
    with TestClient(api.app) as client:
        assert client.get("/healthz").json() == {
            "statut": "ok", "llm": "fake", "source": "fichier", "memoire": "json"
        }
        contexte = lire("p1")
        reponse = client.post("/incidents", json=contexte)
        assert reponse.status_code == 201
        identifiant = reponse.json()["id"]
        evenements = client.get(f"/incidents/{identifiant}/evenements")
        assert evenements.status_code == 200
        assert len(evenements.json()) >= 8
        for evenement in evenements.json():
            assert evenement["incident_id"] == identifiant
            valider("evenement_enquete", evenement)
        assert client.post("/incidents", json=contexte).status_code == 409
        assert client.post("/incidents", json={"incident_id": "incomplet"}).status_code == 422
        assert client.post(
            "/incidents", json={**contexte, "horodatage": "hier"}
        ).status_code == 422
        assert client.post(
            "/incidents", json={**contexte, "commits_recents": [
                {**contexte["commits_recents"][0], "date": "invalide"}
            ]}
        ).status_code == 422
        assert client.get("/incidents/inconnu/evenements").status_code == 404


def test_pas_de_diagnostic_p1_pour_p3_ni_en_mode_reel(monkeypatch):
    api._INCIDENTS.clear()
    with TestClient(api.app) as client:
        client.post("/incidents", json=lire("p3"))
        assert client.get("/incidents/p3/evenements").json() == []
        monkeypatch.setenv("LLM_PROVIDER", "meterless")
        assert client.get("/incidents/p1/evenements").status_code == 501


def test_decision_sans_execution():
    with TestClient(api.app) as client:
        for decision in ("appliquer", "refuser"):
            reponse = client.post("/correctifs/p1/decision", json={"decision": decision})
            assert reponse.status_code == 200
            assert reponse.json()["execute"] is False
            assert api._DECISIONS["p1"] == decision
        assert client.post("/correctifs/p1/decision", json={"decision": "executer"}).status_code == 422
        assert client.post("/correctifs/inconnu/decision", json={"decision": "appliquer"}).status_code == 404
