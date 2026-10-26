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
            return True
        return False

    def selectbox(self, libelle, options, index=0, key=None):
        return list(options)[index]

    def columns(self, nombre):
        return [self] * nombre

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


def test_trois_rejeux_de_suite_puis_plafond(zones, application):
    faux = FauxSt(clics=["rappel_injecter"] * 4)
    module = zones(faux)
    with TestClient(application) as session:
        client = module.ClientApi("http://testserver", session=session)
        for _ in range(4):
            module.zone_pipeline(client)
        assert faux.session_state["rappel_incidents"] == ["ui-p1-1", "ui-p1-2", "ui-p1-3"]
        assert [i["incident_id"] for i in session.get("/incidents").json()] == ["ui-p1-3", "ui-p1-2", "ui-p1-1"]
    desactives = [options["disabled"] for nom, _, options in faux.appels if nom == "button"]
    assert desactives[-1] is True
    assert any("Plafond de 3" in texte for texte in faux.textes("info"))


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
