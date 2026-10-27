"""Garde-fou SQL : schema.table accepté seulement s'il est explicitement autorisé."""

import pytest

from rappel.interfaces.run_sql import (
    TABLES_DONNEES_ZETARIS, RunSqlSQLite, RunSqlZetaris, valider_sql,
)

AUTORISEES = ("logs",) + TABLES_DONNEES_ZETARIS


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
    contexte = {
        "incident_id": "t", "horodatage": "2026-10-27T00:00:00Z", "signature": "s",
        "logs": [], "commits_recents": [], "schema": {"attendu": [], "observe": []},
        "metriques": {},
    }
    assert "rappel.transactions" in RunSqlZetaris(contexte).tables_autorisees
    simulation = RunSqlSQLite(contexte)
    assert "rappel.transactions" not in simulation.tables_autorisees
    simulation.fermer()