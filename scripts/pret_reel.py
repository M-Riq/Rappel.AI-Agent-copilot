"""Go / no-go avant le branchement réel : rapport VERT / ORANGE / ROUGE, lecture seule par défaut.

Ordre : machine, Nightshift, clé NVIDIA (étape humaine), voies et modèle Nemotron, Zetaris, mémoire
(H-MEM ou Postgres selon MEMOIRE_PROVIDER), Langfuse (optionnel), .env hors de git, scan des secrets,
tests rapides. Chaque point ROUGE donne sa cause probable et la commande exacte pour corriger.
--confirmer autorise UN appel facturé par voie (contrôle « nemotron » du modèle renvoyé) ; sans lui,
ce point reste ORANGE. Rapport sans secret : data/pret_reel.json. Code de sortie 0 seulement si tout
ce qui est requis est VERT.

Avec MEMOIRE_PROVIDER=hmem, la mémoire est un fichier SQLite local : Postgres n'est pas contrôlé et
Docker (qui ne sert qu'à Postgres) n'est plus requis.

  python scripts/pret_reel.py [--confirmer] [--sans-tests]
"""

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from urllib.parse import urlsplit

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "src"))

from scripts._essai_commun import afficher, annoncer_faux_services, ecrire_rapport, executer_main, nettoyer
from rappel.config import charger

VERT, ORANGE, ROUGE = "VERT", "ORANGE", "ROUGE"
TESTS_RAPIDES = ("tests/test_run_sql.py", "tests/test_interfaces.py", "tests/test_contrats.py")
HMEM_PATH_DEFAUT = "data/hmem.sqlite"


def point(nom, statut, requis=True, cause="", commande=""):
    return {"point": nom, "statut": statut, "requis": requis, "cause": nettoyer(cause), "commande": commande}


def verifier_machine():
    from scripts.verifier_machine_demo import NON_MESURE, REUSSI, verifier_systeme

    commandes = {"RAM": "machine de démo de 16 Go (ou Zetaris version AWS)", "Disque": "libérer 15 Go",
                 "Python": "installer Python 3.11 ou 3.12", "git": "ajouter C:\\Program Files\\Git\\cmd au PATH",
                 "Docker": "démarrer Docker Desktop"}
    return [point(f"machine : {ligne['case']}", VERT if ligne["statut"] == REUSSI else (ORANGE if ligne["statut"] == NON_MESURE else ROUGE),
                  cause=ligne["detail"], commande="" if ligne["statut"] == REUSSI else commandes.get(ligne["case"], ""))
            for ligne in verifier_systeme()]


def ajuster_docker(points, configuration):
    """Avec H-MEM, Docker ne sert plus (il ne lançait que Postgres) : point gardé à titre indicatif, non requis."""
    if configuration.memoire_provider != "hmem":
        return points
    ajustes = []
    for element in points:
        if element["point"] == "machine : Docker" and element["statut"] != VERT:
            element = {**element, "statut": ORANGE, "requis": False,
                       "cause": element["cause"] + " ; non requis avec MEMOIRE_PROVIDER=hmem (Docker ne sert qu'à Postgres)",
                       "commande": "-"}
        ajustes.append(element)
    return ajustes


def verifier_nightshift(configuration):
    from rappel.interfaces.llm import url_meterless
    from scripts.detecter_nightshift import EN_LIGNE, NON_AUTORISE, sonder

    try:
        base = url_meterless(configuration)
    except ValueError as erreur:
        return point("Nightshift", ROUGE, cause=str(erreur), commande="python scripts/detecter_nightshift.py"), None
    parties = urlsplit(base)
    sonde = sonder(parties.hostname if ":" not in parties.hostname else f"[{parties.hostname}]", parties.port or 80,
                   configuration.meterless_runtime_token, 5.0)
    if sonde["etat"] == EN_LIGNE:
        return point("Nightshift", VERT, cause=f"en ligne ({len(sonde['ids'])} identifiant(s) meterless/*)"), sonde
    if sonde["etat"] == NON_AUTORISE:
        return point("Nightshift", ROUGE, cause=f"non autorisé ({sonde['statut']}) : {sonde['conseil']}",
                     commande="renseigner METERLESS_RUNTIME_TOKEN puis python scripts/detecter_nightshift.py --fichier-jeton <proxy-token>"), sonde
    return point("Nightshift", ROUGE, cause=f"{sonde['etat']} sur {base}",
                 commande="démarrer Nightshift puis python scripts/detecter_nightshift.py (ports 8088, 8090, 8787, 11434)"), sonde


def verifier_voies(configuration, sonde, confirmer, fabrique_llm=None):
    manque = [nom.upper() for nom in ("meterless_model_rapide", "meterless_model_profond") if not getattr(configuration, nom)]
    if manque:
        return point("voies et modèle Nemotron", ROUGE, cause="manque : " + ", ".join(manque),
                     commande="python scripts/detecter_nightshift.py puis python scripts/preparer_env.py")
    inconnues = [voie for voie in (configuration.meterless_model_rapide, configuration.meterless_model_profond)
                 if sonde and sonde.get("ids") and voie not in sonde["ids"]]
    if inconnues:
        return point("voies et modèle Nemotron", ROUGE, cause="voie(s) absente(s) du catalogue : " + ", ".join(inconnues),
                     commande="choisir des identifiants listés par python scripts/detecter_nightshift.py")
    if not confirmer:
        return point("voies et modèle Nemotron", ORANGE, cause="modèle renvoyé non vérifié (un appel facturé par voie)",
                     commande="python scripts/pret_reel.py --confirmer")
    from scripts.recette_reelle import case_a14

    from rappel.enquete.adaptateurs import creer_llm_enquete

    fabrique = fabrique_llm or (lambda: creer_llm_enquete(replace(charger(), llm_provider="meterless")))
    resultat = case_a14(fabrique)
    if resultat["statut"] == "REUSSI":
        return point("voies et modèle Nemotron", VERT, cause=resultat["detail"])
    return point("voies et modèle Nemotron", ROUGE, cause=resultat["detail"],
                 commande="imposer Nemotron pour chaque voie dans la console Nightshift (docs/RUNBOOK-BRANCHEMENT.md)")


def verifier_zetaris(configuration):
    from scripts.decouvrir_zetaris import decouvrir

    if not configuration.zetaris_api_key:
        return point("Zetaris", ROUGE, cause="ZETARIS_API_KEY absente", commande="renseigner ZETARIS_API_KEY dans .env (Juan)")
    rapport = decouvrir(replace(configuration, source_provider="zetaris"))
    if "erreur" in rapport:
        return point("Zetaris", ROUGE, cause=rapport["erreur"], commande="corriger ZETARIS_BASE_URL dans .env")
    if rapport["select_1"]["statut"] != "REUSSI":
        return point("Zetaris", ROUGE, cause=rapport["select_1"]["detail"],
                     commande="./verify.sh puis python scripts/decouvrir_zetaris.py")
    if rapport["tables"].get("manquantes"):
        return point("Zetaris", ROUGE, cause="tables manquantes : " + ", ".join(rapport["tables"]["manquantes"]),
                     commande="exposer les tables de docs/run_sql.md dans Zetaris (Juan)")
    refus = [sonde["construction"] for sonde in rapport["dialecte"] if sonde["statut"] != "REUSSI"]
    if refus:
        return point("Zetaris", ORANGE, requis=False, cause="dialecte : refusé " + ", ".join(refus) +
                     " (une reformulation guidée est tentée automatiquement)", commande="python scripts/decouvrir_zetaris.py")
    return point("Zetaris", VERT, cause="SELECT 1, tables et dialecte vérifiés")


def chemin_hmem(configuration) -> Path:
    brut = getattr(configuration, "hmem_path", None) or os.environ.get("HMEM_PATH") or HMEM_PATH_DEFAUT
    chemin = Path(brut)
    return chemin if chemin.is_absolute() else RACINE / chemin


def verifier_hmem(configuration):
    """Fichier SQLite H-MEM accessible en écriture : aller-retour dans une transaction annulée (rien n'est gardé)."""
    chemin = chemin_hmem(configuration)
    commande = "vérifier HMEM_PATH dans .env et les droits d'écriture du dossier (data/ par défaut)"
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        connexion = sqlite3.connect(chemin, timeout=5)
    except (OSError, sqlite3.Error) as erreur:
        return point("Mémoire H-MEM", ROUGE, cause=f"fichier {chemin.name} inaccessible ({type(erreur).__name__})",
                     commande=commande)
    try:
        connexion.execute("BEGIN IMMEDIATE")
        connexion.execute("CREATE TEMP TABLE IF NOT EXISTS _pret_reel (valeur TEXT)")
        connexion.execute("INSERT INTO _pret_reel VALUES ('aller-retour')")
        relu = connexion.execute("SELECT valeur FROM _pret_reel").fetchone()
        connexion.rollback()
    except sqlite3.Error as erreur:
        return point("Mémoire H-MEM", ROUGE, cause=f"écriture impossible dans {chemin.name} ({type(erreur).__name__})",
                     commande=commande)
    finally:
        connexion.close()
    if not relu or relu[0] != "aller-retour":
        return point("Mémoire H-MEM", ROUGE, cause="aller-retour SQLite incohérent", commande=commande)
    return point("Mémoire H-MEM", VERT, cause=f"{chemin.name} accessible en écriture (aller-retour annulé)")


def verifier_postgres(configuration):
    from scripts.verifier_machine_demo import _executer_script

    if not os.environ.get("DATABASE_URL"):
        return point("Postgres", ROUGE, cause="DATABASE_URL non renseignée (repli : mémoire JSON)",
                     commande="docker compose up -d postgres puis DATABASE_URL dans .env, ou MEMOIRE_PROVIDER=hmem")
    from scripts import check_postgres

    code, detail = _executer_script(check_postgres.main)
    if code == 0:
        return point("Postgres", VERT, cause=detail)
    return point("Postgres", ROUGE, cause=detail, commande="docker compose up -d postgres puis python scripts/check_postgres.py")


def verifier_memoire(configuration):
    if configuration.memoire_provider == "hmem":
        return verifier_hmem(configuration)
    return verifier_postgres(configuration)


def verifier_langfuse():
    if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
        return point("Langfuse (optionnel)", ORANGE, requis=False, cause="clés présentes ; trace à vérifier dans Langfuse")
    return point("Langfuse (optionnel)", VERT, requis=False, cause="désactivé (aucune clé) : optionnel")


def verifier_env_git():
    try:
        suivi = subprocess.run(["git", "ls-files", "--error-unmatch", ".env"], cwd=RACINE, capture_output=True).returncode == 0
    except OSError:
        return point(".env hors de git", ORANGE, cause="git introuvable", commande="ajouter C:\\Program Files\\Git\\cmd au PATH")
    ignore = ".env" in (RACINE / ".gitignore").read_text(encoding="utf-8").split()
    if suivi or not ignore:
        return point(".env hors de git", ROUGE, cause=".env suivi par git ou absent de .gitignore",
                     commande="git rm --cached .env ; ajouter .env à .gitignore ; révoquer les clés exposées")
    return point(".env hors de git", VERT, cause=".env ignoré et non suivi")


def verifier_scan():
    from scripts.verifier_machine_demo import _executer_script

    from scripts import scan_secrets

    code, detail = _executer_script(lambda: scan_secrets.main([]))
    return point("scan des secrets", VERT if code == 0 else ROUGE, cause=detail,
                 commande="" if code == 0 else "python scripts/scan_secrets.py puis révoquer et retirer les secrets signalés")


def verifier_tests():
    resultat = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS_RAPIDES],
                              cwd=RACINE, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    derniere = [ligne for ligne in resultat.stdout.splitlines() if ligne.strip()][-1:] or ["aucune sortie"]
    return point("tests rapides", VERT if resultat.returncode == 0 else ROUGE, cause=derniere[0],
                 commande="" if resultat.returncode == 0 else "pytest --ignore=tests/test_ui.py puis corriger")


def evaluer(confirmer=False, machine=verifier_machine, tests=verifier_tests, scan=verifier_scan, fabrique_llm=None):
    configuration = charger()
    points = ajuster_docker(list(machine()), configuration)
    nightshift, sonde = verifier_nightshift(configuration)
    points.append(nightshift)
    points.append(point("clé NVIDIA côté Nightshift", ORANGE, requis=False,
                        cause="non vérifiable par Rappel : étape humaine",
                        commande="vérifier dans la console Nightshift que la clé NVIDIA est active et créditée"))
    if nightshift["statut"] == VERT:
        points.append(verifier_voies(configuration, sonde, confirmer, fabrique_llm))
    else:
        points.append(point("voies et modèle Nemotron", ROUGE, cause="Nightshift non joignable : voies non vérifiables",
                            commande="corriger le point Nightshift d'abord"))
    points.append(verifier_zetaris(configuration))
    points.append(verifier_memoire(configuration))
    points.append(verifier_langfuse())
    points.append(verifier_env_git())
    points.append(scan())
    if tests is not None:
        points.append(tests())
    requis_non_verts = [element["point"] for element in points if element["requis"] and element["statut"] != VERT]
    return {"points": points, "go": not requis_non_verts, "bloquants": requis_non_verts, "confirme": confirmer}


def main(argv=None, **options) -> int:
    parseur = argparse.ArgumentParser(description="Go / no-go du branchement réel (lecture seule par défaut).")
    parseur.add_argument("--confirmer", action="store_true", help="Autoriser un appel facturé par voie (contrôle Nemotron)")
    parseur.add_argument("--sans-tests", action="store_true", help="Ne pas lancer les tests rapides")
    parseur.add_argument("--json", type=Path, default=RACINE / "data" / "pret_reel.json")
    arguments = parseur.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    faux = annoncer_faux_services()
    if arguments.sans_tests:
        options["tests"] = None
    rapport = evaluer(arguments.confirmer, **options)
    if faux:
        rapport["avertissement"] = "FAUX SERVICE LOCAL, NON une preuve sponsor"
    afficher("Point | Statut | Requis | Cause | Commande")
    for element in rapport["points"]:
        afficher(f"{element['point']} | {element['statut']} | {'oui' if element['requis'] else 'non'} | "
                 f"{element['cause'] or '-'} | {element['commande'] or '-'}")
    afficher("Décision : " + ("GO" if rapport["go"] else "NO-GO (bloquants : " + ", ".join(rapport["bloquants"]) + ")"))
    ecrire_rapport(arguments.json, nettoyer(json.dumps(rapport, ensure_ascii=False, indent=2)) + "\n")
    return 0 if rapport["go"] else 1


if __name__ == "__main__":
    raise SystemExit(executer_main(main))
