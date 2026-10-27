"""Garde-fou SQL : schema.table accepté seulement s'il est explicitement autorisé."""

import pytest

from rappel.config import Configuration
from rappel.interfaces.run_sql import (
    TABLES_DONNEES_ZETARIS, RunSqlSQLite, RunSqlZetaris, creer_run_sql, valider_sql,
)

AUTORISEES = ("logs",) + TABLES_DONNEES_ZETARIS
CONTEXTE_MINIMAL = {
    "incident_id": "t", "horodatage": "2026-10-27T00:00:00Z", "signature": "s",
    "logs": [], "commits_recents": [], "schema": {"attendu": [], "observe": []},
    "metriques": {},
}


def test_table_qualifiee_autorisee():
    validation = valider_sql("SELECT COUNT(*) AS n FROM rappel.transactions", AUTORISEES)
    assert validation.erreur is None
    assert validation.tables == ["rappel.transactions"]
    assert validation.requete.endswith("LIMIT 50")


def test_jointure_avec_alias():
    sql = ("SELECT t.amount, c.expected_type FROM rappel.transactions t "
           "JOIN rappel.schema_contract c ON c.column_name = 'amount' LIMIT 5")
    validation = valider_sql(sql, AUTORISEES)
    assert validation.erreur is None
    assert validation.tables == ["rappel.schema_contract", "rappel.transactions"]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM postgres.logs",
    "SELECT * FROM rappel.secret",
    "SELECT * FROM rappel.transactions.x",
    "SELECT * FROM rappel.",
])
def test_qualifiees_non_autorisees_refusees(sql):
    assert "noms de tables qualifies interdits" in valider_sql(sql, AUTORISEES).erreur


def test_nom_court_non_equivalent():
    assert "hors liste blanche" in valider_sql("SELECT * FROM transactions", AUTORISEES).erreur


def test_ecriture_toujours_refusee():
    assert "interdit" in valider_sql("DROP TABLE rappel.transactions", AUTORISEES).erreur


def test_seul_le_mode_zetaris_expose_rappel():
    zetaris = RunSqlZetaris(CONTEXTE_MINIMAL, Configuration(source_provider="zetaris"))
    assert "rappel.transactions" in zetaris.tables_autorisees
    simulation = RunSqlSQLite(CONTEXTE_MINIMAL)
    assert "rappel.transactions" not in simulation.tables_autorisees
    simulation.fermer()


def test_run_sql_zetaris_refuse_le_mode_fichier():
    """Issue #15 : plus d'interrogation silencieuse des fixtures locales."""
    with pytest.raises(ValueError, match="SOURCE_PROVIDER=zetaris"):
        RunSqlZetaris(CONTEXTE_MINIMAL, Configuration(source_provider="fichier"))


def test_creer_run_sql_choisit_le_bon_backend():
    simulation = creer_run_sql(CONTEXTE_MINIMAL, Configuration(source_provider="fichier"))
    assert isinstance(simulation, RunSqlSQLite)
    simulation.fermer()
    assert isinstance(creer_run_sql(CONTEXTE_MINIMAL, Configuration(source_provider="zetaris")), RunSqlZetaris)