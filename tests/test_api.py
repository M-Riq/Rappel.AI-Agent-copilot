import json
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient
import pytest

from src.rappel import api
from src.rappel.contrats import EvenementEnquete
from src.rappel.interfaces.abstention import AbstentionSeuil
from src.rappel.interfaces.llm import LLMFaux
from src.rappel.interfaces.memoire import MemoireJSON


def charger(nom="contexte_p1.json"):
    chemin = Path(__file__).resolve().parents[1] / "fixtures" / nom
    return json.loads(chemin.read_text(encoding="utf-8"))


@pytest.fixture
def client(tmp_path):
    memoire = MemoireJSON(tmp_path / "memoire.json")
    application = api.creer_app(memoire, LLMFaux(), AbstentionSeuil(), executeur_enquete=lambda tache: tache())
    with TestClient(application) as connexion:
        yield connexion, memoire


def test_creer_et_lire_incident(client):
    connexion, _ = client
    contexte = charger()
    reponse = connexion.post("/incidents", json=contexte)
    assert reponse.status_code == 202
    assert reponse.json() == {"incident_id": contexte["incident_id"], "statut": "en_cours"}
    resultat = connexion.get(f"/incidents/{contexte['incident_id']}").json()
    assert resultat["statut"] == "termine"
    assert resultat["correctif"] is not None
    assert resultat["hypotheses"] and resultat["metriques"]["nb_evenements"] > 0
    assert not resultat["decision_abstention"]["abstient"]
    assert connexion.get("/sante").json() == {"statut": "ok"}


def test_evenements_et_filtre(client):
    connexion, _ = client
    contexte = charger()
    connexion.post("/incidents", json=contexte)
    url = f"/incidents/{contexte['incident_id']}/evenements"
    evenements = [EvenementEnquete.model_validate(element) for element in connexion.get(url).json()]
    assert [element.etape for element in evenements] == list(range(1, len(evenements) + 1))
    assert connexion.get(url, params={"apres": 3}).json() == [element.model_dump() for element in evenements if element.etape > 3]
    assert connexion.get(url, params={"apres": -1}).status_code == 422


def test_conflits_validation_et_inconnu(client):
    connexion, _ = client
    contexte = charger()
    connexion.post("/incidents", json=contexte)
    assert connexion.post("/incidents", json=contexte).status_code == 409
    assert connexion.post("/incidents", json={"incident_id": "invalide"}).status_code == 422
    assert connexion.get("/incidents/inconnu").status_code == 404
    assert connexion.get("/incidents/inconnu/evenements").status_code == 404
    assert connexion.post("/incidents/inconnu/resultat-action", json={"resultat": "echoue"}).status_code == 404


def test_incident_ambigu_sans_correctif(client):
    connexion, _ = client
    contexte = charger("contexte_p3.json")
    connexion.post("/incidents", json=contexte)
    url = f"/incidents/{contexte['incident_id']}"
    resultat = connexion.get(url).json()
    assert resultat["statut"] == "termine"
    assert resultat["correctif"] is None
    assert resultat["decision_abstention"]["abstient"]
    assert connexion.post(f"/correctifs/{contexte['incident_id']}/decision", json={"decision": "appliquer"}).status_code == 409


def test_decision_humaine(client):
    connexion, _ = client
    contexte = charger()
    connexion.post("/incidents", json=contexte)
    url = f"/correctifs/{contexte['incident_id']}/decision"
    assert connexion.post(url, json={"decision": "appliquer"}).status_code == 202
    assert connexion.post(url, json={"decision": "refuser"}).status_code == 409
    evenements = connexion.get(f"/incidents/{contexte['incident_id']}/evenements").json()
    assert evenements[-1]["type"] == "decision" and evenements[-1]["acteur"] == "systeme"
    assert "appliquer" in evenements[-1]["texte"]
    assert evenements[-1]["etape"] == len(evenements)
    assert connexion.get(f"/incidents/{contexte['incident_id']}").json()["decision_humaine"] == "appliquer"


def test_resultat_action_met_a_jour_memoire(client):
    connexion, memoire = client
    contexte = charger()
    connexion.post("/incidents", json=contexte)
    reponse = connexion.post(f"/incidents/{contexte['incident_id']}/resultat-action", json={"resultat": "echoue"})
    assert reponse.status_code == 202
    assert reponse.json()["memoire_mise_a_jour"]
    assert memoire.chercher(contexte["signature"])[-1].resultat == "echoue"


def test_corps_action_invalides_et_correctif_inconnu(client):
    connexion, _ = client
    contexte = charger()
    connexion.post("/incidents", json=contexte)
    decision_url = f"/correctifs/{contexte['incident_id']}/decision"
    resultat_url = f"/incidents/{contexte['incident_id']}/resultat-action"
    assert connexion.post(decision_url, json={"decision": "autre"}).status_code == 422
    assert connexion.post(resultat_url, json={"resultat": "autre"}).status_code == 422
    assert connexion.post("/correctifs/inconnu/decision", json={"decision": "refuser"}).status_code == 404


def test_resultat_action_sans_souvenir(client):
    connexion, _ = client
    contexte = charger("contexte_p3.json")
    connexion.post("/incidents", json=contexte)
    reponse = connexion.post(f"/incidents/{contexte['incident_id']}/resultat-action", json={"resultat": "echoue"})
    assert reponse.status_code == 202
    assert not reponse.json()["memoire_mise_a_jour"]


def test_documentation_openapi(client):
    connexion, _ = client
    schema = connexion.get("/openapi.json")
    assert schema.status_code == 200
    assert "/incidents" in schema.json()["paths"]
    assert "schema" in schema.json()["components"]["schemas"]["ContexteIncident"]["properties"]
    assert connexion.get("/docs").status_code == 200


def test_erreur_enquete_sans_fuite(client, monkeypatch):
    def echouer(*args, **kwargs):
        raise RuntimeError("cle-factice-ne-jamais-exposer")
    monkeypatch.setattr(api, "enqueter", echouer)
    connexion, _ = client
    contexte = charger()
    assert connexion.post("/incidents", json=contexte).status_code == 202
    reponse = connexion.get(f"/incidents/{contexte['incident_id']}")
    assert reponse.json()["statut"] == "erreur"
    assert "cle-factice" not in reponse.text
    assert reponse.json()["message"]


def test_evenements_progressifs_pendant_thread(tmp_path):
    pret = Event()
    continuer = Event()
    class LLMPause(LLMFaux):
        def completer(self, prompt, role="detective"):
            pret.set()
            if not continuer.wait(5):
                raise RuntimeError("Test bloque")
            return super().completer(prompt, role)
    application = api.creer_app(MemoireJSON(tmp_path / "memoire.json"), LLMPause(), AbstentionSeuil())
    with TestClient(application) as connexion:
        contexte = charger()
        try:
            assert connexion.post("/incidents", json=contexte).status_code == 202
            assert pret.wait(5)
            assert connexion.get(f"/incidents/{contexte['incident_id']}").json()["statut"] == "en_cours"
            evenements = connexion.get(f"/incidents/{contexte['incident_id']}/evenements").json()
            assert len(evenements) == 2
            assert [element["etape"] for element in evenements] == [1, 2]
        finally:
            continuer.set()


@pytest.mark.parametrize("decision", ["appliquer", "refuser"])
def test_point_extension_action(tmp_path, decision):
    appels = []
    application = api.creer_app(
        MemoireJSON(tmp_path / "memoire.json"), LLMFaux(), AbstentionSeuil(),
        executeur_enquete=lambda tache: tache(),
        executeur_action=lambda incident_id, correctif: appels.append((incident_id, correctif)),
    )
    with TestClient(application) as connexion:
        contexte = charger()
        connexion.post("/incidents", json=contexte)
        assert connexion.post(f"/correctifs/{contexte['incident_id']}/decision", json={"decision": decision}).status_code == 202
        assert len(appels) == (1 if decision == "appliquer" else 0)