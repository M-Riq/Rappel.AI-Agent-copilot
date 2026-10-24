"""Recherche de secrets dans tout l'historique git et dans l'arbre de travail.

N'affiche JAMAIS une valeur : seulement le type, le fichier et le commit. Vérifie aussi que
.env est ignoré par git et que les variables secrètes de .env.example sont vides.
Code de sortie 1 s'il y a la moindre trouvaille, 0 sinon.
"""

import argparse
from pathlib import Path
import re
import subprocess
import sys

RACINE = Path(__file__).resolve().parents[1]
TAILLE_MAX = 2_000_000
# Motifs construits par concatenation : aucun motif de cle litteral dans ce fichier.
NOMS_SECRETS = ("METERLESS_RUNTIME_TOKEN", "ZETARIS_API_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY",
                "NVIDIA_API_KEY", "NIGHTSHIFT_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
MOTIFS = (
    ("cle NVIDIA", re.compile("nv" + "api" + "-" + r"[A-Za-z0-9_-]{10,}")),
    ("cle sk-", re.compile(r"\bs" + r"k-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}")),
    ("jeton GitHub", re.compile(r"\bg" + r"h[pousr]_[A-Za-z0-9]{20,}")),
    ("cle Langfuse", re.compile(r"\b[ps]" + r"k-lf-[A-Za-z0-9_-]{8,}")),
    ("mot de passe DATABASE_URL", re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^:/\s@\"']+:([^@\s\"']+)@")),
    ("jeton Nightshift/Zetaris/Langfuse", re.compile(
        r"\b(?:" + "|".join(NOMS_SECRETS) + r")[ \t]*[=:][ \t]*[\"']?([^\s\"'#,)]+)")),
)


def est_substitut(valeur: str) -> bool:
    """Valeurs de documentation : variable ${...}, gabarit {...} ou <...>, masque ****.

    Les doubles de test du dépôt portent le mot « factice » : ils ne sont pas des secrets.
    """
    return (not valeur or valeur[0] in "${<*\\" or "{" in valeur or valeur.startswith("****")
            or len(valeur) < 8 or "factice" in valeur.lower())


def trouvailles_ligne(ligne: str) -> list[str]:
    types = []
    for nom, motif in MOTIFS:
        for correspondance in motif.finditer(ligne):
            valeur = correspondance.group(1) if correspondance.groups() else correspondance.group(0)
            if correspondance.groups() and est_substitut(valeur):
                continue
            types.append(nom)
    return types


def git(racine: Path, *arguments) -> str:
    return subprocess.run(["git", "-C", str(racine), *arguments], capture_output=True, check=True).stdout.decode(
        "utf-8", errors="replace")


def scanner_historique(racine: Path) -> list[dict]:
    resultats, commit, fichier = [], None, None
    for ligne in git(racine, "log", "-p", "--all", "--no-color", "--format=commit %H").splitlines():
        if ligne.startswith("commit ") and len(ligne) == 47:
            commit = ligne[7:14]
        elif ligne.startswith("+++ "):
            fichier = ligne[6:] if ligne.startswith("+++ b/") else None
        elif ligne.startswith("+") and fichier:
            for type_secret in trouvailles_ligne(ligne[1:]):
                resultats.append({"source": "historique", "commit": commit, "fichier": fichier, "type": type_secret})
    return resultats


def scanner_arbre(racine: Path) -> list[dict]:
    resultats = []
    chemins = [chemin for chemin in git(racine, "ls-files", "-co", "--exclude-standard", "-z").split("\0") if chemin]
    for relatif in chemins:
        chemin = racine / relatif
        if not chemin.is_file() or chemin.stat().st_size > TAILLE_MAX:
            continue
        contenu = chemin.read_bytes().decode("utf-8", errors="replace")
        for numero, ligne in enumerate(contenu.splitlines(), start=1):
            for type_secret in trouvailles_ligne(ligne):
                resultats.append({"source": "arbre", "commit": None, "fichier": f"{relatif}:{numero}", "type": type_secret})
    return resultats


def controler_configuration(racine: Path) -> list[str]:
    problemes = []
    ignore = racine / ".gitignore"
    if not ignore.is_file() or ".env" not in {ligne.strip() for ligne in ignore.read_text(encoding="utf-8").splitlines()}:
        problemes.append(".env absent de .gitignore")
    exemple = racine / ".env.example"
    if exemple.is_file():
        for numero, ligne in enumerate(exemple.read_text(encoding="utf-8").splitlines(), start=1):
            nom, separateur, valeur = ligne.partition("=")
            valeur = valeur.split(" #", 1)[0].strip()
            if (separateur and not ligne.lstrip().startswith("#")
                    and any(mot in nom.upper() for mot in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and valeur):
                problemes.append(f".env.example ligne {numero} : variable secrète non vide ({nom.strip()})")
    return problemes


def main(argv=None, racine: Path | None = None) -> int:
    parseur = argparse.ArgumentParser(description="Recherche de secrets (historique git complet + arbre de travail).")
    parseur.add_argument("--racine", type=Path, default=None)
    arguments = parseur.parse_args(argv)
    racine = (racine or arguments.racine or RACINE).resolve()
    try:
        trouvailles = scanner_historique(racine) + scanner_arbre(racine)
    except (OSError, subprocess.CalledProcessError):
        print("Analyse impossible : git est-il installé et le dossier est-il un dépôt ?")
        return 2
    problemes = controler_configuration(racine)
    uniques = {(element["source"], element["commit"], element["fichier"], element["type"]) for element in trouvailles}
    for source, commit, fichier, type_secret in sorted(uniques, key=str):
        print(f"SECRET POSSIBLE ({type_secret}) : {fichier}" + (f" dans le commit {commit}" if commit else "")
              + " ; valeur non affichée")
    for probleme in problemes:
        print(f"CONFIGURATION : {probleme}")
    if not uniques and not problemes:
        print("Aucun secret détecté (historique complet et arbre de travail) ; .env ignoré ; .env.example sans valeur secrète.")
        print("Analyse heuristique : elle ne prouve pas l'absence de tout secret.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
