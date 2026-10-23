import json
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient
import pytest

from rappel import api
from rappel.contrats import valider
from rappel.enquete.adaptateurs import LLMFaux, MemoireEnquete
from rappel.interfaces.abstention import AbstentionSeuil


RACINE = Path(__file__).resolve().parents[1]


def charger(nom="p1"):
    return json.loads((RACINE / "fixtures" / f"contexte_{nom}.json").read_text(encoding="utf-8"))


def immediat(tache):
    tache()


@pytest.fixture
def memoire(tmp_path):
    return MemoireEnquete.json(tmp_path / "memoire.json")


@pytest.fixture
def client(memoire):
    application = api.creer_app(memoire, executeur_enquete=immediat)
    with TestClient(application) as connexion:
        yield connexion


def test_creation_lance_l_enquete_et_resultat(client):
    reponse = client.post("/incidents", json=charger())
    assert reponse.status_code == 201 and reponse.json() == {"id": "p1"}
    resultat = client.get("/incidents/p1").json()
    assert resultat["statut"] == "termine"
    assert resultat["correctif"] is not None and not resultat["decision_abstention"]["abstient"]
    assert resultat["mode_preuves"] == "sql" and resultat["source_preuves"] == "simule-sqlite"
    assert resultat["preuves_simulees"] is True and resultat["enquete_simulee"] is True
    assert resultat["metriques"]["nb_tours"] == 5 and resultat["metriques"]["nb_requetes_sql"] == 5
    for hypothese in resultat["hypotheses"]:
        valider("hypothese", hypothese)
    valider("correctif_propose", resultat["correctif"])
    assert client.get("/sante").json() == client.get("/healthz").json()


def test_fil_en_direct_conforme_et_filtre(client):
    client.post("/incidents", json=charger())
    fil = client.get("/incidents/p1/enquete/evenements").json()
    assert [evenement["etape"] for evenement in fil] == list(range(1, len(fil) + 1))
    for evenement in fil:
        valider("evenement_enquete", evenement)
    assert any(evenement["type"] == "preuve" and evenement["requete_sql"] for evenement in fil)
    assert client.get("/incidents/p1/enquete/evenements", params={"apres": 3}).json() == fil[3:]
    assert client.get("/incidents/p1/enquete/evenements", params={"apres": -1}).status_code == 422
    assert client.get("/incidents/inconnu/enquete/evenements").status_code == 404


def test_vue_squelette_inchangee_en_mode_fake(client):
    # La fixture P1 reste servie en mode fake ; P3 n'hérite jamais du diagnostic de P1.
    client.post("/incidents", json=charger())
    client.post("/incidents", json=charger("p3"))
    assert len(client.get("/incidents/p1/evenements").json()) == 8
    assert client.get("/incidents/p3/evenements").json() == []
    assert client.get("/incidents/p3").json()["decision_abstention"]["abstient"]


def test_hors_mode_fake_evenements_reels_et_fixture_refusee(memoire, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "meterless")
    application = api.creer_app(memoire, LLMFaux(), executeur_enquete=immediat)
    with TestClient(application) as client:
        assert client.get("/incidents/p1/evenements").status_code == 501
        client.post("/incidents", json=charger("p3"))
        reels = client.get("/incidents/p3/evenements").json()
        assert reels == client.get("/incidents/p3/enquete/evenements").json()
        assert reels and reels[0]["incident_id"] == "p3"
        assert client.get("/incidents/p3").json()["enquete_simulee"] is False
        assert client.post("/correctifs/p1/decision", json={"decision": "appliquer"}).status_code == 404


def test_validation_doublon_et_inconnu(client):
    assert client.post("/incidents", json={"incident_id": "incomplet"}).status_code == 422
    assert client.post("/incidents", json=charger()).status_code == 201
    assert client.post("/incidents", json=charger()).status_code == 409
    assert client.get("/incidents/inconnu").status_code == 404
    assert client.post("/incidents/inconnu/resultat-action", json={"resultat": "echoue"}).status_code == 404


def test_decision_sur_le_correctif_du_moteur(client):
    client.post("/incidents", json=charger())
    for decision in ("appliquer", "refuser"):
        reponse = client.post("/correctifs/p1/decision", json={"decision": decision})
        assert reponse.status_code == 200 and reponse.json()["execute"] is False
    assert client.get("/incidents/p1").json()["decision_humaine"] == "refuser"
    fil = client.get("/incidents/p1/enquete/evenements").json()
    assert "aucun patch exécuté" in fil[-1]["texte"] and fil[-1]["etape"] == len(fil)


def test_decision_refusee_si_abstention(client):
    client.post("/incidents", json=charger("p3"))
    assert client.post("/correctifs/p3/decision", json={"decision": "appliquer"}).status_code == 409


def test_resultat_action_met_a_jour_la_memoire(client, memoire):
    contexte = charger()
    client.post("/incidents", json=contexte)
    reponse = client.post("/incidents/p1/resultat-action", json={"resultat": "echoue"})
    assert reponse.status_code == 202 and reponse.json()["memoire_mise_a_jour"]
    assert memoire.chercher(contexte["signature"])[-1].resultat == "echoue"
    assert client.post("/incidents/p1/resultat-action", json={"resultat": "autre"}).status_code == 422


def test_liste_et_memoire(client):
    client.post("/incidents", json=charger())
    client.post("/incidents", json=charger("p3"))
    resumes = client.get("/incidents").json()
    assert [element["incident_id"] for element in resumes] == ["p3", "p1"]
    assert resumes[0]["abstention"] and not resumes[0]["a_correctif"]
    assert resumes[1]["a_correctif"] and not resumes[1]["abstention"]
    assert client.get("/incidents", params={"limite": 1}).json() == resumes[:1]
    assert client.get("/incidents", params={"statut": "erreur"}).json() == []
    assert client.get("/incidents", params={"limite": 0}).status_code == 422
    lecture = client.get("/memoire").json()
    assert len(lecture["souvenirs"]) == 1
    assert lecture["refutations_par_signature"][charger()["signature"]]
    assert client.get("/memoire", params={"signature": "inconnue"}).json()["souvenirs"] == []


@pytest.mark.parametrize("corps", [{"confirmer": False}, {"confirmer": "true"}, {}, {"confirmer": True, "autre": 1}])
def test_reinitialisation_confirmation_stricte(client, corps):
    assert client.post("/memoire/reinitialiser", json=corps).status_code == 422


def test_reinitialisation_memoire_et_incidents(client):
    client.post("/incidents", json=charger())
    assert client.post("/memoire/reinitialiser", json={"confirmer": True}).json() == {
        "souvenirs_supprimes": 1, "incidents_supprimes": 0}
    assert client.post("/memoire/reinitialiser", json={"confirmer": True, "incidents": True}).json() == {
        "souvenirs_supprimes": 0, "incidents_supprimes": 1}
    assert client.get("/incidents").json() == []


def test_erreur_sans_fuite(memoire, monkeypatch):
    import rappel.enquete as enquete

    def echouer(*args, **kwargs):
        raise RuntimeError("cle-factice-ne-jamais-exposer")

    monkeypatch.setattr(enquete.graphe, "enqueter", echouer)
    application = api.creer_app(memoire, executeur_enquete=immediat)
    with TestClient(application) as client:
        client.post("/incidents", json=charger())
        reponse = client.get("/incidents/p1")
    assert reponse.json()["statut"] == "erreur" and reponse.json()["message"]
    assert "cle-factice" not in reponse.text


def test_thread_evenements_en_direct_et_reinitialisation_bloquee(memoire):
    pret, continuer = Event(), Event()

    class LLMPause(LLMFaux):
        def completer(self, prompt, role="detective", tools=None):
            pret.set()
            if not continuer.wait(5):
                raise RuntimeError("Test bloque")
            return super().completer(prompt, role, tools)

    application = api.creer_app(memoire, LLMPause(), AbstentionSeuil())
    with TestClient(application) as client:
        try:
            assert client.post("/incidents", json=charger()).status_code == 201
            assert pret.wait(5)
            assert client.get("/incidents/p1").json()["statut"] == "en_cours"
            assert [element["etape"] for element in client.get("/incidents/p1/enquete/evenements").json()] == [1, 2]
            assert client.post("/memoire/reinitialiser", json={"confirmer": True}).status_code == 409
            assert client.post("/correctifs/p1/decision", json={"decision": "appliquer"}).status_code == 200
        finally:
            continuer.set()
    # La sortie du client attend la fin de l'enquête lancée dans le thread.
    assert application is not None


def test_moteur_absent_message_clair(memoire, monkeypatch):
    import builtins

    original = builtins.__import__

    def importer(nom, *args, **kwargs):
        if nom == "rappel.enquete" and args and args[2] and "enqueter" in args[2]:
            raise ImportError("langgraph absent")
        return original(nom, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importer)
    application = api.creer_app(memoire, executeur_enquete=immediat)
    with TestClient(application) as client:
        client.post("/incidents", json=charger())
        reponse = client.get("/incidents/p1").json()
    assert reponse["statut"] == "erreur" and "langgraph" in reponse["message"]


def test_api_globale_partage_l_etat_du_squelette():
    assert api.app is not None and isinstance(api._INCIDENTS, dict) and isinstance(api._DECISIONS, dict)
