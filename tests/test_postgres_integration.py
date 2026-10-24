"""Integration Postgres reelle : lancee seulement avec RAPPEL_TEST_POSTGRES=1 (psycopg et base requis)."""

import os

import pytest

from rappel.enquete.adaptateurs import MemoireEnquete
from rappel.enquete.modeles import EnregistrementMemoire
from scripts import check_postgres

pytestmark = pytest.mark.skipif(os.getenv("RAPPEL_TEST_POSTGRES") != "1",
                                reason="RAPPEL_TEST_POSTGRES=1 requis (Postgres reel)")


def test_diagnostic_postgres_reel():
    assert check_postgres.main() == 0


def test_memoire_enquete_sur_postgres_reel(monkeypatch):
    monkeypatch.setenv("MEMOIRE_PROVIDER", "postgres")
    memoire = MemoireEnquete()
    signature = "integration:rappel-test"
    memoire.ecrire(EnregistrementMemoire(
        signature=signature, cause_confirmee="Test d'integration", hypotheses_refutees=["A"],
        correctif_essaye="Aucun", resultat="reussi", date="2026-10-23",
    ))
    assert memoire.backend.mode == "postgres"
    assert memoire.chercher(signature)[-1].cause_confirmee == "Test d'integration"
