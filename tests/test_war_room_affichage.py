"""War room : étiquette « Débat » dans l'interface, comparaison avec/sans débat, cases W1-W3."""

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from rappel import api
from rappel.contrats import valider
from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import LLMFaux, MemoireEnquete
from rappel.enquete.modeles import ContexteIncident
from rappel.interfaces.abstention import AbstentionSeuil
from scripts import recette_module_a as recette

RACINE = Path(__file__).resolve().parents[1]
UI = RACINE / "ui"


def charger_module(nom, fichier):
    spec = importlib.util.spec_from_file_location(nom, fichier)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


logique = charger_module("rappel_ui_logique_wr", UI / "logique.py")


def contexte_p1():
    return json.loads((RACINE / "fixtures" / "contexte_p1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("evenement, attendu", [
    ({"type": "debat", "acteur": "sceptique", "texte": "{}"}, "Débat · avocat du diable (sceptique)"),
    ({"type": "debat", "acteur": "detective", "texte": "{}"}, "Débat · détective"),
    ({"type": "debat", "acteur": "historien", "texte": "Memoire"}, "Débat · historien (mémoire)"),
    ({"type": "decision", "acteur": "systeme", "texte": "War room terminee : plafond"}, "Débat · arbitre (code)"),
    ({"type": "decision", "acteur": "systeme", "texte": "Objection du tour 1 repondue"}, "Débat · arbitre (code)"),
    ({"type": "decision", "acteur": "systeme", "texte": "Confiance 0.65"}, None),
    ({"type": "preuve", "acteur": "detective", "texte": "q1"}, None),
])
def test_etiquette_debat(evenement, attendu):
    assert logique.etiquette_debat(evenement) == attendu


class FauxStreamlit:
    def __init__(self):
        self.appels = []

    def __getattr__(self, nom):
        return lambda *args, **kwargs: self.appels.append((nom, args))


def test_app_affiche_l_etiquette_du_debat(monkeypatch):
    faux = FauxStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", faux)
    monkeypatch.delitem(sys.modules, "logique", raising=False)
    app = charger_module("rappel_ui_app_wr", UI / "app.py")
    app.afficher_evenement({"etape": 3, "type": "debat", "acteur": "sceptique", "texte": "objection"})
    app.afficher_evenement({"etape": 4, "type": "preuve", "acteur": "detective", "texte": "q1",
                            "requete_sql": "SELECT 1"})
    captions = [args[0] for nom, args in faux.appels if nom == "caption"]
    assert "Débat · avocat du diable (sceptique)" in captions
    assert sum(1 for texte in captions if texte.startswith("Débat")) == 1
    assert ("code", ("SELECT 1",)) in [(nom, args[:1]) for nom, args in faux.appels]


@pytest.fixture
def client(tmp_path):
    application = api.creer_app(MemoireEnquete.json(tmp_path / "m.json"), executeur_enquete=lambda tache: tache())
    with TestClient(application) as connexion:
        yield connexion


def test_comparaison_avec_et_sans_war_room(client):
    client.post("/incidents", json=contexte_p1())
    reponse = client.get("/comparaison", params={"a": "p1", "mode": "war_room"})
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["mode"] == "war_room" and corps["simulation"] is True and corps["note"].startswith("simulé")
    assert corps["sans"]["nb_tours_war_room"] == 0 and corps["avec"]["nb_tours_war_room"] >= 1
    assert corps["difference"]["appels_llm"] == corps["avec"]["nb_appels_llm"] - corps["sans"]["nb_appels_llm"] > 0
    assert corps["preuves_inchangees"] is True
    assert corps["confiance_avant"] is not None and corps["confiance_apres"] <= corps["confiance_avant"]
    # Le rejeu n'écrit pas dans la mémoire de l'application.
    assert len(client.get("/memoire").json()["souvenirs"]) == 1


def test_comparaison_war_room_erreurs(client, monkeypatch):
    assert client.get("/comparaison", params={"a": "inconnu", "mode": "war_room"}).status_code == 404
    assert client.get("/comparaison", params={"a": "p1"}).status_code == 422
    client.post("/incidents", json=contexte_p1())
    monkeypatch.setenv("LLM_PROVIDER", "meterless")
    assert client.get("/comparaison", params={"a": "p1", "mode": "war_room"}).status_code == 409


def test_debat_sql_ne_change_pas_les_preuves_et_reste_conforme(tmp_path):
    contexte = ContexteIncident.model_validate(contexte_p1())
    sans = enqueter(contexte, MemoireEnquete.json(tmp_path / "a.json"), LLMFaux(), AbstentionSeuil())
    avec = enqueter(contexte, MemoireEnquete.json(tmp_path / "b.json"), LLMFaux(), AbstentionSeuil(),
                    war_room_active=True, war_room_max_tours=1)
    assert recette.preuves_sql(sans) == recette.preuves_sql(avec)
    assert avec.metriques.nb_tours_war_room == 1
    for evenement in avec.evenements:
        valider("evenement_enquete", evenement.model_dump(mode="json", exclude_none=True))
        if evenement.type == "preuve":
            assert evenement.requete_sql  # en mode SQL, toute preuve cite une requête exécutée


def test_cases_war_room_reussies_en_fake():
    rapport = recette.evaluer(repetitions=5)
    statuts = {case["case"]: case["statut"] for case in rapport["cases_war_room"]}
    assert statuts == {"W1": "REUSSI", "W2": "REUSSI", "W3": "REUSSI"}
    assert all(case["simule"] and case["reel"] == "NON_MESURE" for case in rapport["cases_war_room"])


def evenement(etape, type_, texte, requete_sql=None):
    return SimpleNamespace(etape=etape, type=type_, texte=texte, requete_sql=requete_sql)


def resultat(preuves, tours=1, appels=2, fin="War room terminee : plafond de tours atteint; tours=1"):
    evenements = [evenement(1, "preuve", "q1", "SELECT 1"), evenement(2, "debat", "{}")]
    if fin:
        evenements.append(evenement(3, "decision", fin))
    return SimpleNamespace(
        hypotheses=[SimpleNamespace(statut="retenue", preuves=preuves)], evenements=evenements,
        metriques=SimpleNamespace(nb_tours_war_room=tours, nb_appels_llm_war_room=appels))


def test_cases_war_room_detectent_les_ecarts():
    sans = resultat(["q1"], tours=0, appels=0, fin=None)
    statuts = lambda cases: {case["case"]: case["statut"] for case in cases}
    assert set(statuts(recette.cases_war_room(sans, resultat(["q1"]), 6, False)).values()) == {"REUSSI"}
    assert statuts(recette.cases_war_room(sans, resultat(["q1"], tours=2), 6, False))["W1"] == "ECHOUE"
    assert statuts(recette.cases_war_room(sans, resultat(["q1"], appels=7), 6, False))["W1"] == "ECHOUE"
    assert statuts(recette.cases_war_room(sans, resultat(["q1 modifiée"]), 6, False))["W2"] == "ECHOUE"
    assert statuts(recette.cases_war_room(sans, resultat(["q1"], fin=None), 6, False))["W3"] == "ECHOUE"
