import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import sys

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "src"))

from dotenv import load_dotenv

from scripts._essai_commun import afficher
from scripts.demo_enquete import controle_sante, jouer_demo
from rappel.enquete.adaptateurs import LLMFaux


# Prefixes construits par concatenation : aucun motif de cle litteral dans le depot.
MOTIFS_CLES = (
    re.compile("(?:" + "nvapi" + "-|" + "sk" + "-ant-)[A-Za-z0-9_-]{12,}"),
    re.compile(r"\b[A-Za-z0-9_]*(?:KEY|TOKEN)\s*=\s*[\"']?[A-Za-z0-9_-]{24,}", re.IGNORECASE),
)
MODES = {"LLM_PROVIDER": {"fake", "meterless"}, "SOURCE_PROVIDER": {"fichier", "zetaris"},
         "MEMOIRE_PROVIDER": {"json", "postgres"}}
MOTS_SECRETS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


@contextmanager
def modes_locaux():
    """Demo a blanc : modes fake/fichier/json imposes, environnement restaure ensuite."""
    anciens = {nom: os.environ.get(nom) for nom in MODES}
    os.environ.update({"LLM_PROVIDER": "fake", "SOURCE_PROVIDER": "fichier", "MEMOIRE_PROVIDER": "json"})
    try:
        yield
    finally:
        for nom, valeur in anciens.items():
            if valeur is None:
                os.environ.pop(nom, None)
            else:
                os.environ[nom] = valeur


def etat_variable(nom):
    if nom not in os.environ:
        return "absent"
    return "present" if os.environ[nom].strip() else "vide"


def afficher_rubrique(nom, variables):
    afficher(f"\n{nom} :")
    manquantes = []
    for variable in variables:
        etat = etat_variable(variable)
        afficher(f"{variable} : {etat}")
        if etat != "present":
            manquantes.append(variable)
    afficher("pret (configuration seulement)" if not manquantes else "a fournir : " + ", ".join(manquantes))
    return manquantes


def examiner_fichiers(racine, chemins):
    trouvailles = []
    for chemin in chemins:
        destination = (racine / chemin).resolve()
        if not destination.is_relative_to(racine.resolve()):
            raise ValueError("Chemin suivi hors depot")
        if not destination.is_file():
            continue
        contenu = destination.read_bytes().decode("utf-8", errors="replace")
        for numero, ligne in enumerate(contenu.splitlines(), start=1):
            if any(motif.search(ligne) for motif in MOTIFS_CLES):
                trouvailles.append({"fichier": chemin, "ligne": numero})
    return trouvailles


def controle_depot(racine=RACINE, lancer_git=None):
    racine = Path(racine)
    lancer_git = subprocess.run if lancer_git is None else lancer_git

    def git(*arguments):
        return lancer_git(["git", *arguments], cwd=racine, capture_output=True, check=False)

    try:
        liste = git("ls-files", "-z")
        ignore = git("check-ignore", "-q", "--", ".env")
        suivi = git("ls-files", "-z", "--", ".env")
        if liste.returncode != 0 or suivi.returncode != 0 or ignore.returncode not in {0, 1}:
            raise RuntimeError("Controle Git indisponible")
        chemins = [chemin for chemin in liste.stdout.decode("utf-8").split("\0") if chemin]
        exemple = racine / ".env.example"
        exemple_vide = exemple.is_file()
        if exemple_vide:
            for ligne in exemple.read_text(encoding="utf-8").splitlines():
                ligne = ligne.strip()
                if not ligne or ligne.startswith("#"):
                    continue
                nom, separateur, valeur = ligne.partition("=")
                valeur = valeur.split(" #", 1)[0].strip()
                # Les modes et adresses locales ont des defauts ; les secrets doivent rester vides.
                secret = any(mot in nom.upper() for mot in MOTS_SECRETS)
                if not separateur or not nom.strip() or (secret and valeur):
                    exemple_vide = False
                    break
        trouvailles = examiner_fichiers(racine, chemins)
        rapport = {"env_ignore": ignore.returncode == 0, "env_non_suivi": not suivi.stdout,
                   "exemple_vide": exemple_vide, "trouvailles": trouvailles, "erreur": False}
        rapport["ok"] = rapport["env_ignore"] and rapport["env_non_suivi"] and exemple_vide and not trouvailles
        return rapport
    except Exception:
        return {"ok": False, "erreur": True, "trouvailles": []}


def verifier(racine=RACINE, lancer_git=None, demo=None):
    load_dotenv(dotenv_path=Path(racine) / ".env", override=False)
    afficher_rubrique("Meterless Nightshift", ("METERLESS_BASE_URL", "METERLESS_RUNTIME_TOKEN",
                                               "METERLESS_MODEL_RAPIDE", "METERLESS_MODEL_PROFOND"))
    afficher("Ensuite : python scripts/check_meterless.py (appel reel, modele effectif par voie).")
    afficher_rubrique("Zetaris", ("ZETARIS_BASE_URL", "ZETARIS_API_KEY", "ZETARIS_ORG_ID"))
    afficher_rubrique("Langfuse", ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"))
    afficher("Langfuse : liaison au SDK a verifier ; configuration presente ne garantit pas une trace distante.")
    afficher("\nModes (rappel.config.Configuration) :")
    for nom, valeurs in MODES.items():
        valeur = os.getenv(nom, "").strip().lower()
        reconnu = valeur in valeurs or not valeur
        objet = "fournisseur" if nom == "LLM_PROVIDER" else "mode"
        afficher(f"{nom} : {etat_variable(nom)} ; " + ("reconnu" if reconnu else f"ERREUR : {objet} non reconnu"))
    source = os.getenv("SOURCE_PROVIDER", "").strip().lower() or "fichier"
    if source == "fichier":
        afficher("Preuves SQL simulees (SQLite construit depuis le contexte), jamais presentees comme reelles.")
    afficher("\nSecurite du depot :")
    securite = controle_depot(racine, lancer_git)
    if securite["erreur"]:
        afficher("ERREUR : controle du depot impossible, aucun detail sensible affiche.")
    else:
        for nom, cle in ((".env ignore par Git", "env_ignore"), (".env non suivi", "env_non_suivi"),
                         (".env.example sans secret", "exemple_vide")):
            afficher(f"{nom} : {'OK' if securite[cle] else 'ERREUR'}")
        for trouvaille in securite["trouvailles"]:
            afficher(f"Motif suspect : {trouvaille['fichier']}:{trouvaille['ligne']} (valeur non affichee)")
        if not securite["trouvailles"]:
            afficher("Recherche dans les fichiers suivis : aucun motif suspect")
        afficher("Cette recherche heuristique n'est pas une preuve d'absence de tout secret.")
    afficher("\nDemo a blanc : LLMFaux impose, aucun appel reseau.")
    try:
        with modes_locaux():
            resultats = (demo or jouer_demo)(LLMFaux())
        code_demo, raisons = controle_sante(resultats)
        afficher("Demo a blanc : OK" if code_demo == 0 else "Demo a blanc : ECHEC")
        for raison in raisons:
            afficher(raison)
    except Exception:
        code_demo = 1
        afficher("Demo a blanc : ECHEC, aucun detail sensible affiche.")
    code = 0 if securite["ok"] and code_demo == 0 else 1
    afficher("Etat global : OK (cles absentes a fournir)" if code == 0 else "Etat global : ECHEC securite ou demo")
    return code


def main(argv=None, racine=RACINE, lancer_git=None, demo=None):
    parseur = argparse.ArgumentParser(description="Controle local de preparation sans divulgation ni appel fournisseur reel.")
    parseur.parse_args(argv)
    return verifier(racine, lancer_git, demo)


if __name__ == "__main__":
    raise SystemExit(main())