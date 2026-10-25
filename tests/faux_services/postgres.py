"""Faux pilote psycopg minimal (SQLite en mémoire) — faux service local, NON une preuve sponsor.

Couvre ce qu'utilisent MemoirePostgres et scripts/check_postgres.py : connect, curseur,
transactions (with), tables hypotheses et memoire_incidents de sql/init.sql, coupure et reprise.
"""

from datetime import date
import re
import sqlite3
import threading

TABLES = (
    "CREATE TABLE hypotheses (id TEXT PRIMARY KEY, incident_id TEXT, signature TEXT, texte TEXT, statut TEXT, "
    "preuves TEXT, confiance REAL, raison_rejet TEXT, cree_le TEXT DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE memoire_incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, signature TEXT, cause_confirmee TEXT, "
    "hypotheses_refutees TEXT, correctif_essaye TEXT, resultat TEXT, date TEXT)",
)
DATE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Error(Exception):
    pass


class OperationalError(Error):
    pass


class FauxCurseur:
    def __init__(self, connexion):
        self._connexion = connexion
        self._lignes: list = []
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    @staticmethod
    def _traduire(sql: str) -> str:
        sql = sql.replace("%s", "?").replace("::jsonb", "")
        if re.search(r"information_schema\.tables", sql, re.IGNORECASE):
            return "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)"
        return sql

    def execute(self, sql, parametres=()):
        self._connexion._verifier()
        try:
            curseur = self._connexion._sqlite.execute(self._traduire(sql), tuple(parametres))
            lignes = curseur.fetchall()
        except sqlite3.Error as erreur:
            raise Error(f"faux postgres : {erreur}") from None
        self._lignes = [tuple(date.fromisoformat(v) if isinstance(v, str) and DATE_ISO.match(v) else v for v in ligne)
                        for ligne in lignes]
        self.rowcount = curseur.rowcount if curseur.rowcount != -1 else len(self._lignes)

    def fetchone(self):
        return self._lignes.pop(0) if self._lignes else None

    def fetchall(self):
        lignes, self._lignes = self._lignes, []
        return lignes


class FauxConnexion:
    def __init__(self, pilote):
        self._pilote = pilote
        self._sqlite = pilote._base
        self.fermee = False

    def _verifier(self):
        if not self._pilote.disponible:
            raise OperationalError("connexion perdue")

    def cursor(self):
        return FauxCurseur(self)

    def commit(self):
        self._sqlite.commit()

    def rollback(self):
        self._sqlite.rollback()

    def close(self):
        self.fermee = True

    def __enter__(self):
        return self

    def __exit__(self, type_exception, *exception):
        if type_exception is None:
            self.commit()
        else:
            self.rollback()
        self.close()
        return False


class FauxPsycopg:
    """Module de remplacement injectable (sys.modules["psycopg"]) ; `disponible=False` simule la coupure."""

    Error = Error
    OperationalError = OperationalError

    def __init__(self, avec_tables=True):
        self._base = sqlite3.connect(":memory:", check_same_thread=False)
        self._verrou = threading.Lock()
        self.disponible = True
        self.connexions = 0
        if avec_tables:
            for instruction in TABLES:
                self._base.execute(instruction)
            self._base.commit()

    def connect(self, url, connect_timeout=5, **options):
        if not self.disponible:
            raise OperationalError("serveur injoignable")
        with self._verrou:
            self.connexions += 1
        return FauxConnexion(self)
