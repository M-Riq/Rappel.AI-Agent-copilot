"""Interface et parcours (R6) sans Streamlit installé : contrôles statiques, logique pure et
remplaçant de Streamlit sur les cas limites (API coupée, 4xx/5xx, événement inconnu, texte très
long, caractères spéciaux, enquête sans événement, incident déjà fini, trois rejeux de suite)."""

import importlib
import importlib.util
import json
from pathlib import Path
import py_compile
import sys
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
import requests

from rappel import api
from rappel.enquete.adaptateurs import MemoireEnquete

RACINE = Path(__file__).resolve().parents[1]
UI = RACINE / "ui"
FICHIERS_UI = sorted([*UI.glob("*.py"), *UI.glob("pages/*.py")])


def charger(nom):
    spec = importlib.util.spec_from_file_location(f"rappel_ui_parcours_{nom}", UI / f"{nom}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


logique = charger("logique")
client_api = charger("client_api")


@pytest.mark.parametrize("fichier", FICHIERS_UI, ids=lambda chemin: chemin.name)
def test_compilation_et_aucun_html_brut(fichier, tmp_path):
    py_compile.compile(str(fichier), cfile=str(tmp_path / "x.pyc"), doraise=True)
    assert "unsafe_allow_html" not in fichier.read_text(encoding="utf-8")


def reponse(statut, corps=None, illisible=False):
    resultat = Mock(status_code=statut)
    resultat.json.side_effect = ValueError("illisible") if illisible else None
    if not illisible:
        resultat.json.return_value = corps
    return resultat


@pytest.mark.parametrize("statut, corps, attendu", [
    (404, {"detail": "Incident inconnu."}, "Erreur API 404 : Incident inconnu."),
    (422, {"detail": [{"loc": ["x"]}]}, "Erreur API 422 : requête refusée"),
    (500, ["inattendu"], "Erreur API 500 : requête refusée"),
    (503, None, "Erreur API 503 : requête refusée"),
])
def test_client_4xx_5xx(statut, corps, attendu):
    client = client_api.ClientApi("http://h", session=Mock(get=Mock(return_value=reponse(statut, corps))))
    with pytest.raises(client_api.ErreurApi) as erreur:
        client.lire_incident("p1")
    assert str(erreur.value) == attendu and erreur.value.statut == statut


def test_client_reponse_illisible_et_api_coupee():
    client = client_api.ClientApi("http://h", session=Mock(get=Mock(return_value=reponse(200, illisible=True))))
    with pytest.raises(client_api.ErreurApi, match="illisible"):
        client.sante()
    coupe = client_api.ClientApi("http://h", session=Mock(get=Mock(side_effect=requests.Timeout("lent"))))
    with pytest.raises(client_api.ErreurApi) as erreur:
        coupe.fil("p1")
    assert erreur.value.statut is None and "coupée" in str(erreur.value)


def test_evenement_inconnu_long_et_special():
    assert logique.style_evenement({"type": "xyz"}) == ("info", "Xyz")
    assert logique.style_evenement({}) == ("info", "Information")
    long = logique.texte_evenement({"texte": "a" * 100_000})
    assert len(long) < 5100 and long.endswith("caractères masqués]")
    special = "<script>alert('x')</script> **gras** ; DROP TABLE ; é ü 漢字 \u202e"
    assert logique.texte_evenement({"texte": special}) == special
    assert logique.etiquette_debat({"type": "debat", "acteur": "inconnu"}) == "Débat · inconnu"


class FauxSt:
    def __init__(self, clics=()):
        self.session_state = {}
        self.appels = []
        self.clics = list(clics)

    def fragment(self, run_every=None):
        return lambda fonction: fonction

    def button(self, libelle, key=None, disabled=False, **options):
        self.appels.append(("button", (libelle,), {"key": key, "disabled": disabled}))
        if key in self.clics and not disabled:
            self.clics.remove(key)
            if options.get("on_click"):
                options["on_click"](*options.get("args", ()))
            return True
        return False

    def selectbox(self, libelle, options, index=0, key=None, format_func=None):
        return list(options)[index]

    def columns(self, nombre):
        return [self] * nombre

    def expander(self, *args, **kwargs):
        return self

    def __getattr__(self, nom):
        return lambda *args, **kwargs: self.appels.append((nom, args, kwargs))

    def textes(self, nom):
        return [str(args[0]) for appel, args, _ in self.appels if appel == nom and args]


@pytest.fixture
def zones(monkeypatch):
    def charger_zones(faux):
        monkeypatch.setitem(sys.modules, "streamlit", faux)
        monkeypatch.syspath_prepend(str(UI))
        for nom in ("zones", "logique", "client_api"):
            monkeypatch.delitem(sys.modules, nom, raising=False)
        return importlib.import_module("zones")
    return charger_zones


@pytest.fixture
def application(tmp_path):
    return api.creer_app(MemoireEnquete.json(tmp_path / "m.json"), executeur_enquete=lambda tache: tache())


def test_neuf_enquetes_de_suite_puis_plafond(zones, application):
    faux = FauxSt(clics=["rappel_injecter"] * 10)
    faux.session_state["rappel_session"] = "test"
    module = zones(faux)
    with TestClient(application) as session:
        client = module.ClientApi("http://testserver", session=session)
        for _ in range(10):
            module.zone_pipeline(client)
        attendus = [f"ui-p1-{numero}-test" for numero in range(1, 10)]
        assert faux.session_state["rappel_incidents"] == attendus
        assert [incident["incident_id"] for incident in session.get("/incidents").json()] == attendus[::-1]
    desactives = [options["disabled"] for nom, _, options in faux.appels if nom == "button"]
    assert desactives[-1] is True
    assert any("Plafond de 9" in texte for texte in faux.textes("info"))


def test_injection_suspendue_pendant_enquete(zones, monkeypatch):
    from rappel import pipeline_demo

    faux = FauxSt(clics=["rappel_injecter"])
    faux.session_state["rappel_courant"] = "en-attente"
    faux.session_state["rappel_selection"] = "ancien-termine"
    module = zones(faux)
    client = Mock()
    client.sante.return_value = {"llm": "fake"}
    client.lire_incident.return_value = {"statut": "en_cours"}
    injecter = Mock()
    monkeypatch.setattr(pipeline_demo, "injecter", injecter)
    module.zone_pipeline(client)
    injecter.assert_not_called()
    client.creer_depuis_demo.assert_not_called()
    client.lire_incident.assert_called_once_with("en-attente")
    assert faux.session_state["rappel_rejeux"] == 0


def test_budget_reel_neuf_enquetes_exige_accord(zones):
    faux = FauxSt()
    module = zones(faux)
    client = Mock()
    client.sante.return_value = {"llm": "meterless"}
    assert module.budget_enquetes(client) == 3
    faux.checkbox = Mock(return_value=True)
    assert module.budget_enquetes(client) == 9
    client.sante.side_effect = module.ErreurApi("API coupée")
    assert module.budget_enquetes(client) is None


def test_creation_incertitude_bloque_nouvelle_injection(zones, monkeypatch):
    from rappel import pipeline_demo

    faux = FauxSt(clics=["rappel_injecter"] * 2)
    module = zones(faux)
    client = Mock()
    client.sante.return_value = {"llm": "fake"}
    client.creer_depuis_demo.side_effect = module.ErreurApi("API coupée")
    client.lire_incident.side_effect = module.ErreurApi("Incident inconnu", 404)
    injecter = Mock()
    monkeypatch.setattr(pipeline_demo, "injecter", injecter)
    module.zone_pipeline(client)
    module.zone_pipeline(client)
    injecter.assert_called_once()
    client.creer_depuis_demo.assert_called_once()
    assert faux.session_state["rappel_rejeux"] == 1


def test_enquete_sans_evenement_en_cours(zones, tmp_path):
    attente = api.creer_app(MemoireEnquete.json(tmp_path / "m.json"), executeur_enquete=lambda tache: None)
    faux = FauxSt()
    module = zones(faux)
    contexte = json.loads((RACINE / "fixtures" / "contexte_p1.json").read_text(encoding="utf-8"))
    with TestClient(attente) as session:
        session.post("/incidents", json=contexte)
        faux.session_state.update(rappel_courant="p1", rappel_evenements={"p1": []}, rappel_incidents=["p1"])
        module.zone_fil(module.ClientApi("http://testserver", session=session))
    assert "Enquête en cours…" in faux.textes("caption")
    assert not [nom for nom, _, _ in faux.appels if nom == "slider"]


def test_incident_deja_fini_et_api_coupee(zones, application):
    faux = FauxSt()
    module = zones(faux)
    contexte = json.loads((RACINE / "fixtures" / "contexte_p1.json").read_text(encoding="utf-8"))
    with TestClient(application) as session:
        session.post("/incidents", json=contexte)
        faux.session_state.update(rappel_courant="p1", rappel_evenements={"p1": []}, rappel_incidents=["p1"])
        client = module.ClientApi("http://testserver", session=session)
        module.zone_fil(client)
        module.zone_fil(client)  # second passage : aucun doublon d'événement
        module.zone_resultat(client)
    assert "Enquête en cours…" not in faux.textes("caption")
    evenements = faux.session_state["rappel_evenements"]["p1"]
    assert [e["etape"] for e in evenements] == list(range(1, len(evenements) + 1))
    assert any(t.startswith("Correctif proposé") for t in faux.textes("write"))
    coupe = module.ClientApi("http://127.0.0.1:9", session=Mock(get=Mock(side_effect=requests.ConnectionError("x"))))
    faux.appels.clear()
    module.zone_fil(coupe)
    module.zone_relations(coupe)
    assert faux.textes("warning") == [logique.MESSAGE_API_COUPEE]
    assert logique.MESSAGE_API_COUPEE in faux.textes("caption")


def incident_avec_correctif(**modifications):
    return {
        "statut": "termine", "decision_humaine": None,
        "correctif": {
            "description": "Renommer la colonne", "patch": "-amount\n+total",
            "niveau_risque": "validation", "confiance": 0.85,
            "reversible": True, "elements_touches": ["pipeline.py"],
            "justification": "Preuve q1", **modifications,
        },
    }


@pytest.mark.parametrize("decision", ["appliquer", "refuser"])
def test_panneau_action_detail_et_envoi_unique(zones, decision):
    faux = FauxSt(clics=[f"rappel_{decision}_p1"])
    module = zones(faux)
    client = Mock(url_base="http://testserver")
    client.decision.return_value = {"id": "p1", "decision": decision, "execute": False}
    incident = incident_avec_correctif()
    module.afficher_action(client, "p1", incident)
    faux.clics.append(f"rappel_{decision}_p1")
    module.afficher_action(client, "p1", incident)
    client.decision.assert_called_once_with("p1", decision)
    assert "Renommer la colonne" in faux.textes("write")
    assert "Preuve q1" in faux.textes("write")
    assert "-amount\n+total" in faux.textes("code")
    assert any(args == ("Confiance", "85%") for nom, args, _ in faux.appels if nom == "metric")
    assert "Aucun patch exécuté." in faux.textes("info")
    boutons = [options for nom, _, options in faux.appels if nom == "button"]
    assert len(boutons) == 4
    assert all(options["disabled"] for options in boutons[-2:])


@pytest.mark.parametrize("modification", [{"niveau_risque": "refus"}, {"reversible": False}])
def test_panneau_action_bloque_application(zones, modification):
    faux = FauxSt(clics=["rappel_appliquer_p1"])
    module = zones(faux)
    client = Mock(url_base="http://testserver")
    module.afficher_action(client, "p1", incident_avec_correctif(**modification))
    client.decision.assert_not_called()
    boutons = {options["key"]: options["disabled"] for nom, _, options in faux.appels if nom == "button"}
    assert boutons == {"rappel_appliquer_p1": True, "rappel_refuser_p1": False}


@pytest.mark.parametrize("resultat", [None, {}, {"id": "autre", "decision": "appliquer", "execute": False}])
def test_panneau_action_reponse_invalide_non_rejouee(zones, resultat):
    faux = FauxSt(clics=["rappel_appliquer_p1"])
    module = zones(faux)
    client = Mock(url_base="http://testserver")
    client.decision.return_value = resultat
    module.afficher_action(client, "p1", incident_avec_correctif())
    faux.clics.append("rappel_appliquer_p1")
    module.afficher_action(client, "p1", incident_avec_correctif())
    client.decision.assert_called_once()
    assert any("non confirmée" in texte for texte in faux.textes("warning"))


def test_panneau_action_timeout_et_decision_deja_enregistree(zones):
    faux = FauxSt(clics=["rappel_appliquer_p1"])
    module = zones(faux)
    client = Mock(url_base="http://testserver")
    client.decision.side_effect = module.ErreurApi("API coupée")
    module.afficher_action(client, "p1", incident_avec_correctif())
    faux.clics.append("rappel_appliquer_p1")
    module.afficher_action(client, "p1", incident_avec_correctif())
    client.decision.assert_called_once()
    assert any("non confirmée" in texte for texte in faux.textes("warning"))
    faux.session_state.clear()
    incident = {**incident_avec_correctif(), "decision_humaine": "appliquer", "resultat_action": "echoue"}
    module.afficher_action(client, "p1", incident)
    client.decision.assert_called_once()
    assert "Résultat de l'action confirmé : échoué." in faux.textes("error")


def test_panneau_action_abstention_sans_boutons(zones):
    faux = FauxSt()
    module = zones(faux)
    incident = {**incident_avec_correctif(), "decision_abstention": {"abstient": True}}
    module.afficher_action(Mock(url_base="http://testserver"), "p1", incident)
    assert not faux.appels


def test_panneau_memoire_consultation_et_souvenirs(zones):
    faux = FauxSt()
    module = zones(faux)
    faux.session_state.update(rappel_courant="p2", rappel_evenements={"p2": [
        {"acteur": "historien", "type": "preuve", "etape": 2, "texte": "incident similaire, résultat non confirmé"},
    ]})
    client = Mock()
    client.fil.return_value = []
    client.graphe.return_value = {
        "noeuds": [{"id": "signature:panne", "type": "signature"}],
        "aretes": [{"source": "incident:p2", "cible": "signature:panne", "type": "a_pour_signature"}],
    }
    client.memoire.return_value = {
        "souvenirs": [{"signature": "panne", "cause_confirmee": "colonne", "resultat": "reussi"}],
        "statuts_resultat": ["non_confirme"], "refutations_par_signature": {"panne": ["disque"]},
    }
    module.zone_memoire(client)
    client.memoire.assert_called_once_with("panne")
    assert "incident similaire, résultat non confirmé" in faux.textes("write")
    assert "disque" in faux.textes("write")
    tableaux = [args[0] for nom, args, _ in faux.appels if nom == "table"]
    assert tableaux[0][0]["résultat"] == "non confirmé"


def test_panneau_memoire_absent_ou_indisponible(zones):
    faux = FauxSt()
    module = zones(faux)
    client = Mock()
    client.fil.return_value = []
    module.zone_memoire(client)
    client.graphe.assert_not_called()
    faux.session_state["rappel_courant"] = "p1"
    client.graphe.return_value = {"noeuds": [], "aretes": []}
    module.zone_memoire(client)
    client.memoire.assert_not_called()
    client.graphe.side_effect = module.ErreurApi("API coupée")
    module.zone_memoire(client)
    assert any("Mémoire indisponible" in texte for texte in faux.textes("warning"))
