import importlib.util
import json
from dataclasses import replace
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


CONTEXTE = {
    "incident_id": "live-001",
    "horodatage": "2026-10-25T10:00:00Z",
    "signature": "schema_mismatch:orders_etl",
    "logs": [{"template": "KeyError: column <*> not found", "compte": 4}],
    "commits_recents": [],
    "schema": {"attendu": ["amount"], "observe": ["total"]},
    "metriques": {"lignes_rejetees": 4, "latence_ms": 120},
}


def configuration_reelle():
    return replace(
        Configuration(),
        llm_provider="meterless",
        meterless_runtime_token="runtime-test-token",
        meterless_model_rapide="nemotron-lightning",
        meterless_model_profond="nemotron-super",
        source_provider="zetaris",
        zetaris_api_key="zetaris-test-key",
        zetaris_org_id="org-test",
        memoire_provider="postgres",
    )


def test_lire_contexte_json_valide():
    assert ui.lire_contexte_json(json.dumps(CONTEXTE)) == CONTEXTE


@pytest.mark.parametrize("contenu", ["{", "[]", '{"incident_id":"incomplet"}'])
def test_lire_contexte_json_refuse_document_invalide(contenu):
    with pytest.raises(ValueError):
        ui.lire_contexte_json(contenu)


def test_configuration_reelle_exige_les_trois_fournisseurs():
    assert ui.verifier_configuration_reelle(Configuration(), {}) == [
        "LLM_PROVIDER=meterless",
        "SOURCE_PROVIDER=zetaris",
        "MEMOIRE_PROVIDER=postgres",
        "METERLESS_RUNTIME_TOKEN",
        "METERLESS_MODEL_RAPIDE",
        "METERLESS_MODEL_PROFOND",
        "ZETARIS_API_KEY",
        "modes réels confirmés par /healthz",
    ]


def test_configuration_reelle_acceptee_si_modes_et_secrets_presents():
    sante = {
        "llm": "meterless",
        "source": "zetaris",
        "memoire": "postgres",
    }
    assert ui.verifier_configuration_reelle(configuration_reelle(), sante) == []


def test_soumettre_incident_appelle_api_sans_fallback(monkeypatch):
    reponse = Mock()
    reponse.json.return_value = {"id": "live-001"}
    poster = Mock(return_value=reponse)
    monkeypatch.setattr(ui.requests, "post", poster)

    assert ui.soumettre_incident("http://127.0.0.1:8000", CONTEXTE) == "live-001"
    poster.assert_called_once_with(
        "http://127.0.0.1:8000/incidents",
        json=CONTEXTE,
        timeout=10,
    )


def test_soumission_api_indisponible_est_signalee(monkeypatch):
    monkeypatch.setattr(
        ui.requests,
        "post",
        Mock(side_effect=requests.ConnectionError("secret")),
    )
    with pytest.raises(RuntimeError, match="API locale"):
        ui.soumettre_incident("http://127.0.0.1:8000", CONTEXTE)


def test_resultat_exige_provenance_reelle_et_memoire_postgres():
    resultat = {
        "statut": "termine",
        "llm": "meterless",
        "source_preuves": "zetaris",
        "preuves_simulees": False,
        "enquete_simulee": False,
        "memoire_mode": "postgres",
    }
    assert ui.verifier_resultat_reel(resultat)
    for cle, valeur in (
        ("llm", "fake"),
        ("source_preuves", "simule-sqlite"),
        ("preuves_simulees", True),
        ("enquete_simulee", True),
        ("memoire_mode", "fallback-json"),
    ):
        resultat_invalide = {**resultat, cle: valeur}
        assert not ui.verifier_resultat_reel(resultat_invalide)


def test_tri_etape_puis_horodatage():
    evenements = [
        {"etape": 10, "horodatage": "2026-01-01"},
        {"etape": 2, "horodatage": "2026-01-02"},
        {"etape": 2, "horodatage": "2026-01-01"},
    ]
    assert ui.trier_evenements(evenements) == [
        evenements[2],
        evenements[1],
        evenements[0],
    ]


def test_streamlit_hors_ligne_ne_charge_aucune_fixture(monkeypatch):
    monkeypatch.setattr(ui, "charger", configuration_reelle)
    monkeypatch.setattr(
        ui.requests,
        "get",
        Mock(side_effect=requests.ConnectionError("secret")),
    )
    app = AppTest.from_file(str(APP)).run(timeout=15)
    assert not app.exception
    assert any("Aucun résultat local ou fixture" in item.value for item in app.error)
    assert "214 KeyError" not in str(app)
    assert "example.invalid" not in str(app)


def test_streamlit_refuse_configuration_fake(monkeypatch):
    monkeypatch.setattr(ui, "charger", Configuration)
    reponse = Mock()
    reponse.json.return_value = {
        "statut": "ok",
        "llm": "fake",
        "source": "fichier",
        "memoire": "json",
    }
    monkeypatch.setattr(ui.requests, "get", Mock(return_value=reponse))
    app = AppTest.from_file(str(APP)).run(timeout=15)
    assert not app.exception
    assert any("Configuration réelle incomplète" in item.value for item in app.error)
    assert not app.button
