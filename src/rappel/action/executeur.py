"""Exécuteur de correctifs sur le pipeline de démo UNIQUEMENT (cases B9-B11).

- refus (rayon d'impact) ou décision « refuser » : aucune modification, prouvée par empreinte SHA-256 ;
- validation sans décision « appliquer » : en attente, aucune modification ;
- auto, ou validation + « appliquer » : sauvegarde complète, application du patch de schéma
  (mapping de renommages et de conversions lu par la transformation), relance du pipeline ;
  s'il ne revient pas au vert, restauration à l'identique (même empreinte) et signalement.
Tout chemin hors du dossier configuré (PIPELINE_DEMO_DIR) est refusé.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from rappel.action.rayon_impact import REFUS, VALIDATION, expliquer_rayon_impact
from rappel.config import Configuration, charger
from rappel.pipeline_demo.pipeline import (
    DOSSIER_SAUVEGARDES, chemins, dossier_demo, ecrire_json, empreinte_dossier, executer_pipeline, lire_etat, lire_json,
)

EXECUTE, EN_ATTENTE, REFUSE, NON_APPLICABLE, ECHEC_RESTAURE = (
    "execute", "en_attente", "refuse", "non_applicable", "echec_restaure")


def _champ(correctif, nom):
    return correctif[nom] if isinstance(correctif, dict) else getattr(correctif, nom)


def _colonnes(schema) -> list[tuple[str, str]]:
    return [(nom.strip(), (type_colonne.strip() or "?")) for nom, _, type_colonne in
            (str(colonne).partition(":") for colonne in schema)]


def mapping_depuis_patch(patch) -> dict | None:
    """Renommages et conversions qui ramènent le schéma « avant » au schéma « apres »."""
    try:
        donnees = json.loads(patch) if isinstance(patch, str) else patch
        avant, apres = _colonnes(donnees["avant"]), _colonnes(donnees["apres"])
    except (ValueError, TypeError, KeyError):
        return None
    noms_avant, noms_apres = [nom for nom, _ in avant], [nom for nom, _ in apres]
    renommages, conversions = {}, {}
    for (nom_avant, type_avant), (nom_apres, type_apres) in zip(avant, apres):
        if nom_avant != nom_apres and nom_avant not in noms_apres and nom_apres not in noms_avant:
            renommages[nom_avant] = nom_apres
        if type_avant != type_apres and "?" not in (type_avant, type_apres):
            conversions[nom_apres] = type_apres
    return {"renommages": renommages, "conversions": conversions} if renommages or conversions else None


def _resultat(statut, niveau, raisons, avant, apres, message, pipeline=None, sauvegarde=None):
    return {"statut": statut, "niveau": niveau, "raisons": raisons, "empreinte_avant": avant,
            "empreinte_apres": apres, "inchange": avant == apres, "pipeline": pipeline,
            "sauvegarde": sauvegarde, "message": message, "simulation": True}


def liens_hors_dossier(racine: Path) -> list[str]:
    """Liens symboliques ou jonctions du pipeline : interdits (ils pourraient sortir du dossier isolé)."""
    trouves = []
    for chemin in [racine, *racine.rglob("*")]:
        est_jonction = getattr(chemin, "is_junction", lambda: False)()
        if chemin.is_symlink() or est_jonction or not chemin.resolve().is_relative_to(racine):
            trouves.append(chemin.relative_to(racine).as_posix() or ".")
    return trouves


def _copier_etat(source: Path, cible: Path) -> None:
    for element in source.iterdir():
        if element.name == DOSSIER_SAUVEGARDES:
            continue
        destination = cible / element.name
        # symlinks=True : un lien éventuel est copié comme lien, jamais suivi hors du dossier.
        shutil.copytree(element, destination, symlinks=True) if element.is_dir() else shutil.copy2(
            element, destination, follow_symlinks=False)


def _vider(racine: Path) -> None:
    for element in racine.iterdir():
        if element.name != DOSSIER_SAUVEGARDES:
            shutil.rmtree(element) if element.is_dir() else element.unlink()


def executer_correctif(correctif, decision: str | None = None, dossier=None,
                       configuration: Configuration | None = None, simuler_echec: bool = False) -> dict:
    configuration = configuration or charger()
    autorise = dossier_demo(None)
    racine = dossier_demo(dossier)
    if not (racine == autorise or racine.is_relative_to(autorise)) or not chemins(dossier)["config"].is_file():
        return _resultat(REFUSE, None, ["chemin hors du pipeline de démo"], None, None,
                         "Action refusée : seul le pipeline de démo isolé peut être modifié.")
    if liens_hors_dossier(racine):
        return _resultat(REFUSE, None, ["lien symbolique dans le pipeline de démo"], None, None,
                         "Action refusée : le pipeline de démo contient un lien symbolique ou une jonction.")
    avant = empreinte_dossier(dossier)
    niveau, raisons = expliquer_rayon_impact(correctif, configuration)
    if decision == "refuser" or niveau == REFUS:
        motif = "décision humaine : refuser" if decision == "refuser" else "rayon d'impact : refus"
        return _resultat(REFUSE, niveau, raisons, avant, empreinte_dossier(dossier), f"Aucune modification ({motif}).")
    if niveau == VALIDATION and decision != "appliquer":
        return _resultat(EN_ATTENTE, niveau, raisons, avant, empreinte_dossier(dossier),
                         "Validation humaine requise : aucune modification avant « appliquer ».")
    fichiers = chemins(dossier)
    config = lire_json(fichiers["config"])
    patch = _champ(correctif, "patch")
    mapping = mapping_depuis_patch(patch)
    composant = (json.loads(patch).get("composant") if isinstance(patch, str) and patch.strip().startswith("{")
                 else None)
    if mapping is None or (composant and composant != config["composant"]):
        return _resultat(NON_APPLICABLE, niveau, raisons, avant, empreinte_dossier(dossier),
                         "Correctif non applicable au pipeline de démo : aucune modification.")
    if lire_etat(dossier)["statut"] == "vert":
        return _resultat(NON_APPLICABLE, niveau, raisons, avant, empreinte_dossier(dossier),
                         "Pipeline déjà vert : aucune modification.")
    nom_sauvegarde = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    sauvegarde = racine / DOSSIER_SAUVEGARDES / nom_sauvegarde
    sauvegarde.mkdir(parents=True)
    _copier_etat(racine, sauvegarde)
    try:
        actuel = lire_json(fichiers["mapping"])
        actuel["renommages"] = {**actuel.get("renommages", {}), **mapping["renommages"]}
        actuel["conversions"] = {**actuel.get("conversions", {}), **mapping["conversions"]}
        ecrire_json(fichiers["mapping"], actuel)
        etat = executer_pipeline(dossier)
        reussi = etat["statut"] == "vert" and not simuler_echec
    except Exception:
        reussi = False
    if reussi:
        return _resultat(EXECUTE, niveau, raisons, avant, empreinte_dossier(dossier),
                         "Correctif appliqué au pipeline de démo : retour au vert vérifié.", "vert", nom_sauvegarde)
    _vider(racine)
    _copier_etat(sauvegarde, racine)
    apres = empreinte_dossier(dossier)
    return _resultat(ECHEC_RESTAURE, niveau, raisons, avant, apres,
                     "Échec : le pipeline n'est pas revenu au vert ; état restauré à l'identique et signalé.",
                     lire_etat(dossier)["statut"], nom_sauvegarde)
