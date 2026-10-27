"""Une requête SQL par preuve, via fichiers locaux ou Zetaris REST."""

import json
import re
import sqlite3
from contextlib import closing
from uuid import uuid4

import requests

from rappel.config import RACINE, charger
from rappel.securite import url_service

# Clé du SQL dans le corps et endpoint : ZETARIS_CLE_SQL / ZETARIS_CHEMIN_SQL (scripts/decouvrir_zetaris.py).

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


CONSEILS_ZETARIS = {
    "non_autorise": "clé refusée : vérifier ZETARIS_API_KEY",
    "interdit": "clé reconnue mais droits insuffisants : vérifier ZETARIS_ORG_ID et les droits de la clé",
    "introuvable": "vérifier ZETARIS_BASE_URL et ZETARIS_CHEMIN_SQL (scripts/decouvrir_zetaris.py)",
    "requete": "requête refusée : vérifier ZETARIS_CLE_SQL et le dialecte (docs/run_sql.md)",
    "table_inconnue": "table absente côté Zetaris : exposer les tables de docs/run_sql.md (Juan)",
    "syntaxe_sql": "dialecte refusé : utiliser du SQL standard (docs/run_sql.md, portabilité)",
    "quota": "Zetaris limite le débit : réessayer plus tard",
    "serveur": "Zetaris a échoué : consulter ses journaux, puis réessayer",
    "hors_ligne": "Zetaris ne répond pas : vérifier qu'il est démarré (./verify.sh) et ZETARIS_BASE_URL",
    "delai": "délai ZETARIS_DELAI_S dépassé : requête trop lourde ou service surchargé",
    "redirection": "la clé n'est envoyée qu'à l'hôte configuré : corriger ZETARIS_BASE_URL",
    "coupe_circuit": "trop d'échecs consécutifs : attendre une minute puis lancer scripts/check_zetaris.py",
    "trop_volumineux": "résultat trop volumineux : restreindre la requête (LIMIT)",
}
MOTS_TABLE_INCONNUE = ("table or view not found", "table not found", "does not exist", "unknown table",
                       "no such table", "not found")
MOTS_SYNTAXE = ("syntax", "parse", "mismatched", "unexpected", "dialect", "not supported")
_CLIENTS: dict = {}


def client_zetaris(configuration):
    """Client partagé par configuration : le coupe-circuit couvre tous les appels du processus."""
    from rappel.interfaces.client_http import ClientService
    from rappel.interfaces.llm import politique_reseau

    politique = politique_reseau(configuration, configuration.zetaris_delai_s)
    cle = (configuration.zetaris_base_url, configuration.zetaris_api_key, politique)
    if cle not in _CLIENTS:
        _CLIENTS[cle] = ClientService("Zetaris", politique, CONSEILS_ZETARIS, secrets=(configuration.zetaris_api_key,))
    return _CLIENTS[cle]


def extraire_lignes(donnees):
    """Lignes Zetaris : liste, {lignes|rows|data|results: [...]}, ou {columns|headers: [...], rows|data: [[...]]}.

    La passerelle UI du Gateway (/api/proxy/sql-editor/sqls/run-query) renvoie {"headers": [...], "data": [[...]]}.
    """
    if isinstance(donnees, list):
        return donnees
    if not isinstance(donnees, dict):
        return None
    colonnes = donnees.get("columns") or donnees.get("colonnes") or donnees.get("headers")
    for cle in ("lignes", "rows", "data", "results"):
        lignes = donnees.get(cle)
        if isinstance(lignes, list):
            if isinstance(colonnes, list) and lignes and all(isinstance(ligne, list) for ligne in lignes):
                noms = [colonne.get("name") if isinstance(colonne, dict) else str(colonne) for colonne in colonnes]
                return [dict(zip(noms, ligne)) for ligne in lignes]
            return lignes
    return None


def run_sql(sql, *, configuration=None, scenario="p1"):
    from rappel.interfaces.client_http import ErreurService

    _verifier_instruction(sql)
    configuration = configuration or charger()
    if configuration.source_provider == "fichier":
        return {"requete": sql, "lignes": _lignes_fichier(sql, scenario)}
    if not configuration.zetaris_api_key:
        raise ValueError("ZETARIS_API_KEY est requise pour Zetaris.")
    racine = url_service(configuration.zetaris_base_url, "ZETARIS_BASE_URL", (configuration.zetaris_chemin_sql,))
    client = client_zetaris(configuration)
    try:
        # Une requête SELECT ne modifie rien : relancer après une erreur passagère est sans risque.
        reponse = client.requete(
            "post", f"{racine}{configuration.zetaris_chemin_sql}",
            headers={
                "Authorization": "Bearer " + configuration.zetaris_api_key,
                "X-Org-ID": configuration.zetaris_org_id,
                "X-Request-ID": str(uuid4()),
            },
            json={configuration.zetaris_cle_sql: sql},
        )
    except ErreurService as erreur:
        if erreur.categorie == "requete" and erreur.detail:
            detail = erreur.detail.lower()
            if any(mot in detail for mot in MOTS_TABLE_INCONNUE):
                raise client.erreur("table_inconnue", erreur.statut, erreur.detail) from None
            if any(mot in detail for mot in MOTS_SYNTAXE):
                raise client.erreur("syntaxe_sql", erreur.statut, erreur.detail) from None
        raise
    try:
        donnees = reponse.json()
    except ValueError:
        raise RuntimeError("Réponse JSON Zetaris invalide.") from None
    lignes = extraire_lignes(donnees)
    if not isinstance(lignes, list):
        raise RuntimeError("Format des lignes Zetaris à confirmer dans la documentation REST locale.")
    return {"requete": sql, "lignes": lignes}
