"""Vérification de la machine de démonstration : système, configuration et services réels.

Sans --confirmer : contrôles locaux seulement (aucun appel réseau) ; les services réels sont
NON_MESURE. Avec --confirmer : Zetaris (SELECT 1 AS one, requête sur deux sources), Meterless
et Postgres sont appelés. Aucune clé ni mot de passe n'est affiché.
"""

import argparse
import contextlib
import ctypes
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "src"))
sys.path.insert(0, str(RACINE))

from rappel.config import charger
from scripts._essai_commun import ecrire_rapport, executer_main, nettoyer

REUSSI, ECHOUE, NON_MESURE = "REUSSI", "ECHOUE", "NON_MESURE"
RAM_MIN_GO = 16
DISQUE_MIN_GO = 15
PYTHONS_ACCEPTES = ((3, 11), (3, 12))
GO = 1024 ** 3

# Requête de démonstration sur deux sources (journaux et commits) ; tables de tools_zetaris.
REQUETE_DEUX_SOURCES = (
    "SELECT c.hash, l.compte FROM pipeline.commits c, pipeline.logs l "
    "WHERE c.hash = 'a1b2c3' AND l.template LIKE '%KeyError%'"
)
ATTENDU_DEUX_SOURCES = {"hash": "a1b2c3", "compte": 214}

VARIABLES_REELLES = {
    "meterless": ("meterless_base_url", "meterless_runtime_token", "meterless_model_rapide", "meterless_model_profond"),
    "zetaris": ("zetaris_base_url", "zetaris_api_key", "zetaris_org_id"),
    "postgres": ("database_url",),
}


def ligne(case, statut, detail):
    return {"case": case, "statut": statut, "detail": nettoyer(str(detail))}


def memoire_totale_octets():
    if platform.system() == "Windows":
        class Etat(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        etat = Etat()
        etat.dwLength = ctypes.sizeof(Etat)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(etat)):
            return None
        return etat.ullTotalPhys
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def docker_disponible():
    if not shutil.which("docker"):
        return ECHOUE, "docker introuvable dans le PATH"
    try:
        resultat = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                  capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return ECHOUE, "docker installé mais ne répond pas"
    if resultat.returncode != 0:
        return ECHOUE, "docker installé mais démon arrêté (lancer Docker Desktop)"
    return REUSSI, f"démon Docker {resultat.stdout.strip() or 'actif'}"


def verifier_systeme(memoire=memoire_totale_octets, disque=None, version=None, outil=shutil.which, docker=docker_disponible):
    lignes = []
    total = memoire()
    if total is None:
        lignes.append(ligne("RAM", NON_MESURE, "mémoire totale non mesurable sur ce système"))
    else:
        go = total / GO
        lignes.append(ligne("RAM", REUSSI if go >= RAM_MIN_GO - 0.5 else ECHOUE, f"{go:.1f} Go (minimum {RAM_MIN_GO} Go, tolérance 0,5 Go)"))
    libre = (disque or (lambda: shutil.disk_usage(RACINE).free))() / GO
    lignes.append(ligne("Disque", REUSSI if libre >= DISQUE_MIN_GO else ECHOUE, f"{libre:.1f} Go libres (minimum {DISQUE_MIN_GO} Go)"))
    majeure_mineure = tuple((version or sys.version_info)[:2])
    texte_version = ".".join(map(str, majeure_mineure))
    lignes.append(ligne("Python", REUSSI if majeure_mineure in PYTHONS_ACCEPTES else ECHOUE,
                        f"Python {texte_version} (attendu 3.11 ou 3.12)"))
    lignes.append(ligne("git", REUSSI if outil("git") else ECHOUE, "git trouvé" if outil("git") else "git introuvable dans le PATH"))
    statut, detail = docker()
    lignes.append(ligne("Docker", statut, detail))
    return lignes


def verifier_configuration(configuration, confirmer=False):
    """Présence (jamais la valeur) des variables du mode réel ; manque = ECHOUE seulement avec --confirmer."""
    lignes = []
    for service, champs in VARIABLES_REELLES.items():
        absents = [champ.upper() for champ in champs if not str(getattr(configuration, champ, "") or "").strip()]
        if absents:
            lignes.append(ligne(f"Config {service}", ECHOUE if confirmer else NON_MESURE,
                                "manque : " + ", ".join(absents) + " (requis pour le mode réel)"))
        else:
            lignes.append(ligne(f"Config {service}", REUSSI, "variables présentes (valeurs masquées)"))
    modes = f"llm={configuration.llm_provider} source={configuration.source_provider} memoire={configuration.memoire_provider}"
    lignes.append(ligne("Modes", REUSSI, modes + (" (SIMULATION)" if configuration.llm_provider == "fake" else "")))
    return lignes


def _verifier_deux_sources(resultat):
    lignes = resultat.get("lignes") or []
    return any(str(l.get("hash")) == ATTENDU_DEUX_SOURCES["hash"] and str(l.get("compte")) == str(ATTENDU_DEUX_SOURCES["compte"])
               for l in lignes if isinstance(l, dict))


def requete_deux_sources_simulee(configuration):
    from rappel.interfaces.tools_zetaris import run_sql

    try:
        resultat = run_sql(REQUETE_DEUX_SOURCES, configuration=replace(configuration, source_provider="fichier"))
    except (ValueError, RuntimeError):
        return ligne("2 sources (simulé)", ECHOUE, "requête refusée par la base simulée")
    if _verifier_deux_sources(resultat):
        return ligne("2 sources (simulé)", REUSSI, "a1b2c3 et 214 KeyError trouvés (fixtures SQLite, simulé)")
    return ligne("2 sources (simulé)", ECHOUE, "lignes attendues absentes")


def _executer_script(main):
    sortie = io.StringIO()
    try:
        with contextlib.redirect_stdout(sortie):
            code = main()
    except Exception:
        return 1, "erreur inattendue (détail masqué)"
    derniere = [texte for texte in sortie.getvalue().splitlines() if texte.strip()]
    return code, nettoyer(derniere[-1] if derniere else "")


def verifier_services(configuration):
    from rappel.interfaces.tools_zetaris import run_sql
    from scripts import check_meterless, check_postgres

    lignes = []
    zetaris = replace(configuration, source_provider="zetaris")
    try:
        resultat = run_sql("SELECT 1 AS one", configuration=zetaris)
        ok = any(str(l.get("one")) == "1" for l in resultat["lignes"] if isinstance(l, dict))
        lignes.append(ligne("Zetaris SELECT 1", REUSSI if ok else ECHOUE, "one = 1" if ok else "réponse inattendue"))
    except (ValueError, RuntimeError, KeyError, TypeError) as erreur:
        lignes.append(ligne("Zetaris SELECT 1", ECHOUE, erreur))
    try:
        resultat = run_sql(REQUETE_DEUX_SOURCES, configuration=zetaris)
        ok = _verifier_deux_sources(resultat)
        lignes.append(ligne("Zetaris 2 sources", REUSSI if ok else ECHOUE,
                            "a1b2c3 et 214 KeyError trouvés" if ok else "tables pipeline.commits/logs à aligner (docs/run_sql.md)"))
    except (ValueError, RuntimeError, KeyError, TypeError) as erreur:
        lignes.append(ligne("Zetaris 2 sources", ECHOUE, erreur))
    for nom, module in (("Meterless", check_meterless), ("Postgres", check_postgres)):
        code, detail = _executer_script(module.main)
        lignes.append(ligne(nom, REUSSI if code == 0 else ECHOUE, detail))
    return lignes


def services_non_mesures():
    raison = "--confirmer requis (appel réel)"
    return [ligne(nom, NON_MESURE, raison) for nom in ("Zetaris SELECT 1", "Zetaris 2 sources", "Meterless", "Postgres")]


def main(argv=None):
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--confirmer", action="store_true", help="Autoriser les appels réels (Zetaris, Meterless, Postgres)")
    parseur.add_argument("--json", type=Path, default=RACINE / "data" / "verification_machine.json")
    arguments = parseur.parse_args(argv)
    configuration = charger()
    lignes = verifier_systeme() + verifier_configuration(configuration, arguments.confirmer) + [requete_deux_sources_simulee(configuration)]
    lignes += verifier_services(configuration) if arguments.confirmer else services_non_mesures()
    print("Case | Statut | Détail")
    for entree in lignes:
        print(f"{entree['case']} | {entree['statut']} | {entree['detail']}")
    echecs = [entree["case"] for entree in lignes if entree["statut"] == ECHOUE]
    arguments.json.parent.mkdir(parents=True, exist_ok=True)
    ecrire_rapport(arguments.json, json.dumps({"confirme": arguments.confirmer, "cases": lignes, "echecs": echecs},
                                              ensure_ascii=False, indent=2))
    print("Bilan : " + ("aucun ECHOUE" if not echecs else "ECHOUE sur " + ", ".join(echecs)))
    return 0 if not echecs else 1


if __name__ == "__main__":
    raise SystemExit(executer_main(main))
