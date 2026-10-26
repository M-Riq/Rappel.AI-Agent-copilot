"""Rejeu d'une enquête jusqu'à une étape (lecture seule, rien n'est recalculé)."""

import importlib.util
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest

from rappel import api
from rappel.enquete.adaptateurs import MemoireEnquete

RACINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rappel_ui_logique_rejeu", RACINE / "ui" / "logique.py")
logique = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logique)


@pytest.fixture
def client(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "m.json")
    application = api.creer_app(memoire, executeur_enquete=lambda tache: tache())
    with TestClient(application) as connexion:
        connexion.post("/incidents", json=json.loads((RACINE / "fixtures" / "contexte_p1.json").read_text(encoding="utf-8")))
        yield connexion, memoire


def test_fenetre_apres_jusqua(client):
    connexion, _ = client
    fil = connexion.get("/incidents/p1/enquete/evenements").json()
    assert len(fil) > 6
    assert connexion.get("/incidents/p1/enquete/evenements", params={"jusqua": 5}).json() == fil[:5]
    assert connexion.get("/incidents/p1/enquete/evenements", params={"apres": 2, "jusqua": 5}).json() == fil[2:5]
    assert connexion.get("/incidents/p1/enquete/evenements", params={"apres": 3, "jusqua": 3}).json() == []
    assert connexion.get("/incidents/p1/enquete/evenements", params={"jusqua": 10_000}).json() == fil
    assert connexion.get("/incidents/p1/enquete/evenements", params={"apres": 5, "jusqua": 2}).status_code == 422
    assert connexion.get("/incidents/p1/enquete/evenements", params={"jusqua": -1}).status_code == 422
    assert connexion.get("/incidents/inconnu/enquete/evenements", params={"jusqua": 2}).status_code == 404


def test_rejeu_en_lecture_seule(client):
    connexion, memoire = client
    avant_memoire = [souvenir.model_dump() for souvenir in memoire.lister()]
    avant_incident = connexion.get("/incidents/p1").json()
    complet = connexion.get("/incidents/p1/enquete/evenements").json()
    for etape in range(1, len(complet) + 1):
        assert connexion.get("/incidents/p1/enquete/evenements", params={"jusqua": etape}).json() == complet[:etape]
    assert [souvenir.model_dump() for souvenir in memoire.lister()] == avant_memoire
    apres_incident = connexion.get("/incidents/p1").json()
    assert {cle: valeur for cle, valeur in apres_incident.items() if cle != "metriques"} == \
        {cle: valeur for cle, valeur in avant_incident.items() if cle != "metriques"}
    assert connexion.get("/incidents/p1/enquete/evenements").json() == complet


def test_logique_curseur():
    evenements = [{"etape": 1}, {"etape": 2}, {"etape": 3}]
    assert logique.rejouer_jusqua(evenements, 2) == evenements[:2]
    assert logique.rejouer_jusqua(evenements, None) == evenements
    assert logique.rejouer_jusqua(evenements, 0) == []
    assert logique.bornes_curseur(evenements) == (1, 3)
    assert logique.bornes_curseur(evenements[:1]) is None


class FauxSt:
    def __init__(self, etape):
        self.session_state = {"rappel_courant": "p1", "rappel_evenements": {}, "rappel_incidents": ["p1"],
                              "rappel_rejeux": 0}
        self.etape = etape
        self.appels = []

    def fragment(self, run_every=None):
        return lambda fonction: fonction

    def slider(self, *args, **kwargs):
        self.appels.append(("slider", args, kwargs))
        return self.etape

    def __getattr__(self, nom):
        return lambda *args, **kwargs: self.appels.append((nom, args, kwargs))


def test_curseur_de_l_interface(client, monkeypatch):
    connexion, _ = client
    faux = FauxSt(3)
    monkeypatch.setitem(sys.modules, "streamlit", faux)
    monkeypatch.syspath_prepend(str(RACINE / "ui"))
    for nom in ("zones", "logique", "client_api"):
        monkeypatch.delitem(sys.modules, nom, raising=False)
    import zones

    zones.zone_fil(zones.ClientApi("http://testserver", session=connexion))
    curseurs = [kwargs for nom, _, kwargs in faux.appels if nom == "slider"]
    total = len(connexion.get("/incidents/p1/enquete/evenements").json())
    assert curseurs and curseurs[0]["min_value"] == 1 and curseurs[0]["max_value"] == total
    etapes = [args[0] for nom, args, _ in faux.appels if nom == "caption" and args and str(args[0]).startswith("Étape ")]
    assert len(etapes) == 3
