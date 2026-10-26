"""Logique pure de l'interface et client API (sans Streamlit)."""

import importlib
import importlib.util
from pathlib import Path
import sys
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
import requests

from rappel import api
from rappel.enquete.adaptateurs import MemoireEnquete
from rappel.pipeline_demo import injecter

UI = Path(__file__).resolve().parents[1] / "ui"


def _charger(nom):
    spec = importlib.util.spec_from_file_location(f"rappel_ui_{nom}", UI / f"{nom}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


logique = _charger("logique")
client_api = _charger("client_api")


def test_aucun_import_streamlit_dans_la_logique():
    source = (UI / "logique.py").read_text(encoding="utf-8") + (UI / "client_api.py").read_text(encoding="utf-8")
    assert "streamlit" not in source


@pytest.mark.parametrize("type_evenement, gravite", [
    ("hypothese", "info"), ("preuve", "success"), ("rejet", "error"),
    ("debat", "warning"), ("decision", "success"), ("abstention", "warning"), ("inconnu", "info"),
])
def test_style_par_type(type_evenement, gravite):
    assert logique.style_evenement({"type": type_evenement})[0] == gravite


def test_badge_honnete():
    assert logique.badge_modele({"modele": "faux-rapide"}, True) == "simulé · faux-rapide"
    assert logique.badge_modele({"modele": "nvidia/Nemotron-super"}, True).startswith("simulé")
    assert logique.badge_modele({"modele": "nvidia/nemotron-nano"}, False) == "Nemotron via Meterless · nvidia/nemotron-nano"
    assert "fixer la voie" in logique.badge_modele({"modele": "gpt-x"}, False)
    assert logique.badge_modele({"acteur": "systeme"}, False) is None
    assert logique.badge_modele({"acteur": "detective"}, True) == "simulé"


def test_sql_sous_la_preuve():
    assert logique.sql_a_afficher({"type": "preuve", "requete_sql": " SELECT 1 LIMIT 50 "}) == "SELECT 1 LIMIT 50"
    assert logique.sql_a_afficher({"type": "preuve", "requete_sql": "  "}) is None
    assert logique.sql_a_afficher({"type": "hypothese"}) is None


def test_fusion_sans_doublon_et_apres():
    existants = [{"etape": 1, "texte": "a"}, {"etape": 2, "texte": "b"}]
    nouveaux = [{"etape": 2, "texte": "doublon"}, {"etape": 3, "texte": "c"}, "pas un dict"]
    fusion = logique.fusionner(existants, nouveaux)
    assert [e["texte"] for e in fusion] == ["a", "b", "c"]
    assert logique.derniere_etape(fusion) == 3
    assert logique.derniere_etape([]) == 0


def test_resume_et_simulation():
    assert logique.resume_incident(None) == "Aucun incident."
    assert logique.resume_incident({"statut": "en_cours"}) == "Enquête en_cours."
    abstention = {"statut": "termine", "decision_abstention": {"abstient": True, "manque": ["deuxième source"]}}
    assert "deuxième source" in logique.resume_incident(abstention)
    assert "auto" in logique.resume_incident({"statut": "termine", "correctif": {"niveau_risque": "auto"},
                                              "decision_abstention": {"abstient": False}})
    mention = logique.mention_simulation({"enquete_simulee": True, "preuves_simulees": True, "source_preuves": "simule-sqlite"})
    assert "simulé" in mention and "simule-sqlite" in mention
    assert logique.mention_simulation({"enquete_simulee": False, "preuves_simulees": False}) is None
    assert logique.enquete_terminee({"statut": "erreur"}) and not logique.enquete_terminee({"statut": "en_cours"})


def test_compteur_et_duree_indicative():
    comparaison = {
        "metriques": {
            "a": {"nb_evenements": 21, "nb_hypotheses_testees": 4, "nb_appels_llm": 5,
                  "nb_tokens_entree": None, "nb_tokens_sortie": None, "duree_ms": 30},
            "b": {"nb_evenements": 12, "nb_hypotheses_testees": 1, "nb_appels_llm": 3,
                  "nb_tokens_entree": None, "nb_tokens_sortie": None, "duree_ms": 20},
        },
        "reductions_pct": {"evenements": 42.86, "hypotheses_testees": 75.0, "appels_llm": 40.0, "tokens": None, "duree": 33.33},
        "regle_60pc": {"evenements_b_sur_a": 0.5714, "appels_b_sur_a": 0.6, "respecte": True},
        "simulation": True,
    }
    lignes = logique.lignes_compteur(comparaison)
    assert lignes[0] == {"mesure": "Événements", "a": 21, "b": 12, "reduction": "42.86 %"}
    assert lignes[3]["a"] == "inconnu" and lignes[3]["reduction"] == "inconnue"
    assert "indicative" in lignes[4]["mesure"]
    verdict = logique.verdict_regle_60pc(comparaison)
    assert "respectée" in verdict and "57%" in verdict and verdict.endswith("(simulé)")
    assert "non mesurable" in logique.verdict_regle_60pc({})


def test_plafond_de_rejeu():
    assert logique.REJEUX_MAX == 3
    assert [logique.peut_rejouer(n) for n in range(5)] == [True, True, True, False, False]
    assert logique.identifiant_incident("p2", 2) == "ui-p2-2"


def test_client_api_coupee_sans_detail():
    session = Mock()
    session.get.side_effect = requests.ConnectionError("http://jeton-secret@example.invalid")
    client = client_api.ClientApi("http://127.0.0.1:8000/", session=session)
    with pytest.raises(client_api.ErreurApi) as erreur:
        client.sante()
    assert erreur.value.statut is None
    assert "secret" not in str(erreur.value) and "example.invalid" not in str(erreur.value)
    session.get.assert_called_once_with("http://127.0.0.1:8000/healthz", timeout=5)


def test_client_api_erreur_http_assainie():
    reponse = Mock(status_code=500)
    reponse.json.return_value = {"detail": "x" * 500}
    client = client_api.ClientApi("http://h", session=Mock(get=Mock(return_value=reponse)))
    with pytest.raises(client_api.ErreurApi) as erreur:
        client.lire_incident("p1")
    assert erreur.value.statut == 500 and "x" * 50 not in str(erreur.value)


def test_client_contre_l_api_p1_p2_compteur(tmp_path):
    application = api.creer_app(MemoireEnquete.json(tmp_path / "m.json"), executeur_enquete=lambda tache: tache())
    with TestClient(application) as session:
        client = client_api.ClientApi("http://testserver", session=session)
        assert client.sante()["statut"] == "ok"
        for scenario, numero in (("p1", 1), ("p2", 2)):
            injecter(scenario)
            client.creer_depuis_demo(logique.identifiant_incident(scenario, numero))
        a, b = "ui-p1-1", "ui-p2-2"
        evenements = logique.fusionner([], client.fil(a))
        assert evenements and logique.fusionner(evenements, client.fil(a, logique.derniere_etape(evenements))) == evenements
        assert any(logique.sql_a_afficher(e) for e in evenements if e["type"] == "preuve")
        incident = client.lire_incident(a)
        assert logique.enquete_terminee(incident) and logique.mention_simulation(incident)
        assert all(logique.badge_modele(e, True) in (None, "simulé") or logique.badge_modele(e, True).startswith("simulé")
                   for e in evenements)
        comparaison = client.comparaison(a, b)
        assert comparaison["regle_60pc"]["respecte"]
        assert logique.verdict_regle_60pc(comparaison).endswith("(simulé)")
        with pytest.raises(client_api.ErreurApi) as erreur:
            client.comparaison(a, "inconnu")
        assert erreur.value.statut == 404


class _FauxStreamlit:
    """Remplaçant minimal de Streamlit : enregistre les appels d'affichage."""

    def __init__(self, clics=()):
        self.session_state = {}
        self.appels = []
        self.clics = set(clics)

    def fragment(self, run_every=None):
        return lambda fonction: fonction

    def __getattr__(self, nom):
        def appel(*args, **kwargs):
            self.appels.append((nom, args, kwargs))
            if nom == "button":
                return kwargs.get("key", args[0] if args else None) in self.clics or (
                    args and str(args[0]).startswith("Injecter") and "injecter" in self.clics)
            if nom == "selectbox":
                options = list(args[1])
                return options[kwargs.get("index", 0)]
            if nom == "columns":
                return [self] * args[0]
            return None
        return appel

    def textes(self, nom):
        return [str(args[0]) for appel, args, _ in self.appels if appel == nom and args]


def test_zones_avec_streamlit_simule(monkeypatch, tmp_path):
    faux = _FauxStreamlit(clics={"injecter"})
    monkeypatch.setitem(sys.modules, "streamlit", faux)
    monkeypatch.syspath_prepend(str(UI))
    for nom in ("zones", "logique", "client_api"):
        monkeypatch.delitem(sys.modules, nom, raising=False)
    zones = importlib.import_module("zones")
    application = api.creer_app(MemoireEnquete.json(tmp_path / "m.json"), executeur_enquete=lambda tache: tache())
    with TestClient(application) as session:
        client = zones.ClientApi("http://testserver", session=session)
        zones.zone_pipeline(client)
        zones.zone_fil(client)
        zones.zone_resultat(client)
    assert faux.session_state["rappel_incidents"] == ["ui-p1-1"]
    assert any(code.startswith("SELECT") for code in faux.textes("code"))
    assert any("Mode simulé" in texte for texte in faux.textes("caption"))
    assert any(texte.startswith("simulé") for texte in faux.textes("caption"))
    assert not any("Nemotron via Meterless" in texte for texte in faux.textes("caption"))
    assert any("Correctif proposé" in texte for texte in faux.textes("write"))
    faux.session_state["rappel_rejeux"] = 3
    zones.zone_pipeline(client)
    assert any("Plafond de 3" in texte for texte in faux.textes("info"))


def test_zone_fil_api_coupee(monkeypatch):
    faux = _FauxStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", faux)
    monkeypatch.syspath_prepend(str(UI))
    for nom in ("zones", "logique", "client_api"):
        monkeypatch.delitem(sys.modules, nom, raising=False)
    zones = importlib.import_module("zones")
    faux.session_state["rappel_courant"] = "ui-p1-1"
    session = Mock(get=Mock(side_effect=requests.ConnectionError("http://jeton@example.invalid")))
    zones.zone_fil(zones.ClientApi("http://127.0.0.1:8000", session=session))
    avertissements = faux.textes("warning")
    assert avertissements == [zones.logique.MESSAGE_API_COUPEE]
    assert "example.invalid" not in str(faux.appels)
