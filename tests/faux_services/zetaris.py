"""Faux Zetaris REST — faux service local, NON une preuve sponsor.

Exécute le SQL reçu sur une base SQLite en lecture seule construite depuis un ContexteIncident
(tables de docs/run_sql.md). Le contexte servi est piloté par le test : un vrai Zetaris expose
des vues limitées à l'incident.
"""

import json
from pathlib import Path
import re
import sqlite3
import threading
from uuid import UUID

from tests.faux_services.serveur import ServeurLocal

RACINE = Path(__file__).resolve().parents[2]


def contexte_fixture(nom="p1"):
    return json.loads((RACINE / "fixtures" / f"contexte_{nom}.json").read_text(encoding="utf-8"))


class FauxZetaris(ServeurLocal):
    def __init__(self, cle="cle-" + "faux-zetaris-local", org_id="7", chemin="/sql-editor/sqls/run", cle_sql="sql",
                 contexte=None, publier_openapi=True):
        self.cle, self.org_id, self.chemin, self.cle_sql = cle, org_id, chemin, cle_sql
        self.contexte = contexte or contexte_fixture("p1")
        self.publier_openapi = publier_openapi
        self.scenarios: list[str] = []
        self.refuser_limit = False
        self.requetes: list[dict] = []
        self.ids_vus: set[str] = set()
        self._verrou = threading.Lock()

    def openapi(self):
        return {"openapi": "3.0.0", "info": {"title": "Faux Zetaris (local)"}, "paths": {
            "/api/v1.0" + self.chemin: {"post": {
                "summary": "Run SQL",
                "parameters": [{"name": "X-Org-ID", "in": "header", "required": True},
                               {"name": "X-Request-ID", "in": "header", "required": True}],
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object", "required": ["queryId", self.cle_sql, "limit", "source"],
                    "properties": {
                        "queryId": {"type": "string"},
                        self.cle_sql: {"type": "string"},
                        "limit": {"type": "integer"},
                        "source": {"type": "string"},
                    }}}}},
            }},
            "/api/v1.0/health": {"get": {"summary": "Health"}},
        }}

    def traiter(self, methode, chemin, entetes, corps):
        with self._verrou:
            self.requetes.append({"methode": methode, "chemin": chemin, "entetes": entetes, "corps": corps})
        if methode == "GET" and chemin in ("/openapi.json", "/api/v1.0/openapi.json"):
            return (200, self.openapi(), None) if self.publier_openapi else (404, {"message": "absent"}, None)
        if methode == "POST" and chemin == "/api/v1.0" + self.chemin:
            return self._sql(entetes, corps)
        return 404, {"message": f"chemin inconnu : {chemin}"}, None

    def _sql(self, entetes, corps):
        if entetes.get("Authorization") != "Bearer " + self.cle:
            return 401, {"message": "unauthorized"}, None
        if entetes.get("X-Org-ID") != self.org_id:
            return 403, {"message": "organisation refusee"}, None
        identifiant = entetes.get("X-Request-ID")
        with self._verrou:
            if not identifiant or identifiant in self.ids_vus:
                return 400, {"message": "X-Request-ID manquant ou deja utilise"}, None
            self.ids_vus.add(identifiant)
            scenario = self.scenarios.pop(0) if self.scenarios else "ok"
        if not isinstance(corps, dict) or not isinstance(corps.get(self.cle_sql), str):
            return 400, {"message": f"champ {self.cle_sql} requis dans le corps"}, None
        try:
            if UUID(corps.get("queryId", "")).version != 4:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            return 400, {"message": "queryId UUID v4 requis"}, None
        if corps.get("source") != "SqlEditor" or not isinstance(corps.get("limit"), int):
            return 400, {"message": "source ou limit invalide"}, None
        if scenario in ("401", "500", "429"):
            return int(scenario), {"message": f"erreur simulee {scenario}"}, None
        if scenario == "table_not_found_real":
            return 400, {"msg": "[TABLE_OR_VIEW_NOT_FOUND] The table or view `default`.`logs` cannot be found."}, None
        sql = corps[self.cle_sql]
        if scenario == "syntaxe_limit" or (self.refuser_limit and re.search(r"\blimit\b", sql, re.IGNORECASE)):
            return 400, {"message": "Syntax error: mismatched input 'LIMIT' expecting <EOF>"}, None
        if self.refuser_limit:
            # Dialecte imité : FETCH FIRST n ROWS ONLY accepté (traduit pour SQLite).
            sql = re.sub(r"\s+FETCH\s+FIRST\s+(\d+)\s+ROWS\s+ONLY\s*$", r" LIMIT \1", sql, flags=re.IGNORECASE)
        if scenario == "volumineux":
            return 200, {"columns": [{"name": "n"}], "rows": [[numero] for numero in range(10_000)]}, None
        return self._executer(sql)

    def _executer(self, sql):
        from rappel.interfaces.run_sql import TABLES, construire_base

        if re.search(r"information_schema\.tables", sql, re.IGNORECASE):
            return 200, {"columns": [{"name": "table_name"}], "rows": [[table] for table in TABLES]}, None
        connexion = construire_base(self.contexte)
        try:
            connexion.execute("PRAGMA query_only = ON")
            curseur = connexion.execute(sql)
            colonnes = [{"name": description[0]} for description in curseur.description or []]
            lignes = [list(ligne) for ligne in curseur.fetchall()]
        except sqlite3.Error as erreur:
            texte = str(erreur)
            if "no such table" in texte:
                return 400, {"message": "Table or view not found: " + texte.split(":")[-1].strip()}, None
            return 400, {"message": f"Syntax error: {texte}"}, None
        finally:
            connexion.close()
        return 200, {"columns": colonnes, "rows": lignes}, None
