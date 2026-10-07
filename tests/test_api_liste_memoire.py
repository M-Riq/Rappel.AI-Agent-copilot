from datetime import datetime, timedelta
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from src.rappel import api
from src.rappel.contrats import EnregistrementMemoire
from src.rappel.interfaces.abstention import AbstentionSeuil
from src.rappel.interfaces.llm import LLMFaux
from src.rappel.interfaces.memoire import MemoireJSON
from src.rappel.interfaces.traceur import TraceurNul


def charger(nom="contexte_p1.json"):
    return json.loads((Path(__file__).resolve().parents[1] / "fixtures" / nom).read_text(encoding="utf-8"))


@pytest.fixture
def client(tmp_path):
    memoire = MemoireJSON(tmp_path / "memoire.json")
    app = api.creer_app(memoire, LLMFaux(), AbstentionSeuil(),
                        executeur_enquete=lambda tache: tache(), traceur=TraceurNul())
    with TestClient(app) as connexion:
        yield connexion, memoire


def test_liste_vide_et_resumes_recents(client):
    connexion, _ = client
    assert connexion.get("/incidents").json() == []
    premier, ambigu = charger(), charger("contexte_p3.json")
    connexion.post("/incidents", json=premier)
    connexion.post("/incidents", json=ambigu)
    resumes = connexion.get("/incidents").json()
    assert [element["incident_id"] for element in resumes] == [ambigu["incident_id"], premier["incident_id"]]
    champs = {"incident_id", "signature", "statut", "cree_le", "nb_evenements", "a_correctif",
              "abstention", "decision_humaine", "resultat_action"}
    for element in resumes:
        assert set(element) == champs
        assert datetime.fromisoformat(element["cree_le"]).utcoffset() == timedelta(0)
        assert element["statut"] == "termine"
        assert element["nb_evenements"] > 0
        assert element["decision_humaine"] is None and element["resultat_action"] is None
    assert resumes[0]["abstention"] and not resumes[0]["a_correctif"]
    assert resumes[1]["a_correctif"] and not resumes[1]["abstention"]
    assert connexion.get("/incidents", params={"statut": "termine"}).json() == resumes
    assert connexion.get("/incidents", params={"statut": "erreur"}).json() == []
    assert connexion.get("/incidents", params={"limite": 1}).json() == resumes[:1]


@pytest.mark.parametrize("parametres", [{"limite": 0}, {"limite": 201}, {"limite": "abc"}, {"statut": "autre"}])
def test_filtres_invalides(client, parametres):
    assert client[0].get("/incidents", params=parametres).status_code == 422


def test_resume_decision_et_action(client):
    connexion, _ = client
    contexte = charger()
    connexion.post("/incidents", json=contexte)
    avant = connexion.get("/incidents").json()[0]
    connexion.post(f"/correctifs/{contexte['incident_id']}/decision", json={"decision": "refuser"})
    connexion.post(f"/incidents/{contexte['incident_id']}/resultat-action", json={"resultat": "echoue"})
    apres = connexion.get("/incidents").json()[0]
    assert apres["decision_humaine"] == "refuser" and apres["resultat_action"] == "echoue"
    assert apres["nb_evenements"] == avant["nb_evenements"] + 2
    assert apres["cree_le"] == avant["cree_le"]


def test_memoire_apres_incident_et_filtre(client):
    connexion, _ = client
    assert connexion.get("/memoire").json() == {"souvenirs": [], "refutations_par_signature": {}}
    contexte = charger()
    connexion.post("/incidents", json=contexte)
    contenu = connexion.get("/memoire").json()
    souvenir = EnregistrementMemoire.model_validate(contenu["souvenirs"][0])
    assert len(contenu["souvenirs"]) == 1
    assert contenu["refutations_par_signature"] == {contexte["signature"]: souvenir.hypotheses_refutees}
    assert connexion.get("/memoire", params={"signature": contexte["signature"]}).json() == contenu
    assert connexion.get("/memoire", params={"signature": "inconnue"}).json() == {"souvenirs": [], "refutations_par_signature": {}}


def test_refutations_ordre_et_dedoublonnage(client):
    connexion, memoire = client
    for signature, textes in [("sig", ["b", "a", "b"]), ("autre", ["z"]), ("sig", ["a", "c"])]:
        memoire.ecrire(EnregistrementMemoire(signature=signature, cause_confirmee="cause",
            hypotheses_refutees=textes, correctif_essaye="correction", resultat="reussi", date="2026-10-07"))
    contenu = connexion.get("/memoire").json()
    assert [element["signature"] for element in contenu["souvenirs"]] == ["sig", "autre", "sig"]
    assert contenu["refutations_par_signature"] == {"sig": ["b", "a", "c"], "autre": ["z"]}


@pytest.mark.parametrize("corps", [{}, {"confirmer": False}, {"confirmer": "true"}, {"confirmer": 1}])
def test_confirmation_obligatoire_sans_effacement(client, corps):
    connexion, memoire = client
    connexion.post("/incidents", json=charger())
    assert connexion.post("/memoire/reinitialiser", json=corps).status_code == 422
    assert len(memoire.lister()) == 1 and len(connexion.get("/incidents").json()) == 1


@pytest.mark.parametrize("effacer_incidents", [False, True])
def test_reinitialisation_confirmee(client, effacer_incidents):
    connexion, memoire = client
    connexion.post("/incidents", json=charger())
    reponse = connexion.post("/memoire/reinitialiser", json={"confirmer": True, "incidents": effacer_incidents})
    assert reponse.status_code == 200
    assert reponse.json() == {"souvenirs_supprimes": 1, "incidents_supprimes": int(effacer_incidents)}
    assert memoire.lister() == []
    assert len(connexion.get("/incidents").json()) == (0 if effacer_incidents else 1)


def test_suspendu_refuse_reset_et_resume_en_cours(tmp_path):
    taches = []
    memoire = MemoireJSON(tmp_path / "memoire.json")
    app = api.creer_app(memoire, LLMFaux(), AbstentionSeuil(), executeur_enquete=taches.append, traceur=TraceurNul())
    with TestClient(app) as connexion:
        connexion.post("/incidents", json=charger())
        resume = connexion.get("/incidents", params={"statut": "en_cours"}).json()[0]
        assert resume["abstention"] is None and not resume["a_correctif"]
        assert connexion.post("/memoire/reinitialiser", json={"confirmer": True, "incidents": True}).status_code == 409
        assert len(connexion.get("/incidents").json()) == 1
        taches[0]()
        assert connexion.get("/incidents", params={"statut": "en_cours"}).json() == []


def test_resume_erreur_sans_detail(client, monkeypatch):
    def echouer(*args, **kwargs):
        raise RuntimeError("secret-factice")
    monkeypatch.setattr(api, "enqueter", echouer)
    connexion, _ = client
    connexion.post("/incidents", json=charger())
    resumes = connexion.get("/incidents", params={"statut": "erreur"}).json()
    assert len(resumes) == 1 and resumes[0]["abstention"] is None
    assert "secret-factice" not in str(resumes)


def test_nouvelles_routes_openapi(client):
    chemins = client[0].get("/openapi.json").json()["paths"]
    assert "get" in chemins["/incidents"]
    assert "get" in chemins["/memoire"]
    assert "post" in chemins["/memoire/reinitialiser"]