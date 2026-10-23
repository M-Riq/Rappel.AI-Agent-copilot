"""Injecteur de pannes du pipeline de démo : python -m rappel.pipeline_demo.injecteur p1|p2|p3|reset."""

import argparse
import sys

from rappel.pipeline_demo.pipeline import NOMS_SCENARIOS, dossier_demo, injecter, reset


def main(argv=None) -> int:
    parseur = argparse.ArgumentParser(description="Injecte une panne dans le pipeline de démo isolé, ou le remet au vert.")
    parseur.add_argument("scenario", choices=(*NOMS_SCENARIOS, "reset"))
    parseur.add_argument("--dossier", help="Dossier du pipeline (défaut : PIPELINE_DEMO_DIR, data/pipeline_demo)")
    arguments = parseur.parse_args(argv)
    try:
        etat = reset(arguments.dossier) if arguments.scenario == "reset" else injecter(arguments.scenario, arguments.dossier)
    except (OSError, ValueError) as erreur:
        print(f"Injection impossible : {erreur}")
        return 1
    print(f"Pipeline de démo ({dossier_demo(arguments.dossier).name}) : scénario {etat['scenario']}, "
          f"statut {etat['statut'].upper()}, lignes rejetées {etat['lignes_rejetees']}/{etat['lignes_lues']}")
    for alerte in etat["alertes"]:
        print(f"ALERTE : {alerte}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
