"""Découverte de l'API REST Zetaris (lecture seule) : endpoint, clé du SQL, tables, dialecte.

1. OpenAPI publié (/openapi.json, puis /redoc et /docs) : endpoint SQL, clé du SQL dans le corps,
   en-têtes requis ;
2. SELECT 1 AS one avec ZETARIS_API_KEY ;
3. tables visibles (information_schema.tables) comparées à celles de docs/run_sql.md ;
4. portabilité des constructions des requêtes types, une par une (aucune écriture).
La clé n'est jamais affichée. Rapport : data/decouverte_zetaris.json.

  python scripts/decouvrir_zetaris.py
"""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "src"))

import requests

from scripts._essai_commun import afficher, ecrire_rapport, executer_main, nettoyer
from rappel.config import charger
from rappel.interfaces.run_sql import TABLES
from rappel.securite import url_service

REUSSI, ECHOUE, NON_MESURE = "REUSSI", "ECHOUE", "NON_MESURE"
SONDES_DIALECTE = (
    ("LIMIT", "SELECT 1 AS one LIMIT 1"),
    ("FETCH FIRST", "SELECT 1 AS one FETCH FIRST 1 ROWS ONLY"),
    ("date (CAST AS DATE)", "SELECT CAST('2026-10-24' AS DATE) AS jour"),
    ("type (CAST AS INTEGER)", "SELECT CAST('7' AS INTEGER) AS nombre"),
    ("NULL AS colonne dans UNION ALL", "SELECT 'a' AS x, NULL AS y UNION ALL SELECT 'b', 'c'"),
    ("<> (différent)", "SELECT 1 AS one WHERE 1 <> 2"),
    ("LOWER / LIKE", "SELECT 1 AS one WHERE LOWER('AbC') LIKE '%b%'"),
)
REQUETE_TABLES = "SELECT table_name FROM information_schema.tables"


def lire_openapi(configuration) -> dict:
    """Endpoint SQL et clé du corps lus dans l'OpenAPI publié, sinon {} (configuration conservée)."""
    racine = url_service(configuration.zetaris_base_url, "ZETARIS_BASE_URL", (configuration.zetaris_chemin_sql,))
    parties = urlsplit(racine)
    origine = f"{parties.scheme}://{parties.netloc}"
    for url in (f"{origine}/openapi.json", f"{racine}/openapi.json"):
        try:
            reponse = requests.get(url, timeout=(5, 15), allow_redirects=False)
            document = reponse.json() if reponse.status_code == 200 else None
        except (requests.RequestException, ValueError):
            document = None
        if isinstance(document, dict) and isinstance(document.get("paths"), dict):
            break
    else:
        pages = []
        for page in ("/redoc", "/docs"):
            try:
                if requests.get(origine + page, timeout=(5, 15), allow_redirects=False).status_code == 200:
                    pages.append(origine + page)
            except requests.RequestException:
                pass
        return {"publie": False, "pages": pages}
    for chemin, operations in document["paths"].items():
        post = operations.get("post") if isinstance(operations, dict) else None
        if not post or "sql" not in chemin.lower():
            continue
        schema = (post.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {}))
        proprietes = [nom for nom, definition in schema.get("properties", {}).items()
                      if isinstance(definition, dict) and definition.get("type") == "string"]
        en_tetes = [parametre.get("name") for parametre in post.get("parameters", []) if parametre.get("in") == "header"]
        prefixe = parties.path.rstrip("/")
        chemin_relatif = chemin[len(prefixe):] if prefixe and chemin.startswith(prefixe) else chemin
        return {"publie": True, "chemin_sql": chemin_relatif, "cle_sql": proprietes[0] if proprietes else None,
                "en_tetes": en_tetes}
    return {"publie": True, "chemin_sql": None, "cle_sql": None, "en_tetes": []}


def executer(sql, configuration):
    from rappel.interfaces.tools_zetaris import run_sql

    try:
        return run_sql(sql, configuration=configuration)["lignes"], None
    except (ValueError, RuntimeError) as erreur:
        return None, nettoyer(str(erreur))


def decouvrir(configuration) -> dict:
    rapport = {"openapi": {}, "select_1": None, "tables": {}, "dialecte": [], "proposition": {}}
    try:
        openapi = lire_openapi(configuration)
    except ValueError as erreur:
        return {**rapport, "erreur": nettoyer(str(erreur))}
    rapport["openapi"] = openapi
    if openapi.get("chemin_sql"):
        rapport["proposition"]["ZETARIS_CHEMIN_SQL"] = openapi["chemin_sql"]
        configuration = replace(configuration, zetaris_chemin_sql=openapi["chemin_sql"])
    if openapi.get("cle_sql"):
        rapport["proposition"]["ZETARIS_CLE_SQL"] = openapi["cle_sql"]
        configuration = replace(configuration, zetaris_cle_sql=openapi["cle_sql"])
    lignes, erreur = executer("SELECT 1 AS one", configuration)
    ok = bool(lignes) and str(next(iter(lignes[0].values()), "")) == "1" if lignes and isinstance(lignes[0], dict) else False
    rapport["select_1"] = {"statut": REUSSI if ok else ECHOUE, "detail": erreur or ("one = 1" if ok else "réponse inattendue")}
    if not ok:
        return rapport
    lignes, erreur = executer(REQUETE_TABLES, configuration)
    if lignes is None:
        rapport["tables"] = {"statut": NON_MESURE, "detail": erreur}
    else:
        visibles = {str(next(iter(ligne.values()))).lower() for ligne in lignes if isinstance(ligne, dict) and ligne}
        manquantes = [table for table in TABLES if table not in visibles]
        rapport["tables"] = {"statut": REUSSI if not manquantes else ECHOUE, "visibles": sorted(visibles),
                             "manquantes": manquantes}
    for nom, sql in SONDES_DIALECTE:
        lignes, erreur = executer(sql, configuration)
        rapport["dialecte"].append({"construction": nom, "statut": REUSSI if lignes is not None else ECHOUE,
                                    "detail": erreur or f"{len(lignes)} ligne(s)"})
    return rapport


def main(argv=None) -> int:
    parseur = argparse.ArgumentParser(description="Découverte de l'API REST Zetaris (lecture seule).")
    parseur.add_argument("--json", type=Path, default=RACINE / "data" / "decouverte_zetaris.json")
    arguments = parseur.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    configuration = replace(charger(), source_provider="zetaris")
    if not configuration.zetaris_api_key:
        afficher("ZETARIS_API_KEY absente : la renseigner dans .env (clé fournie par l'instance Zetaris).")
        return 2
    rapport = decouvrir(configuration)
    if "erreur" in rapport:
        afficher(rapport["erreur"])
        return 2
    openapi = rapport["openapi"]
    if openapi.get("publie"):
        afficher(f"OpenAPI publié : endpoint SQL {openapi.get('chemin_sql') or 'non trouvé'} ; "
                 f"clé du SQL {openapi.get('cle_sql') or 'non trouvée'} ; en-têtes {', '.join(openapi.get('en_tetes') or []) or '-'}")
    else:
        afficher("OpenAPI non publié" + (f" ; pages : {', '.join(openapi['pages'])}" if openapi.get("pages") else "")
                 + " : garder ZETARIS_CHEMIN_SQL / ZETARIS_CLE_SQL de .env et vérifier dans la documentation.")
    afficher(f"SELECT 1 AS one : {rapport['select_1']['statut']} ({rapport['select_1']['detail']})")
    if rapport["tables"]:
        tables = rapport["tables"]
        afficher(f"Tables de docs/run_sql.md : {tables['statut']}"
                 + (f" ; manquantes : {', '.join(tables['manquantes'])} (à exposer par Juan)" if tables.get("manquantes") else "")
                 + (f" ; {tables['detail']}" if tables.get("detail") else ""))
    for sonde in rapport["dialecte"]:
        afficher(f"Dialecte {sonde['construction']} : {sonde['statut']} ({sonde['detail']})")
    for cle, valeur in rapport["proposition"].items():
        afficher(f"Proposé pour .env : {cle}={valeur}")
    arguments.json.parent.mkdir(parents=True, exist_ok=True)
    ecrire_rapport(arguments.json, nettoyer(json.dumps(rapport, ensure_ascii=False, indent=2)))
    ok = rapport["select_1"]["statut"] == REUSSI and rapport["tables"].get("statut") == REUSSI
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(executer_main(main))
