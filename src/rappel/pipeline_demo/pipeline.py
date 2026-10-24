"""Pipeline de démo isolé : CSV d'entrée -> transformation -> table SQLite, logs et état.

Tout est écrit dans un dossier dédié (PIPELINE_DEMO_DIR, défaut data/pipeline_demo, ignoré
par git). Les données des scénarios (colonnes, commits, environnement) viennent de
scenarios.json : ce module ne contient aucune valeur propre à un scénario.
"""

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3

from rappel.config import RACINE, charger

SCENARIOS = json.loads(Path(__file__).with_name("scenarios.json").read_text(encoding="utf-8"))
NOMS_SCENARIOS = tuple(SCENARIOS["scenarios"])
DOSSIER_SAUVEGARDES = "sauvegardes"
TYPES_SQL = {"integer": "INTEGER", "decimal": "REAL", "text": "TEXT"}


def dossier_demo(dossier=None) -> Path:
    chemin = Path(dossier) if dossier else Path(charger().pipeline_demo_dir)
    return (chemin if chemin.is_absolute() else RACINE / chemin).resolve()


def chemins(dossier=None) -> dict[str, Path]:
    racine = dossier_demo(dossier)
    return {
        "racine": racine,
        "config": racine / "pipeline" / "config.json",
        "mapping": racine / "pipeline" / "mapping.json",
        "environnement": racine / "pipeline" / "environnement.json",
        "entree": racine / "entree" / f"{SCENARIOS['table_entree']}.csv",
        "commits": racine / "historique" / "commits.json",
        "logs": racine / "logs" / "pipeline.log",
        "etat": racine / "etat.json",
        "sortie": racine / "sortie" / "demo.sqlite",
        "scenario": racine / "scenario.json",
    }


def lire_json(chemin: Path):
    return json.loads(chemin.read_text(encoding="utf-8"))


def ecrire_json(chemin: Path, donnees) -> None:
    from rappel.securite import ecrire_texte_atomique

    ecrire_texte_atomique(chemin, json.dumps(donnees, ensure_ascii=False, indent=2) + "\n")


def _maintenant() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(instant: datetime) -> str:
    return instant.isoformat().replace("+00:00", "Z")


def _lignes_initiales() -> tuple[list[str], list[dict]]:
    colonnes = list(SCENARIOS["schema_attendu"])
    lignes = []
    for indice in range(1, SCENARIOS["nb_lignes"] + 1):
        valeurs = {
            "integer": str((indice % 5) + 1),
            "decimal": f"{10 + (indice * 37) % 500}.{indice % 100:02d}",
            "text": f"client-{indice % 17:02d}",
        }
        ligne = {}
        for position, (nom, type_colonne) in enumerate(SCENARIOS["schema_attendu"].items()):
            # Premiere colonne entiere = identifiant ; les suivantes, des quantites.
            ligne[nom] = str(indice) if position == 0 else valeurs[type_colonne]
        lignes.append(ligne)
    return colonnes, lignes


def _ecrire_csv(chemin: Path, colonnes: list[str], lignes: list[dict]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", encoding="utf-8", newline="") as fichier:
        ecrivain = csv.DictWriter(fichier, fieldnames=colonnes, lineterminator="\n")
        ecrivain.writeheader()
        ecrivain.writerows(lignes)


def _lire_csv(chemin: Path) -> tuple[list[str], list[dict]]:
    with chemin.open(encoding="utf-8", newline="") as fichier:
        lecteur = csv.DictReader(fichier)
        return list(lecteur.fieldnames or []), list(lecteur)


def reset(dossier=None) -> dict:
    """Remet le pipeline dans son état sain (idempotent) et le relance : état vert."""
    fichiers = chemins(dossier)
    racine = fichiers["racine"]
    racine.mkdir(parents=True, exist_ok=True)
    for element in racine.iterdir():
        shutil.rmtree(element) if element.is_dir() else element.unlink()
    ecrire_json(fichiers["config"], {
        "composant": SCENARIOS["composant"], "table_sortie": SCENARIOS["table_sortie"],
        "schema_attendu": SCENARIOS["schema_attendu"], "seuils": SCENARIOS["seuils"],
    })
    ecrire_json(fichiers["mapping"], {"renommages": {}, "conversions": {}})
    ecrire_json(fichiers["environnement"], {**SCENARIOS["environnement_base"], "nb_timeouts": 0})
    colonnes, lignes = _lignes_initiales()
    _ecrire_csv(fichiers["entree"], colonnes, lignes)
    maintenant = _maintenant()
    ecrire_json(fichiers["commits"], [
        {"hash": commit["hash"], "message": commit["message"],
         "date": _iso(maintenant - timedelta(days=commit["jours"]))}
        for commit in SCENARIOS["commits_base"]
    ])
    ecrire_json(fichiers["scenario"], {"scenario": "reset"})
    return executer_pipeline(dossier)


def injecter(scenario: str, dossier=None) -> dict:
    """Part d'un état sain puis injecte la panne du scénario et relance le pipeline."""
    if scenario not in NOMS_SCENARIOS:
        raise ValueError(f"Scénario inconnu : utiliser {', '.join(NOMS_SCENARIOS)} ou reset.")
    reset(dossier)
    fichiers = chemins(dossier)
    definition = SCENARIOS["scenarios"][scenario]
    if definition["type"] == "renommage":
        colonnes, lignes = _lire_csv(fichiers["entree"])
        ancien, nouveau = definition["colonne"], definition["nouveau_nom"]
        colonnes = [nouveau if colonne == ancien else colonne for colonne in colonnes]
        lignes = [{(nouveau if cle == ancien else cle): valeur for cle, valeur in ligne.items()} for ligne in lignes]
        _ecrire_csv(fichiers["entree"], colonnes, lignes)
    elif definition["type"] == "changement_type":
        colonnes, lignes = _lire_csv(fichiers["entree"])
        for ligne in lignes:
            ligne[definition["colonne"]] = definition["format_texte"].format(valeur=ligne[definition["colonne"]])
        _ecrire_csv(fichiers["entree"], colonnes, lignes)
    elif definition["type"] == "externe":
        ecrire_json(fichiers["environnement"], {**definition["environnement"], "nb_timeouts": definition["nb_timeouts"]})
    if "commit" in definition:
        commits = lire_json(fichiers["commits"])
        commits.append({**definition["commit"], "date": _iso(_maintenant() - timedelta(minutes=10))})
        ecrire_json(fichiers["commits"], commits)
    ecrire_json(fichiers["scenario"], {"scenario": scenario, "description": definition["description"]})
    return executer_pipeline(dossier)


def convertir(valeur: str, type_cible: str) -> str:
    """Conversion tolérante appliquée par le mapping (retour à un type attendu)."""
    motif = {"integer": r"-?\d+", "decimal": r"-?\d+(?:\.\d+)?"}.get(type_cible)
    if motif is None:
        return valeur
    trouve = re.search(motif, valeur or "")
    return trouve.group(0) if trouve else valeur


def _valider(valeur: str, type_colonne: str, colonne: str):
    try:
        if type_colonne == "integer":
            return int(valeur)
        if type_colonne == "decimal":
            return float(Decimal(valeur))
    except (ValueError, InvalidOperation, TypeError):
        raise TypeError(f"{colonne} must be {type_colonne}, got text") from None
    return valeur


def lignes_transformees(dossier=None) -> tuple[list[str], list[dict]]:
    """Colonnes et lignes telles que la transformation les voit (mapping appliqué)."""
    fichiers = chemins(dossier)
    mapping = lire_json(fichiers["mapping"])
    colonnes, lignes = _lire_csv(fichiers["entree"])
    renommages, conversions = mapping.get("renommages", {}), mapping.get("conversions", {})
    colonnes = [renommages.get(colonne, colonne) for colonne in colonnes]
    resultat = []
    for brute in lignes:
        ligne = {renommages.get(cle, cle): valeur for cle, valeur in brute.items()}
        for colonne, type_cible in conversions.items():
            if colonne in ligne:
                ligne[colonne] = convertir(ligne[colonne], type_cible)
        resultat.append(ligne)
    return colonnes, resultat


def _type_infere(valeurs: list[str]) -> str:
    def tous(test):
        return bool(valeurs) and all(test(valeur) for valeur in valeurs)
    if tous(lambda valeur: re.fullmatch(r"-?\d+", valeur or "") is not None):
        return "integer"
    if tous(lambda valeur: re.fullmatch(r"-?\d+(?:\.\d+)?", valeur or "") is not None):
        return "decimal"
    return "text"


def schema_observe(dossier=None) -> list[str]:
    colonnes, lignes = lignes_transformees(dossier)
    return [f"{colonne}:{_type_infere([ligne.get(colonne, '') for ligne in lignes])}" for colonne in colonnes]


def schema_attendu(dossier=None) -> list[str]:
    config = lire_json(chemins(dossier)["config"])
    return [f"{nom}:{type_colonne}" for nom, type_colonne in config["schema_attendu"].items()]


def executer_pipeline(dossier=None) -> dict:
    """Transforme le CSV, écrit la table SQLite, les logs et l'état vert/rouge."""
    fichiers = chemins(dossier)
    config = lire_json(fichiers["config"])
    environnement = lire_json(fichiers["environnement"])
    composant, attendu, seuils = config["composant"], config["schema_attendu"], config["seuils"]
    horloge = [_maintenant()]
    journal = []

    def log(niveau, message):
        horloge[0] += timedelta(milliseconds=40)
        journal.append(f"{_iso(horloge[0])} {niveau} {message}")

    log("INFO", f"{composant}: pipeline start, source {SCENARIOS['table_entree']}")
    log("INFO", f"{composant}: expected schema {', '.join(f'{nom}:{typ}' for nom, typ in attendu.items())}")
    log("INFO", "scheduler: worker ready, slots=4")
    for tentative in range(1, environnement.get("nb_timeouts", 0) + 1):
        log("WARN", f"{composant}: TimeoutError: orders source request exceeded {environnement['latence_ms']} ms; retry {tentative}")
    _, lignes = lignes_transformees(dossier)
    acceptees, rejets = [], 0
    for indice, ligne in enumerate(lignes, start=1):
        identifiant = next(iter(ligne.values()), indice)
        try:
            acceptees.append({colonne: _valider(ligne[colonne], typ, colonne) for colonne, typ in attendu.items()})
        except KeyError as erreur:
            rejets += 1
            log("ERROR", f"{composant}: KeyError: {erreur} while transforming row {identifiant}")
        except TypeError as erreur:
            rejets += 1
            log("ERROR", f"{composant}: TypeError: {erreur} (row {identifiant})")
        if indice % 10 == 0:
            lot = indice // 10
            log("INFO", f"{composant}: batch {lot} read 10 rows from source in {80 + lot % 7 * 11} ms")
            log("INFO", f"{composant}: batch {lot} written to staging")
            log("INFO", f"scheduler: heartbeat ok seq={lot}")
            log("DEBUG", f"{composant}: cache hit ratio {60 + lot % 30} percent")
            if lot % 3 == 0:
                log("WARN", f"metrics_exporter: scrape timeout after {300 + lot * 7} ms; retry succeeded")
            if lot % 5 == 0:
                log("WARN", f"worker: memory usage {70 + lot % 9} percent; recovered after GC")
        if indice % 2 == 0:
            log("INFO", f"{composant}: row {identifiant} checked")
    disque = environnement["disque_libre_pct"]
    if disque < seuils["disque_min_pct"]:
        for numero in range(1, 6):
            log("WARN", f"worker: disk free {disque} percent below threshold {seuils['disque_min_pct']} (check {numero})")
    fichiers["sortie"].parent.mkdir(parents=True, exist_ok=True)
    base = sqlite3.connect(fichiers["sortie"])
    try:
        # Noms de table et colonnes issus de la configuration du pipeline, valeurs parametrees.
        with base:
            base.execute(f"DROP TABLE IF EXISTS {config['table_sortie']}")
            definition = ", ".join(f"{nom} {TYPES_SQL[typ]}" for nom, typ in attendu.items())
            base.execute(f"CREATE TABLE {config['table_sortie']} ({definition})")
            marqueurs = ", ".join("?" for _ in attendu)
            base.executemany(f"INSERT INTO {config['table_sortie']} VALUES ({marqueurs})",
                             [tuple(ligne[nom] for nom in attendu) for ligne in acceptees])
    finally:
        base.close()
    alertes = []
    if rejets:
        alertes.append(f"{rejets} ligne(s) rejetee(s) par la transformation")
    if environnement["latence_ms"] > seuils["latence_max_ms"]:
        alertes.append(f"latence source {environnement['latence_ms']} ms au-dessus de {seuils['latence_max_ms']} ms")
    if disque < seuils["disque_min_pct"]:
        alertes.append(f"disque libre {disque} % sous {seuils['disque_min_pct']} %")
    statut = "rouge" if alertes else "vert"
    if alertes:
        log("ALERT", f"pipeline {composant}: statut rouge : {'; '.join(alertes)}")
    log("INFO", f"{composant}: pipeline end status={statut} rows_ok={len(acceptees)} rows_rejected={rejets}")
    fichiers["logs"].parent.mkdir(parents=True, exist_ok=True)
    fichiers["logs"].write_text("\n".join(journal) + "\n", encoding="utf-8")
    etat = {
        "composant": composant, "statut": statut, "alertes": alertes,
        "lignes_lues": len(lignes), "lignes_traitees": len(acceptees), "lignes_rejetees": rejets,
        "latence_ms": environnement["latence_ms"], "disque_libre_pct": disque,
        "scenario": lire_json(fichiers["scenario"]).get("scenario"), "horodatage": _iso(horloge[0]),
    }
    ecrire_json(fichiers["etat"], etat)
    return etat


def lire_etat(dossier=None) -> dict:
    return lire_json(chemins(dossier)["etat"])


def empreinte_dossier(dossier=None) -> str:
    """SHA-256 de tous les fichiers du pipeline (hors sauvegardes) : preuve d'absence de modification."""
    racine = dossier_demo(dossier)
    condensat = hashlib.sha256()
    for chemin in sorted(racine.rglob("*")):
        relatif = chemin.relative_to(racine)
        if not chemin.is_file() or relatif.parts[0] == DOSSIER_SAUVEGARDES:
            continue
        condensat.update(relatif.as_posix().encode("utf-8") + b"\0")
        condensat.update(chemin.read_bytes() + b"\0")
    return condensat.hexdigest()
