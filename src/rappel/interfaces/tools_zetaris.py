"""Une requête SQL par preuve, via fichiers locaux ou Zetaris REST."""

import json
import re
import sqlite3
from contextlib import closing
from uuid import uuid4

import requests

from rappel.config import RACINE, charger
from rappel.securite import url_service

# TODO : confirmer le corps via http://localhost:8888/redoc/index.html.
CLE_SQL_CORPS = "sql"

RUN_SQL_TOOL = {
    "type": "function",
    "function": {
        "name": "run_sql",
        "description": (
            "Exécute une seule requête SQL Lightning dans Zetaris sur les tables "
            "du pipeline (orders, logs, commits, métriques) et renvoie les lignes"
        ),
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
}


def _verifier_instruction(sql):
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("Une instruction SQL non vide est requise.")
    # Les chaînes, identifiants et commentaires ne séparent pas les instructions.
    motif = re.compile(
        r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`"
        r"|--[^\r\n]*(?:[\r\n]|$)|/\*.*?\*/|[^'\"`;/\-]+|[/-]|;",
        re.DOTALL,
    )
    fin = 0
    termine = False
    contenu = False
    for jeton in motif.finditer(sql):
        if jeton.start() != fin:
            raise ValueError("Chaîne ou commentaire SQL non terminé.")
        valeur = jeton.group()
        fin = jeton.end()
        if valeur == "/" and sql[fin:fin + 1] == "*":
            raise ValueError("Commentaire SQL non terminé.")
        if valeur.startswith("--"):
            continue
        if valeur.startswith("/*"):
            if "/*" in valeur[2:]:
                raise ValueError("Commentaires SQL imbriqués non pris en charge.")
            continue
        if valeur == ";":
            if termine or not contenu:
                raise ValueError("Une seule instruction SQL est autorisée par appel.")
            termine = True
        elif valeur.strip():
            if termine:
                raise ValueError("Une seule instruction SQL est autorisée par appel.")
            contenu = True
    if fin != len(sql) or not contenu or re.search(r"\$(?:[A-Za-z_]\w*)?\$", sql):
        raise ValueError("Instruction SQL invalide ou syntaxe non prise en charge.")


def _lignes_fichier(sql, scenario):
    if scenario not in ("p1", "p2", "p3"):
        raise ValueError("Scénario fichier inconnu : utiliser p1, p2 ou p3.")
    contexte = json.loads(
        (RACINE / "fixtures" / f"contexte_{scenario}.json").read_text(encoding="utf-8")
    )
    with closing(sqlite3.connect(":memory:")) as connexion:
        connexion.row_factory = sqlite3.Row
        connexion.execute("ATTACH DATABASE ':memory:' AS pipeline")
        connexion.execute("ATTACH DATABASE ':memory:' AS information_schema")
        connexion.executescript(
            "CREATE TABLE pipeline.logs (template TEXT, compte INTEGER, horodatage TEXT);"
            "CREATE TABLE pipeline.commits (hash TEXT, message TEXT, date TEXT);"
            "CREATE TABLE pipeline.metriques (lignes_rejetees INTEGER, latence_ms REAL);"
            "CREATE TABLE pipeline.schema_orders (attendu TEXT, observe TEXT);"
            "CREATE TABLE information_schema.columns "
            "(table_schema TEXT, table_name TEXT, column_name TEXT, data_type TEXT, ordinal_position INTEGER);"
        )
        connexion.executemany(
            "INSERT INTO pipeline.logs VALUES (?, ?, ?)",
            [(log["template"], log["compte"], contexte["horodatage"]) for log in contexte["logs"]],
        )
        connexion.executemany(
            "INSERT INTO pipeline.commits VALUES (?, ?, ?)",
            [(commit["hash"], commit["message"], commit["date"]) for commit in contexte["commits_recents"]],
        )
        connexion.execute(
            "INSERT INTO pipeline.metriques VALUES (?, ?)",
            (contexte["metriques"]["lignes_rejetees"], contexte["metriques"]["latence_ms"]),
        )
        connexion.executemany(
            "INSERT INTO pipeline.schema_orders VALUES (?, ?)",
            list(zip(contexte["schema"]["attendu"], contexte["schema"]["observe"])),
        )
        # Table vide : seuls les noms de colonnes observés sont disponibles en Phase 1.
        colonnes = [
            colonne.split(":", 1)[0] for colonne in contexte["schema"]["observe"]
        ]
        connexion.executemany(
            "INSERT INTO information_schema.columns VALUES (?, ?, ?, ?, ?)",
            [
                ("pipeline", "orders", colonne.split(":", 1)[0],
                 colonne.split(":", 1)[1] if ":" in colonne else "text", position)
                for position, colonne in enumerate(contexte["schema"]["observe"], 1)
            ],
        )
        definition = ", ".join('"' + nom.replace('"', '""') + '" TEXT' for nom in colonnes)
        connexion.execute(f"CREATE TABLE pipeline.orders ({definition})")
        autorises = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
        connexion.set_authorizer(
            lambda action, *_: sqlite3.SQLITE_OK if action in autorises else sqlite3.SQLITE_DENY
        )
        try:
            return [dict(ligne) for ligne in connexion.execute(sql).fetchall()]
        except sqlite3.Error:
            raise ValueError("Requête non prise en charge par les fixtures locales (SQL SQLite).") from None


def run_sql(sql, *, configuration=None, scenario="p1"):
    _verifier_instruction(sql)
    configuration = configuration or charger()
    if configuration.source_provider == "fichier":
        return {"requete": sql, "lignes": _lignes_fichier(sql, scenario)}
    if not configuration.zetaris_api_key:
        raise ValueError("ZETARIS_API_KEY est requise pour Zetaris.")
    racine = url_service(configuration.zetaris_base_url, "ZETARIS_BASE_URL", ("/sql-editor/sqls/run",))
    try:
        # Pas de redirection : la clé n'est envoyée qu'à l'hôte configuré.
        reponse = requests.post(
            f"{racine}/sql-editor/sqls/run",
            headers={
                "Authorization": "Bearer " + configuration.zetaris_api_key,
                "X-Org-ID": configuration.zetaris_org_id,
                "X-Request-ID": str(uuid4()),
            },
            json={CLE_SQL_CORPS: sql},
            timeout=30,
            allow_redirects=False,
        )
        reponse.raise_for_status()
        donnees = reponse.json()
    except requests.RequestException:
        raise RuntimeError("Zetaris est indisponible ou a refusé la requête ; vérifier sa configuration.") from None
    except ValueError:
        raise RuntimeError("Réponse JSON Zetaris invalide.") from None
    if isinstance(donnees, list):
        lignes = donnees
    elif isinstance(donnees, dict):
        lignes = donnees.get("lignes", donnees.get("rows"))
    else:
        lignes = None
    if not isinstance(lignes, list):
        raise RuntimeError("Format des lignes Zetaris à confirmer dans la documentation REST locale.")
    return {"requete": sql, "lignes": lignes}
