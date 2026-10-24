"""Détection de Meterless Nightshift sur ce poste (lecture seule, aucun appel facturé).

Essaie 127.0.0.1, localhost et [::1] sur les ports 8088, 8090, 8787 et 11434 ; pour chacun appelle
GET /v1/models (jamais /healthz seul) et classe : « en ligne » (200 avec des identifiants
meterless/*), « non autorisé » (401/403 : jeton requis ou refusé), « hors ligne », ou « autre
service ». Propose METERLESS_BASE_URL, METERLESS_MODEL_RAPIDE et METERLESS_MODEL_PROFOND parmi les
identifiants vus. Le jeton (--fichier-jeton ou METERLESS_RUNTIME_TOKEN) n'est jamais affiché.

  python scripts/detecter_nightshift.py [--fichier-jeton chemin] [--ports 8088,8090] [--json data/...]
"""

import argparse
import json
import os
from pathlib import Path
import sys

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "src"))

import requests

from scripts._essai_commun import afficher, ecrire_rapport, executer_main, nettoyer

HOTES = ("127.0.0.1", "localhost", "[::1]")
PORTS = (8088, 8090, 8787, 11434)
EN_LIGNE, NON_AUTORISE, HORS_LIGNE, AUTRE = "en ligne", "non autorisé", "hors ligne", "autre service"
MOTS_RAPIDES = ("fast", "rapide", "lightning", "nano", "mini", "flash")
MOTS_PROFONDS = ("deep", "profond", "super", "ultra", "reasoning")
CONSEIL_NEMOTRON = (
    "Aucun identifiant ne garantit Nemotron : les voies meterless/* sont routées par Nightshift, qui peut "
    "choisir un autre modèle. Dans la console Nightshift, imposer un modèle Nemotron (clé NVIDIA) pour la voie "
    "rapide et la voie profonde, puis vérifier A14 : python scripts/recette_reelle.py --confirmer."
)


def lire_jeton(fichier: str | None) -> str:
    """Jeton du fichier proxy-token (s'il est donné), sinon METERLESS_RUNTIME_TOKEN ; jamais affiché."""
    if fichier:
        try:
            return Path(fichier).read_text(encoding="utf-8").strip()
        except OSError:
            raise SystemExit("Fichier de jeton illisible : vérifier le chemin passé à --fichier-jeton.") from None
    return os.environ.get("METERLESS_RUNTIME_TOKEN", "").strip()


def sonder(hote: str, port: int, jeton: str, delai: float) -> dict:
    base = f"http://{hote}:{port}/v1"
    entetes = {"Authorization": "Be" "arer " + jeton} if jeton else {}
    resultat = {"base": base, "hote": hote, "port": port, "etat": HORS_LIGNE, "statut": None, "ids": [], "conseil": ""}
    try:
        reponse = requests.get(f"{base}/models", headers=entetes, timeout=(min(delai, 2.0), delai), allow_redirects=False)
    except requests.RequestException:
        resultat["conseil"] = "rien n'écoute ici"
        return resultat
    resultat["statut"] = reponse.status_code
    if reponse.status_code in (401, 403):
        resultat["etat"] = NON_AUTORISE
        resultat["conseil"] = ("jeton requis : --fichier-jeton ou METERLESS_RUNTIME_TOKEN" if not jeton else
                               "jeton refusé : utiliser le jeton Runtime de la console (pas rt-local, pas la clé NVIDIA)")
        return resultat
    try:
        donnees = reponse.json()
        ids = [str(modele["id"]) for modele in donnees.get("data", []) if isinstance(modele, dict) and "id" in modele]
    except (ValueError, AttributeError, TypeError):
        ids = []
    resultat["ids"] = ids
    if reponse.status_code == 200 and any(ident.startswith("meterless/") for ident in ids):
        resultat["etat"] = EN_LIGNE
    else:
        resultat["etat"] = AUTRE
        resultat["conseil"] = "service OpenAI-compatible sans voie meterless/* (Ollama ?) : ce n'est pas Nightshift"
    return resultat


def detecter(hotes=HOTES, ports=PORTS, jeton="", delai=2.0) -> list[dict]:
    return [sonder(hote, port, jeton, delai) for port in ports for hote in hotes]


def _choisir(ids: list[str], mots: tuple[str, ...], defaut: str) -> str | None:
    for filtre in (lambda i: "nemotron" in i.lower() and any(m in i.lower() for m in mots),
                   lambda i: any(m in i.lower() for m in mots), lambda i: i == defaut):
        choix = [ident for ident in ids if filtre(ident)]
        if choix:
            return sorted(choix)[0]
    return None


def proposer(resultats: list[dict]) -> dict:
    en_ligne = [resultat for resultat in resultats if resultat["etat"] == EN_LIGNE]
    if not en_ligne:
        return {"trouve": False}
    choix = en_ligne[0]
    ids = choix["ids"]
    return {
        "trouve": True, "METERLESS_BASE_URL": choix["base"],
        "METERLESS_MODEL_RAPIDE": _choisir(ids, MOTS_RAPIDES, "meterless/fast") or "meterless/auto",
        "METERLESS_MODEL_PROFOND": _choisir(ids, MOTS_PROFONDS, "meterless/deep") or "meterless/auto",
        "nemotron_garanti": any("nemotron" in ident.lower() for ident in ids),
    }


def main(argv=None) -> int:
    parseur = argparse.ArgumentParser(description="Détection de Meterless Nightshift (GET /v1/models, lecture seule).")
    parseur.add_argument("--fichier-jeton", help="Fichier proxy-token contenant le jeton Runtime (jamais affiché)")
    parseur.add_argument("--hotes", default=",".join(HOTES), help="Hôtes séparés par des virgules")
    parseur.add_argument("--ports", default=",".join(map(str, PORTS)), help="Ports séparés par des virgules")
    parseur.add_argument("--delai", type=float, default=2.0, help="Délai par sonde (s)")
    parseur.add_argument("--json", type=Path, default=RACINE / "data" / "detection_nightshift.json")
    arguments = parseur.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    try:
        ports = [int(port) for port in arguments.ports.split(",") if port.strip()]
    except ValueError:
        afficher("--ports : entiers séparés par des virgules.")
        return 2
    hotes = [hote.strip() for hote in arguments.hotes.split(",") if hote.strip()]
    jeton = lire_jeton(arguments.fichier_jeton)
    afficher(f"Jeton : {'fourni (masqué)' if jeton else 'absent'} ; sondes GET /v1/models sur {len(hotes) * len(ports)} adresse(s).")
    resultats = detecter(hotes, ports, jeton, arguments.delai)
    afficher("Adresse | État | Statut | Identifiants | Conseil")
    for resultat in resultats:
        afficher(f"{resultat['base']} | {resultat['etat']} | {resultat['statut'] or '-'} | "
                 f"{', '.join(resultat['ids']) or '-'} | {resultat['conseil'] or '-'}")
    proposition = proposer(resultats)
    if proposition["trouve"]:
        afficher("Valeurs proposées pour .env (python scripts/preparer_env.py les reprend) :")
        for cle in ("METERLESS_BASE_URL", "METERLESS_MODEL_RAPIDE", "METERLESS_MODEL_PROFOND"):
            afficher(f"  {cle}={proposition[cle]}")
        if not proposition["nemotron_garanti"]:
            afficher("AVERTISSEMENT : " + CONSEIL_NEMOTRON)
    elif any(resultat["etat"] == NON_AUTORISE for resultat in resultats):
        afficher("Nightshift répond mais refuse l'accès : fournir le jeton Runtime (--fichier-jeton).")
    else:
        afficher("Nightshift introuvable : le démarrer (console Meterless) puis relancer ; vérifier le port.")
    arguments.json.parent.mkdir(parents=True, exist_ok=True)
    ecrire_rapport(arguments.json, nettoyer(json.dumps({"sondes": resultats, "proposition": proposition},
                                                       ensure_ascii=False, indent=2)))
    return 0 if proposition["trouve"] else 1


if __name__ == "__main__":
    raise SystemExit(executer_main(main))
