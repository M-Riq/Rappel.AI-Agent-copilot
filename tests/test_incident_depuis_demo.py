from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import pytest

from rappel import api
from rappel.enquete.adaptateurs import MemoireEnquete
from rappel.pipeline_demo import injecter
from scripts import incident_depuis_demo


@pytest.fixture
def client(tmp_path):
    application = api.creer_app(MemoireEnquete.json(tmp_path / "m.json"), executeur_enquete=lambda tache: tache())
    with TestClient(application) as connexion:
        yield connexion


def test_p1_depuis_la_demo_colonne_renommee(client):
    injecter("p1")
    reponse = client.post("/incidents/depuis-demo", json={"incident_id": "demo-p1"})
    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["id"] == "demo-p1" and corps["simulation"] is True and corps["source"] == "pipeline_demo"
    assert corps["signature"] == "schema_mismatch:orders_etl" and corps["statut_pipeline"] == "rouge"
    resultat = client.get("/incidents/demo-p1").json()
    assert "colonne renommée" in resultat["correctif"]["justification"]
    assert client.post("/incidents/depuis-demo", json={"incident_id": "demo-p1"}).status_code == 409


def test_sans_corps_identifiant_genere_et_contrat_inchange(client):
    injecter("p3")
    corps = client.post("/incidents/depuis-demo").json()
    assert corps["id"].startswith("demo-p3-") and corps["signature"] == "timeout:orders_etl"
    assert client.get(f"/incidents/{corps['id']}").json()["decision_abstention"]["abstient"]
    assert client.post("/incidents", json={"incident_id": "incomplet"}).status_code == 422


def test_pipeline_absent_409(client):
    assert client.post("/incidents/depuis-demo").status_code == 409


def test_script_local(capsys):
    assert incident_depuis_demo.main(["--scenario", "p1", "--local"]) == 0
    sortie = capsys.readouterr().out
    assert "SIMULATION" in sortie and "colonne renommée" in sortie


def test_script_api(monkeypatch, capsys):
    reponse = MagicMock()
    reponse.json.return_value = {"id": "demo-x"}
    poster = MagicMock(return_value=reponse)
    monkeypatch.setattr(incident_depuis_demo.requests, "post", poster)
    assert incident_depuis_demo.main(["--scenario", "p2"]) == 0
    assert poster.call_args.kwargs["json"]["signature"] == "schema_mismatch:orders_etl"
    assert "demo-x" in capsys.readouterr().out
