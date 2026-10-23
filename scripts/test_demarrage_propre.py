"""Vérifie le parcours « Démarrage rapide » sur un clone propre, sans réseau.

1. Lit le bloc bash de la section « ## Démarrage rapide » (README.md, sinon
   docs/PROPOSITION-README.md).
2. Clone le dépôt local (dernier commit) dans un dossier temporaire et crée un venv vide
   avec l'interpréteur courant ; les paquets de cet interpréteur sont rendus visibles par un
   fichier .pth (aucune installation réseau : `pip install` est remplacé par un contrôle
   d'importabilité de chaque paquet de requirements.txt).
3. Exécute les autres commandes dans le clone (mode simulé, .env copié de .env.example),
   démarre l'API, enchaîne P1 -> P2 -> P3 via /incidents/depuis-demo et mesure la durée totale
   (objectif < 15 min). Rapport : data/demarrage_propre.json.
"""

import argparse
from importlib.metadata import PackageNotFoundError, version as version_installee
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import requests

RACINE = Path(__file__).resolve().parents[1]
REUSSI, ECHOUE, NON_MESURE = "REUSSI", "ECHOUE", "NON_MESURE"
DUREE_MAX_S = 15 * 60
DELAI_COMMANDE_S = 600
# Nom de distribution -> module importé, quand ils diffèrent.
MODULES = {"python-dotenv": "dotenv", "rfc3339-validator": "rfc3339_validator", "psycopg[binary]": "psycopg"}
# Variables de l'environnement courant qui fausseraient un démarrage « propre ».
PREFIXES_ENV_RETIRES = ("LLM_", "METERLESS_", "NIGHTSHIFT_", "ZETARIS_", "SOURCE_", "MEMOIRE_", "DATABASE_",
                        "LANGFUSE_", "PIPELINE_", "ACTION_", "RAYON_", "SEUIL_", "MAX_TOURS", "API_URL",
                        "WAR_ROOM", "PYTHONPATH", "VIRTUAL_ENV")


def lire_bloc_demarrage(textes: list[str]) -> tuple[list[str], int]:
    """Commandes du premier bloc ```bash de la section « Démarrage rapide » et indice du texte."""
    for indice, texte in enumerate(textes):
        section = re.search(r"^## Démarrage rapide\s*$(.*?)(?=^## |\Z)", texte, re.M | re.S)
        if not section:
            continue
        bloc = re.search(r"```(?:bash|sh)?\n(.*?)```", section.group(1), re.S)
        if bloc:
            commandes = [ligne.split(" #")[0].strip() for ligne in bloc.group(1).splitlines()]
            return [commande for commande in commandes if commande and not commande.startswith("#")], indice
    return [], -1


def decouper(commandes: list[str]) -> list[str]:
    return [partie.strip() for commande in commandes for partie in commande.split("&&") if partie.strip()]


def classer(commande: str) -> str:
    mots = commande.split()
    if mots[:2] == ["git", "clone"]:
        return "clone"
    if mots[0] == "cd":
        return "cd"
    if "venv" in mots and "-m" in mots:
        return "venv"
    if mots[0] in ("source", ".") or commande.endswith("activate"):
        return "activer"
    if mots[0] == "pip" or mots[1:3] == ["-m", "pip"]:
        return "pip"
    if mots[0] in ("cp", "copy"):
        return "copie"
    if mots[0] == "uvicorn":
        return "api"
    if mots[0] == "pytest":
        return "pytest"
    if mots[0] == "python":
        return "python"
    if mots[0] == "streamlit":
        return "interface"
    return "inconnue"


def paquets_requis(fichier: Path) -> list[str]:
    paquets = []
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        ligne = ligne.split("#")[0].strip()
        if ligne and not ligne.startswith("-"):
            paquets.append(re.split(r"[=<>!~ ]", ligne, maxsplit=1)[0])
    return paquets


def env_propre(venv: Path) -> dict:
    env = {cle: valeur for cle, valeur in os.environ.items() if not cle.upper().startswith(PREFIXES_ENV_RETIRES)}
    scripts = venv / ("Scripts" if os.name == "nt" else "bin")
    env["PATH"] = str(scripts) + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(venv)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def python_venv(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def creer_venv(venv: Path) -> None:
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, capture_output=True)
    site = subprocess.run([str(python_venv(venv)), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                          check=True, capture_output=True, text=True).stdout.strip()
    import site as site_courant
    chemins = [chemin for chemin in site_courant.getsitepackages() if chemin.endswith("site-packages")]
    Path(site, "paquets_hors_ligne.pth").write_text("\n".join(chemins) + "\n", encoding="utf-8")


def port_libre() -> int:
    with socket.socket() as sonde:
        sonde.bind(("127.0.0.1", 0))
        return sonde.getsockname()[1]


def executer(argv, clone, env, delai=DELAI_COMMANDE_S):
    debut = time.perf_counter()
    try:
        resultat = subprocess.run(argv, cwd=clone, env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=delai)
        code, sortie = resultat.returncode, (resultat.stdout + resultat.stderr).strip()
    except subprocess.TimeoutExpired:
        code, sortie = -1, f"délai dépassé ({delai} s)"
    derniere = [ligne for ligne in sortie.splitlines() if ligne.strip() and "UserWarning" not in ligne]
    return code, round(time.perf_counter() - debut, 1), (derniere[-1] if derniere else "")[:200]


def enchainer_p1_p2_p3(url, python, clone, env):
    """Injecte chaque panne puis crée l'incident par l'API ; attend la fin de chaque enquête."""
    resultats = {}
    for scenario in ("p1", "p2", "p3"):
        code, _, detail = executer([str(python), "scripts/injecteur.py", scenario], clone, env, 60)
        if code != 0:
            return False, f"injection {scenario} impossible : {detail}"
        identifiant = f"propre-{scenario}"
        reponse = requests.post(f"{url}/incidents/depuis-demo", json={"incident_id": identifiant}, timeout=10)
        if reponse.status_code != 201:
            return False, f"{scenario} : création refusée ({reponse.status_code})"
        for _ in range(120):
            incident = requests.get(f"{url}/incidents/{identifiant}", timeout=10).json()
            if incident.get("statut") in ("termine", "erreur"):
                break
            time.sleep(0.5)
        resultats[scenario] = incident
    correctif_p1 = (resultats["p1"].get("correctif") or {}).get("justification", "")
    p1_ok = "colonne renommée" in correctif_p1
    p2_ok = resultats["p2"].get("correctif") is not None
    p3_ok = (resultats["p3"].get("decision_abstention") or {}).get("abstient") is True
    comparaison = requests.get(f"{url}/comparaison", params={"a": "propre-p1", "b": "propre-p2"}, timeout=10).json()
    regle = comparaison.get("regle_60pc", {})

    def oui(valeur):
        return "oui" if valeur else "non"

    detail = (f"P1 « colonne renommée » : {oui(p1_ok)} ; P2 correctif : {oui(p2_ok)} ; "
              f"P3 abstention : {oui(p3_ok)} ; règle des 60 % : {oui(regle.get('respecte'))} (simulé)")
    return p1_ok and p2_ok and p3_ok and bool(regle.get("respecte")), detail


def lancer_api(commande, python, clone, env):
    port = port_libre()
    argv = [str(python), "-m"] + commande.split() + ["--port", str(port)]
    processus = subprocess.Popen(argv, cwd=clone, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if requests.get(f"{url}/healthz", timeout=2).status_code == 200:
                return processus, url
        except requests.RequestException:
            pass
        if processus.poll() is not None:
            break
        time.sleep(0.5)
    processus.terminate()
    return None, url


def parcourir(commandes, source, depot=RACINE, garder=False):
    lignes = []

    def noter(commande, statut, duree, detail):
        lignes.append({"commande": commande, "statut": statut, "duree_s": duree, "detail": detail})

    debut = time.perf_counter()
    temporaire = Path(tempfile.mkdtemp(prefix="rappel-propre-"))
    clone, venv = temporaire / "rappel", temporaire / "rappel" / ".venv"
    try:
        for commande in decouper(commandes):
            nature = classer(commande)
            t0 = time.perf_counter()
            if nature == "clone":
                resultat = subprocess.run(["git", "clone", "--quiet", str(depot), str(clone)], capture_output=True, text=True)
                noter(commande, REUSSI if resultat.returncode == 0 else ECHOUE, round(time.perf_counter() - t0, 1),
                      "clone du dépôt local (dernier commit) à la place de l'URL")
                if resultat.returncode != 0:
                    break
            elif nature == "cd":
                noter(commande, REUSSI, 0.0, "dossier du clone")
            elif nature == "venv":
                creer_venv(venv)
                noter(commande, REUSSI, round(time.perf_counter() - t0, 1),
                      "venv sans pip ; paquets de l'interpréteur courant via .pth (hors ligne)")
            elif nature == "activer":
                noter(commande, REUSSI, 0.0, "remplacé par l'appel direct au python du venv")
            elif nature == "pip":
                fichier = clone / commande.split("-r", 1)[1].strip().split()[0]
                manquants, versions = [], []
                for paquet in paquets_requis(fichier):
                    module = MODULES.get(paquet, paquet.replace("-", "_"))
                    code = subprocess.run([str(python_venv(venv)), "-c", f"import {module}"],
                                          capture_output=True, cwd=clone).returncode
                    if code != 0:
                        manquants.append(paquet)
                    else:
                        try:
                            versions.append(f"{paquet} {version_installee(paquet.split('[')[0])}")
                        except PackageNotFoundError:
                            pass
                statut = NON_MESURE if manquants else REUSSI
                detail = ("installation réseau non testée ; "
                          + (f"absents ici : {', '.join(manquants)}" if manquants else "tous les paquets importables"))
                noter(commande, statut, round(time.perf_counter() - t0, 1), detail)
            elif nature == "copie":
                shutil.copyfile(clone / ".env.example", clone / ".env")
                noter(commande, REUSSI, 0.0, ".env créé depuis .env.example (mode simulé)")
            elif nature in ("pytest", "python"):
                argv = [str(python_venv(venv))] + (["-m"] if nature == "pytest" else []) + commande.split()[(0 if nature == "pytest" else 1):]
                code, duree, detail = executer(argv, clone, env_propre(venv))
                noter(commande, REUSSI if code == 0 else ECHOUE, duree, detail)
            elif nature == "api":
                processus, url = lancer_api(commande, python_venv(venv), clone, env_propre(venv))
                if processus is None:
                    noter(commande, ECHOUE, round(time.perf_counter() - t0, 1), "API non joignable")
                    continue
                try:
                    ok, detail = enchainer_p1_p2_p3(url, python_venv(venv), clone, env_propre(venv))
                finally:
                    processus.terminate()
                    processus.wait(timeout=20)
                noter(commande, REUSSI if ok else ECHOUE, round(time.perf_counter() - t0, 1), "API + P1 -> P2 -> P3 : " + detail)
            else:
                noter(commande, NON_MESURE, 0.0, f"commande non automatisée ({nature})")
    finally:
        if not garder:
            shutil.rmtree(temporaire, ignore_errors=True)
    duree = round(time.perf_counter() - debut, 1)
    noter("durée totale", REUSSI if duree < DUREE_MAX_S else ECHOUE, duree, f"objectif < {DUREE_MAX_S // 60} min")
    return {"source": source, "lignes": lignes, "duree_totale_s": duree,
            "echecs": [ligne["commande"] for ligne in lignes if ligne["statut"] == ECHOUE]}


def main(argv=None):
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--json", type=Path, default=RACINE / "data" / "demarrage_propre.json")
    parseur.add_argument("--garder", action="store_true", help="Ne pas supprimer le clone temporaire")
    arguments = parseur.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    fichiers = [RACINE / "README.md", RACINE / "docs" / "PROPOSITION-README.md"]
    commandes, indice = lire_bloc_demarrage([f.read_text(encoding="utf-8") if f.exists() else "" for f in fichiers])
    if not commandes:
        print("Section « ## Démarrage rapide » introuvable dans README.md et docs/PROPOSITION-README.md.")
        return 1
    source = fichiers[indice].relative_to(RACINE).as_posix()
    print(f"Parcours lu dans {source} ({len(commandes)} commande(s)) ; clone propre, sans réseau.")
    rapport = parcourir(commandes, source, garder=arguments.garder)
    print("Commande | Statut | Durée (s) | Détail")
    for ligne in rapport["lignes"]:
        print(f"{ligne['commande']} | {ligne['statut']} | {ligne['duree_s']} | {ligne['detail']}")
    arguments.json.parent.mkdir(parents=True, exist_ok=True)
    arguments.json.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Bilan : " + ("aucun ECHOUE" if not rapport["echecs"] else "ECHOUE sur " + ", ".join(rapport["echecs"])))
    return 0 if not rapport["echecs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
