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
    evenements, hors_ligne = ui.charger_evenements(Configuration())
    assert hors_ligne
    assert evenements


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
    assert any("API coupée" in message.value for message in app.warning)
    assert any("214 KeyError" in message.value for message in app.info)
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
