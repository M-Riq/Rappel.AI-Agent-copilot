"""Crée un incident depuis l'état courant du pipeline de démo (simulé).

  python scripts/incident_depuis_demo.py --scenario p1            # injecte P1 puis envoie à l'API locale
  python scripts/incident_depuis_demo.py --scenario p1 --local    # enquête locale sans API
"""

import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "src"))

import requests

from rappel.config import charger
from rappel.ingestion.contexte_demo import construire_contexte
from rappel.pipeline_demo import NOMS_SCENARIOS, injecter


def enquete_locale(contexte):
    from rappel.enquete import enqueter
    from rappel.enquete.adaptateurs import MemoireEnquete, creer_llm_enquete
    from rappel.interfaces.abstention import AbstentionSeuil

    with TemporaryDirectory() as dossier:
        return enqueter(contexte, MemoireEnquete.json(Path(dossier) / "memoire.json"), creer_llm_enquete(),
                        AbstentionSeuil())


def main(argv=None):
    parseur = argparse.ArgumentParser(description="Incident construit depuis le pipeline de démo (données simulées).")
    parseur.add_argument("--scenario", choices=NOMS_SCENARIOS, help="Injecter d'abord cette panne")
    parseur.add_argument("--local", action="store_true", help="Enquêter localement au lieu d'appeler l'API")
    arguments = parseur.parse_args(argv)
    configuration = charger()
    if configuration.llm_provider != "fake" and arguments.local:
        print("REFUS : enquête locale réelle non prévue ici ; utiliser l'API avec --confirmer côté démo.")
        return 2
    if arguments.scenario:
        injecter(arguments.scenario)
    try:
        contexte, infos = construire_contexte()
    except (OSError, KeyError, ValueError):
        print("Pipeline de démo absent : lancer d'abord python scripts/injecteur.py p1|p2|p3.")
        return 1
    print(f"Contexte construit (SIMULATION, pipeline {infos['statut_pipeline']}, réducteur {infos['reducteur_logs']}) : "
          f"{contexte['incident_id']} signature {contexte['signature']}")
    if arguments.local:
        resultat = enquete_locale(contexte)
        if resultat.correctif is None:
            print("Abstention : " + "; ".join(resultat.decision_abstention.manque))
        else:
            print(f"Conclusion : {resultat.correctif.justification}")
        return 0
    try:
        reponse = requests.post(f"{configuration.api_url.rstrip('/')}/incidents", json=contexte, timeout=10)
        reponse.raise_for_status()
    except requests.RequestException:
        print("API indisponible : lancer uvicorn rappel.api:app --app-dir src, ou utiliser --local.")
        return 1
    print(f"Incident créé : {reponse.json()['id']} (suivre GET /incidents/{reponse.json()['id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
