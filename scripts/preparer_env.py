"""Propose un fichier .env à partir des valeurs détectées (Nightshift, Zetaris), sans afficher de secret.

Part de .env.example et remplace les valeurs connues :
- data/detection_nightshift.json (scripts/detecter_nightshift.py) : METERLESS_BASE_URL et voies ;
- data/decouverte_zetaris.json (scripts/decouvrir_zetaris.py) : ZETARIS_CHEMIN_SQL, ZETARIS_CLE_SQL ;
- jetons : --fichier-jeton (Nightshift) et variables d'environnement déjà définies (jamais affichés).
N'écrase jamais un .env existant sans --forcer ; --simuler affiche seulement la proposition masquée.
Le fichier écrit a des droits restreints quand le système le permet (0600).

  python scripts/preparer_env.py [--fichier-jeton chemin] [--simuler] [--forcer]
"""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "src"))

from scripts._essai_commun import afficher

SECRETS = ("METERLESS_RUNTIME_TOKEN", "ZETARIS_API_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY", "DATABASE_URL")
REPRISES_ENVIRONNEMENT = ("METERLESS_RUNTIME_TOKEN", "ZETARIS_API_KEY", "ZETARIS_BASE_URL", "ZETARIS_ORG_ID",
                          "DATABASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")


def lire_json(chemin: Path) -> dict:
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return donnees if isinstance(donnees, dict) else {}


def valeurs_detectees(detection: dict, decouverte: dict, jeton: str) -> dict:
    valeurs = {nom: os.environ[nom] for nom in REPRISES_ENVIRONNEMENT if os.environ.get(nom)}
    proposition = detection.get("proposition") or {}
    if proposition.get("trouve"):
        for cle in ("METERLESS_BASE_URL", "METERLESS_MODEL_RAPIDE", "METERLESS_MODEL_PROFOND"):
            valeurs[cle] = proposition[cle]
        valeurs["LLM_PROVIDER"] = "meterless"
    if jeton:
        valeurs["METERLESS_RUNTIME_TOKEN"] = jeton
    valeurs.update({cle: valeur for cle, valeur in (decouverte.get("proposition") or {}).items() if valeur})
    if (decouverte.get("select_1") or {}).get("statut") == "REUSSI":
        valeurs["SOURCE_PROVIDER"] = "zetaris"
    return valeurs


def composer(modele: str, valeurs: dict) -> str:
    """Le modèle .env.example avec les valeurs remplacées ; les clés absentes du modèle sont ajoutées."""
    vues, lignes = set(), []
    for ligne in modele.splitlines():
        trouve = re.match(r"^([A-Z][A-Z0-9_]*)=", ligne)
        if trouve and trouve.group(1) in valeurs:
            cle = trouve.group(1)
            vues.add(cle)
            ligne = f"{cle}={valeurs[cle]}"
        lignes.append(ligne)
    manquantes = [cle for cle in valeurs if cle not in vues]
    if manquantes:
        lignes.append("# Ajouté par scripts/preparer_env.py")
        lignes.extend(f"{cle}={valeurs[cle]}" for cle in manquantes)
    return "\n".join(lignes) + "\n"


def masquer(contenu: str) -> str:
    def remplacer(trouve):
        cle, valeur = trouve.group(1), trouve.group(2)
        return f"{cle}={'[MASQUE]' if valeur and cle in SECRETS else valeur}"

    return re.sub(r"^([A-Z][A-Z0-9_]*)=(.*)$", remplacer, contenu, flags=re.MULTILINE)


def ecrire_restreint(chemin: Path, contenu: str) -> bool:
    """Écrit le fichier puis restreint ses droits (0600) ; False si le système ne le permet pas."""
    descripteur = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descripteur, "w", encoding="utf-8", newline="\n") as fichier:
        fichier.write(contenu)
    try:
        os.chmod(chemin, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        return False
    return os.name != "nt"


def main(argv=None) -> int:
    parseur = argparse.ArgumentParser(description="Propose un .env à partir des valeurs détectées (aucun secret affiché).")
    parseur.add_argument("--fichier-jeton", help="Fichier proxy-token du jeton Runtime Nightshift")
    parseur.add_argument("--detection", type=Path, default=RACINE / "data" / "detection_nightshift.json")
    parseur.add_argument("--decouverte", type=Path, default=RACINE / "data" / "decouverte_zetaris.json")
    parseur.add_argument("--modele", type=Path, default=RACINE / ".env.example")
    parseur.add_argument("--sortie", type=Path, default=RACINE / ".env")
    parseur.add_argument("--simuler", action="store_true", help="Afficher la proposition masquée sans écrire")
    parseur.add_argument("--forcer", action="store_true", help="Remplacer un .env existant")
    arguments = parseur.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    jeton = ""
    if arguments.fichier_jeton:
        try:
            jeton = Path(arguments.fichier_jeton).read_text(encoding="utf-8").strip()
        except OSError:
            afficher("Fichier de jeton illisible : vérifier --fichier-jeton.")
            return 2
    valeurs = valeurs_detectees(lire_json(arguments.detection), lire_json(arguments.decouverte), jeton)
    contenu = composer(arguments.modele.read_text(encoding="utf-8"), valeurs)
    afficher("Proposition (secrets masqués) :")
    for ligne in masquer(contenu).splitlines():
        if re.match(r"^[A-Z][A-Z0-9_]*=", ligne) and ligne.split("=", 1)[0] in {*valeurs, "LLM_PROVIDER", "SOURCE_PROVIDER"}:
            afficher("  " + ligne.replace(jeton, "[MASQUE]") if jeton else "  " + ligne)
    if arguments.simuler:
        return 0
    if arguments.sortie.exists() and not arguments.forcer:
        afficher(f"{arguments.sortie.name} existe déjà : rien n'est écrasé (relancer avec --forcer pour le remplacer).")
        return 2
    restreint = ecrire_restreint(arguments.sortie, contenu)
    ignore = ".env" in (RACINE / ".gitignore").read_text(encoding="utf-8").split()
    afficher(f"{arguments.sortie.name} écrit" + (" (droits 0600)" if restreint else
             " (droits restreints non garantis sous Windows : dossier utilisateur uniquement)")
             + ("" if ignore else " ; ATTENTION : .env n'est pas dans .gitignore"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
