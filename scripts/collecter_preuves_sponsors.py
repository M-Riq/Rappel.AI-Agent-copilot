"""Collecte des preuves d'usage des sponsors à partir d'une enquête P1.

Sans --confirmer (ou en mode simulé) : enquête P1 simulée, chaque sponsor est NON_MESURE et
l'échantillon est marqué « simulé ». Avec --confirmer et les fournisseurs réels configurés :
Zetaris (requêtes SQL exécutées, source « zetaris »), Meterless (appels et tokens par voie) et
NVIDIA Nemotron (modèles renvoyés par le service). Écrit data/preuves_sponsors.json, sans clé.
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "src"))

from scripts._essai_commun import afficher, autoriser, fermer, nettoyer
from rappel.config import charger
from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import MemoireEnquete, creer_llm_enquete
from rappel.enquete.modeles import ContexteIncident
from rappel.interfaces.abstention import AbstentionSeuil

REUSSI, ECHOUE, NON_MESURE = "REUSSI", "ECHOUE", "NON_MESURE"
MAX_REQUETES_ECHANTILLON = 3


def contexte_p1():
    return ContexteIncident.model_validate_json((RACINE / "fixtures" / "contexte_p1.json").read_text(encoding="utf-8"))


def _usage(usage):
    donnees = asdict(usage)
    return {"nb_appels": donnees["nb_appels"], "tokens_entree": donnees["tokens_entree"],
            "tokens_sortie": donnees["tokens_sortie"],
            "par_voie": {voie: {cle: valeur for cle, valeur in compteur.items() if cle != "par_voie"}
                         for voie, compteur in (donnees.get("par_voie") or {}).items()}}


def evaluer(configuration, resultat, usage, confirme):
    """Statut par sponsor ; rien de simulé n'est REUSSI."""
    preuves = [e for e in resultat.evenements if e.type == "preuve" and e.requete_sql]
    modeles = sorted({e.modele for e in resultat.evenements if e.modele})
    llm_reel = configuration.llm_provider != "fake" and confirme
    source_reelle = configuration.source_provider == "zetaris" and confirme
    sponsors = {}

    if not source_reelle:
        sponsors["zetaris"] = {"statut": NON_MESURE, "detail": f"source simulée ({resultat.source_preuves})"}
    elif resultat.source_preuves != "zetaris" or not preuves:
        sponsors["zetaris"] = {"statut": ECHOUE, "detail": f"aucune preuve Zetaris (source {resultat.source_preuves})"}
    else:
        sponsors["zetaris"] = {"statut": REUSSI, "detail": f"{len(preuves)} requête(s) exécutée(s) via l'API REST"}
    sponsors["zetaris"]["requetes"] = [e.requete_sql for e in preuves[:MAX_REQUETES_ECHANTILLON]]

    if not llm_reel:
        sponsors["meterless"] = {"statut": NON_MESURE, "detail": "LLM simulé (fake)"}
        sponsors["nemotron"] = {"statut": NON_MESURE, "detail": "aucun modèle réel à contrôler"}
    else:
        appels = usage["par_voie"]
        ok = usage["nb_appels"] > 0
        sponsors["meterless"] = {"statut": REUSSI if ok else ECHOUE,
                                 "detail": f"{usage['nb_appels']} appel(s) via Nightshift"}
        autres = [m for m in modeles if "nemotron" not in m.lower()]
        if not modeles:
            sponsors["nemotron"] = {"statut": ECHOUE, "detail": "modèle non communiqué par le service"}
        elif autres:
            sponsors["nemotron"] = {"statut": ECHOUE, "detail": f"le routage a choisi {autres[0]} : fixer la voie"}
        else:
            sponsors["nemotron"] = {"statut": REUSSI, "detail": "modèles renvoyés : " + ", ".join(modeles)}
        sponsors["meterless"]["par_voie"] = appels
    sponsors["nemotron"]["modeles_renvoyes"] = modeles
    return sponsors


def collecter(configuration, confirme, fabrique_llm=None):
    llm = (fabrique_llm or (lambda: creer_llm_enquete(configuration)))()
    try:
        with TemporaryDirectory() as dossier:
            resultat = enqueter(contexte_p1(), MemoireEnquete.json(Path(dossier) / "m.json"), llm,
                                AbstentionSeuil(), mode_preuves="sql")
        usage = _usage(llm.usage)
    finally:
        fermer(llm)
    return {
        "horodatage": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "confirme": confirme,
        "modes": {"llm": configuration.llm_provider, "source": configuration.source_provider,
                  "simulation": configuration.llm_provider == "fake" or configuration.source_provider == "fichier"},
        "incident": "p1",
        "conclusion": None if resultat.correctif is None else resultat.correctif.justification,
        "usage": usage,
        "sponsors": evaluer(configuration, resultat, usage, confirme),
    }


def main(argv=None, fabrique_llm=None):
    parseur = argparse.ArgumentParser(description="Collecte des preuves d'usage des sponsors (enquête P1).")
    parseur.add_argument("--confirmer", action="store_true", help="Autoriser les appels réels")
    parseur.add_argument("--json", type=Path, default=RACINE / "data" / "preuves_sponsors.json")
    arguments = parseur.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    configuration = charger()
    if configuration.source_provider != "fichier" and not arguments.confirmer:
        afficher("REFUS : source réelle configurée. Relancer avec --confirmer pour autoriser les appels Zetaris.")
        return 2
    if not autoriser(arguments.confirmer, configuration.max_tours_enquete * 2):
        return 2
    try:
        rapport = collecter(configuration, arguments.confirmer, fabrique_llm)
    except Exception:
        afficher("Collecte impossible : vérifier la configuration et les services ; aucun détail sensible affiché.")
        return 1
    if rapport["modes"]["simulation"]:
        afficher("Modes simulés : les preuves ci-dessous ne sont PAS des preuves sponsor.")
    for nom, sponsor in rapport["sponsors"].items():
        afficher(f"{nom} | {sponsor['statut']} | {sponsor['detail']}")
    arguments.json.parent.mkdir(parents=True, exist_ok=True)
    arguments.json.write_text(nettoyer(json.dumps(rapport, ensure_ascii=False, indent=2)) + "\n", encoding="utf-8")
    afficher(f"Rapport écrit : {arguments.json.name}")
    return 0 if all(s["statut"] != ECHOUE for s in rapport["sponsors"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
