import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from streamlit.testing.v1 import AppTest

from rappel.config import Configuration


APP = Path(__file__).resolve().parents[1] / "ui" / "app.py"
spec = importlib.util.spec_from_file_location("rappel_ui", APP)
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


def test_evenements_api_et_timeout(monkeypatch):
    evenements = [{"etape": 1, "horodatage": "2026-01-01", "message": "OK"}]
    reponse = Mock()
    reponse.json.return_value = {"evenements": evenements}
    obtenir = Mock(return_value=reponse)
    monkeypatch.setattr(ui.requests, "get", obtenir)
    resultat, hors_ligne = ui.charger_evenements(Configuration())
    assert resultat == evenements
    assert not hors_ligne
    obtenir.assert_called_once_with(
        "http://127.0.0.1:8000/incidents/p1/evenements", timeout=5
    )


@pytest.mark.parametrize(
    "probleme",
    [
        requests.ConnectionError("******example.invalid"),
        ValueError("JSON invalide avec secret"),
    ],
)
def test_repli_fixture(monkeypatch, probleme):
    monkeypatch.setattr(ui.requests, "get", Mock(side_effect=probleme))
    evenements, hors_ligne = ui.charger_evenements(Configuration(), repli_fixture=True)
    assert hors_ligne
    assert evenements


@pytest.mark.parametrize(
    ("incident_id", "repli_fixture"),
    [("p1", False), ("p2", True), ("p3", True)],
)
def test_aucun_repli_p1_implicite(monkeypatch, incident_id, repli_fixture):
    monkeypatch.setattr(
        ui.requests, "get", Mock(side_effect=requests.ConnectionError("secret"))
    )
    with pytest.raises(RuntimeError, match="Événements indisponibles") as erreur:
        ui.charger_evenements(Configuration(), incident_id, repli_fixture)
    assert "secret" not in str(erreur.value)


def test_identifiant_incident_encode(monkeypatch):
    reponse = Mock()
    reponse.json.return_value = []
    obtenir = Mock(return_value=reponse)
    monkeypatch.setattr(ui.requests, "get", obtenir)
    assert ui.charger_evenements(Configuration(), "inc/42?autre=1") == ([], False)
    obtenir.assert_called_once_with(
        "http://127.0.0.1:8000/incidents/inc%2F42%3Fautre%3D1/evenements", timeout=5
    )


def test_tri_etape_puis_horodatage():
    evenements = [
        {"etape": 10, "horodatage": "2026-01-01"},
        {"etape": 2, "horodatage": "2026-01-02"},
        {"etape": 2, "horodatage": "2026-01-01"},
    ]
    assert ui.trier_evenements(evenements) == [
        evenements[2], evenements[1], evenements[0]
    ]


def test_streamlit_hors_ligne_sans_erreur_brute(monkeypatch):
    monkeypatch.setattr(
        requests, "get",
        Mock(side_effect=requests.ConnectionError("******example.invalid")),
    )
    app = AppTest.from_file(str(APP)).run(timeout=15)
    assert not app.exception
    assert any("API coupée" in message.value for message in app.error)
    assert not any("214 KeyError" in message.value for message in app.info)
    assert "secret" not in str(app)
    assert "example.invalid" not in str(app)


def test_streamlit_mode_et_champs_optionnels(monkeypatch):
    def obtenir(url, **kwargs):
        assert kwargs["timeout"] == 5
        assert url.endswith(("/healthz", "/incidents/p1/evenements"))
        reponse = Mock()
        reponse.json.return_value = (
            {
                "statut": "ok",
                "llm": "fake",
                "source": "fichier",
                "memoire": "json",
            } if url.endswith("/healthz") else [
                {
                    "etape": 1,
                    "type": "erreur",
                    "texte": "<script>alert('x')</script>",
                    "modele": "fake/nemotron",
                    "requete_sql": "SELECT 1 AS one",
                }
            ]
        )
        return reponse

    monkeypatch.setattr(requests, "get", obtenir)
    app = AppTest.from_file(str(APP)).run(timeout=15)
    assert not app.exception
    assert any(
        "Mode API — llm : fake · source : fichier · memoire : json" in texte.value
        for texte in app.text
    )
    assert any("fake/nemotron" in texte.value for texte in app.text)
    assert app.code[0].value == "SELECT 1 AS one"
    assert app.error[0].value == "<script>alert('x')</script>"


def test_fixture_explicite_sans_reseau(monkeypatch):
    obtenir = Mock(side_effect=requests.ConnectionError("secret"))
    monkeypatch.setattr(requests, "get", obtenir)
    app = AppTest.from_file(str(APP)).run(timeout=15)
    obtenir.reset_mock()
    app.radio[0].set_value("Fixture locale P1").run()
    assert not app.exception
    obtenir.assert_not_called()
    assert any("Simulation locale P1" in message.value for message in app.warning)
    assert any("214 KeyError" in message.value for message in app.info)
    assert any("requête SQL non transmise" in message.value for message in app.warning)
    assert app.text_input[0].disabled


def test_selection_incident_et_actualisation(monkeypatch):
    reponse = Mock()
    reponse.json.return_value = []
    obtenir = Mock(return_value=reponse)
    monkeypatch.setattr(requests, "get", obtenir)
    app = AppTest.from_file(str(APP)).run(timeout=15)
    app.text_input[0].set_value("p2").run()
    assert not app.exception
    assert any("Aucun événement" in message.value for message in app.info)
    obtenir.assert_any_call("http://127.0.0.1:8000/incidents/p2/evenements", timeout=5)
    obtenir.reset_mock()
    app.button[0].click().run()
    obtenir.assert_any_call("http://127.0.0.1:8000/incidents/p2/evenements", timeout=5)
    obtenir.reset_mock()
    app.radio[0].set_value("Fixture locale P1").run()
    assert app.text_input[0].value == "p1"
    obtenir.assert_not_called()
    app.radio[0].set_value("API").run()
    obtenir.reset_mock()
    app.text_input[0].set_value("   ").run()
    obtenir.assert_not_called()


def test_evenements_autre_incident_refuses(monkeypatch):
    reponse = Mock()
    reponse.json.return_value = [{"incident_id": "p2", "texte": "Diagnostic étranger"}]
    monkeypatch.setattr(requests, "get", Mock(return_value=reponse))
    app = AppTest.from_file(str(APP)).run(timeout=15)
    assert not app.exception
    assert any("ne correspondent pas" in message.value for message in app.error)
    assert "Diagnostic étranger" not in str(app)


def simuler_api_p1(monkeypatch, erreur=None, abstention=False):
    def obtenir(url, **kwargs):
        reponse = Mock()
        reponse.json.return_value = (
            {"llm": "fake", "source": "fichier", "memoire": "json"}
            if url.endswith("/healthz")
            else [{"incident_id": "p1", "type": "abstention" if abstention else "decision", "texte": "Diagnostic"}]
        )
        return reponse

    def publier(url, json, timeout):
        if erreur:
            raise erreur
        reponse = Mock()
        reponse.json.return_value = {"id": "p1", "decision": json["decision"], "execute": False}
        return reponse

    monkeypatch.setattr(requests, "get", obtenir)
    envoi = Mock(side_effect=publier)
    monkeypatch.setattr(requests, "post", envoi)
    return envoi


@pytest.mark.parametrize("decision", ["appliquer", "refuser"])
def test_decision_api_sans_execution_et_sans_double_envoi(monkeypatch, decision):
    envoi = simuler_api_p1(monkeypatch)
    app = AppTest.from_file(str(APP)).run(timeout=15)
    app.button(key=decision).click().run()
    assert not app.exception
    envoi.assert_called_once_with(
        "http://127.0.0.1:8000/correctifs/p1/decision", json={"decision": decision}, timeout=5
    )
    assert any("Aucun patch exécuté" in message.value for message in app.success)
    assert app.button(key="appliquer").disabled
    assert app.button(key="refuser").disabled
    app.button[0].click().run()
    assert envoi.call_count == 1


def test_decision_timeout_non_rejouee(monkeypatch):
    envoi = simuler_api_p1(monkeypatch, erreur=requests.Timeout("secret"))
    app = AppTest.from_file(str(APP)).run(timeout=15)
    app.button(key="appliquer").click().run()
    assert not app.exception
    assert any("Décision non confirmée" in message.value for message in app.warning)
    assert not any("enregistrée" in message.value for message in app.success)
    assert "secret" not in str(app)
    assert app.button(key="appliquer").disabled
    app.button[0].click().run()
    assert envoi.call_count == 1


def test_simulation_locale_et_abstention_sans_action(monkeypatch):
    envoi = simuler_api_p1(monkeypatch, abstention=True)
    app = AppTest.from_file(str(APP)).run(timeout=15)
    assert not app.exception
    assert len(app.button) == 1
    assert any("aucun correctif" in message.value for message in app.warning)
    app.radio[0].set_value("Fixture locale P1").run()
    assert app.button(key="appliquer").disabled
    assert app.button(key="refuser").disabled
    envoi.assert_not_called()


@pytest.mark.parametrize("resultat", [None, {}, {"id": "p2", "decision": "appliquer", "execute": False}, {"id": "p1", "decision": "appliquer", "execute": True}])
def test_reponse_decision_invalide(monkeypatch, resultat):
    reponse = Mock()
    reponse.json.return_value = resultat
    monkeypatch.setattr(requests, "post", Mock(return_value=reponse))
    with pytest.raises(RuntimeError, match="Décision non confirmée"):
        ui.envoyer_decision(Configuration(), "p1", "appliquer")


def test_interface_avec_api_phase_01(monkeypatch):
    from fastapi.testclient import TestClient
    from rappel import api

    monkeypatch.setattr(api, "_DECISIONS", {})
    with TestClient(api.app) as client:
        monkeypatch.setattr(
            requests, "get",
            lambda url, **kwargs: client.get(url.removeprefix("http://127.0.0.1:8000")),
        )
        publier = Mock(side_effect=lambda url, json, **kwargs: client.post(
            url.removeprefix("http://127.0.0.1:8000"), json=json
        ))
        monkeypatch.setattr(requests, "post", publier)
        app = AppTest.from_file(str(APP)).run(timeout=15)
        assert not app.exception
        assert any("8 événements" in texte.value for texte in app.caption)
        assert any("SELECT column_name" in code.value for code in app.code)
        app.button(key="refuser").click().run()
        assert not app.exception
        assert api._DECISIONS == {"p1": "refuser"}
        assert any("Aucun patch exécuté" in message.value for message in app.success)
        app.button[0].click().run()
        publier.assert_called_once()


def test_api_reelle_indisponible_sans_fixture(monkeypatch):
    def obtenir(url, **kwargs):
        reponse = Mock()
        if url.endswith("/healthz"):
            reponse.json.return_value = {"llm": "meterless", "source": "zetaris", "memoire": "postgres"}
        else:
            reponse.raise_for_status.side_effect = requests.HTTPError("secret")
        return reponse

    monkeypatch.setattr(requests, "get", obtenir)
    app = AppTest.from_file(str(APP)).run(timeout=15)
    assert not app.exception
    assert any("API coupée" in message.value for message in app.error)
    assert not any("214 KeyError" in message.value for message in app.info)
    assert len(app.button) == 1
    assert "secret" not in str(app)


def test_polling_uniquement_en_mode_api(monkeypatch):
    import streamlit as st

    simuler_api_p1(monkeypatch)
    fragment_original = st.fragment
    intervalles = []

    def fragment(*args, **kwargs):
        intervalles.append(kwargs.get("run_every"))
        return fragment_original(*args, **kwargs)

    monkeypatch.setattr(st, "fragment", fragment)
    app = AppTest.from_file(str(APP)).run(timeout=15)
    assert intervalles[-1] is None
    app.toggle[0].set_value(True).run()
    assert not app.exception
    assert intervalles[-1] == 5
    app.radio[0].set_value("Fixture locale P1").run()
    assert not app.exception
    assert intervalles[-1] is None


@pytest.mark.parametrize("modification", [{"niveau_risque": "refus"}, {"reversible": False}])
def test_correctif_refuse_ou_irreversible_non_applicable(monkeypatch, tmp_path, modification):
    import json
    from rappel import config

    correctif = {**ui.charger_fixture("correctif_p1.json"), **modification}
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "correctif_p1.json").write_text(json.dumps(correctif), encoding="utf-8")
    monkeypatch.setattr(config, "RACINE", tmp_path)
    envoi = simuler_api_p1(monkeypatch)
    app = AppTest.from_file(str(APP)).run(timeout=15)
    assert not app.exception
    assert app.button(key="appliquer").disabled
    assert not app.button(key="refuser").disabled
    envoi.assert_not_called()
