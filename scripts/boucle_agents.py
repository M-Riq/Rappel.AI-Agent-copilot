"""Boucle des 4 agents (Ingestion, Enquête, Mémoire, Action) sur le pipeline de démo.

  python scripts/boucle_agents.py --scenario p1,p2,p3      # séquence (mémoire partagée)
  python scripts/boucle_agents.py --surveiller --intervalle 5 --max-cycles 3

Le correctif n'est exécuté que si ACTION_REELLE_DEMO=1, et uniquement sur le pipeline de démo
isolé ; sinon il est seulement proposé. Un fournisseur réel (Nightshift, Zetaris) exige --confirmer.
Journal : data/boucle_journal.jsonl (BOUCLE_JOURNAL_PATH) ; résumé : data/boucle_resultats.json.
"""

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "src"))

from scripts._essai_commun import afficher, autoriser, nettoyer
from rappel.config import charger
from rappel.orchestration.boucle import chemin_journal, executer_sequence, surveiller
from rappel.pipeline_demo import NOMS_SCENARIOS


def scenarios(texte: str) -> list[str]:
    noms = [nom.strip().lower() for nom in texte.split(",") if nom.strip()]
    inconnus = [nom for nom in noms if nom not in NOMS_SCENARIOS]
    if not noms or inconnus:
        raise argparse.ArgumentTypeError(f"scénarios attendus parmi {', '.join(NOMS_SCENARIOS)}")
    return noms


def ligne_cycle(resume: dict) -> str:
    enquete, action, memoire = resume.get("enquete", {}), resume.get("action", {}), resume.get("memoire", {})
    conclusion = "abstention" if enquete.get("abstention") else ("correctif" if enquete.get("conclusion") else "-")
    return (f"{resume.get('scenario') or 'surveillance'} | {resume['statut']} | {resume.get('signature')} | "
            f"{conclusion} | événements {enquete.get('nb_evenements', '-')} | appels LLM {enquete.get('nb_appels_llm', '-')} | "
            f"war room {enquete.get('war_room', {}).get('tours', 0)} tour(s) | action {action.get('statut', '-')} | "
            f"mémoire {memoire.get('resultat') or '-'}")


def main(argv=None, fabrique_llm=None, memoire=None) -> int:
    parseur = argparse.ArgumentParser(description="Boucle des 4 agents sur le pipeline de démo.")
    groupe = parseur.add_mutually_exclusive_group(required=True)
    groupe.add_argument("--scenario", type=scenarios, help="Séquence, par exemple p1,p2,p3")
    groupe.add_argument("--surveiller", action="store_true", help="Lancer la boucle à chaque passage au rouge")
    parseur.add_argument("--intervalle", type=float, default=5.0, help="Secondes entre deux lectures de l'état")
    parseur.add_argument("--max-cycles", type=int, default=3, help="Plafond de cycles en surveillance")
    parseur.add_argument("--duree-max", type=float, default=600.0, help="Durée maximale de surveillance (s)")
    parseur.add_argument("--memoire-neuve", action="store_true", help="Mémoire JSON temporaire (sinon la mémoire configurée)")
    parseur.add_argument("--confirmer", action="store_true", help="Autoriser les appels à un service réel")
    parseur.add_argument("--json", type=Path, default=RACINE / "data" / "boucle_resultats.json")
    arguments = parseur.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    configuration = charger()
    if arguments.max_cycles < 1:
        afficher("--max-cycles doit être positif.")
        return 2
    if configuration.source_provider != "fichier" and not arguments.confirmer:
        afficher("REFUS : source réelle configurée. Relancer avec --confirmer pour autoriser les appels Zetaris.")
        return 2
    nb_cycles = len(arguments.scenario) if arguments.scenario else arguments.max_cycles
    if not autoriser(arguments.confirmer, nb_cycles * configuration.max_tours_enquete, executions=nb_cycles):
        return 2
    afficher(f"Modes : llm={configuration.llm_provider} source={configuration.source_provider} "
             f"memoire={configuration.memoire_provider} action_reelle_demo={int(configuration.action_reelle_demo)}"
             + (" (SIMULATION)" if configuration.llm_provider == "fake" or configuration.source_provider == "fichier" else ""))
    if not configuration.action_reelle_demo:
        afficher("ACTION_REELLE_DEMO=0 : les correctifs sont proposés, jamais exécutés.")
    with TemporaryDirectory() as temporaire:
        if memoire is None and arguments.memoire_neuve:
            from rappel.enquete.adaptateurs import MemoireEnquete

            memoire = MemoireEnquete.json(Path(temporaire) / "memoire.json")
        options = {"memoire": memoire, "fabrique_llm": fabrique_llm, "configuration": configuration}
        try:
            if arguments.scenario:
                cycles = executer_sequence(arguments.scenario, **options)
            else:
                afficher(f"Surveillance : toutes les {arguments.intervalle} s, au plus {arguments.max_cycles} cycle(s) "
                         f"et {arguments.duree_max} s (Ctrl+C pour arrêter).")
                try:
                    cycles = surveiller(intervalle_s=arguments.intervalle, max_cycles=arguments.max_cycles,
                                        duree_max_s=arguments.duree_max, **options)
                except KeyboardInterrupt:
                    afficher("Surveillance arrêtée proprement.")
                    cycles = []
        except Exception:
            afficher("Boucle interrompue : vérifier la configuration ; aucun détail sensible affiché.")
            return 1
    afficher("Scénario | Statut | Signature | Issue | Événements | Appels | War room | Action | Mémoire")
    for cycle in cycles:
        afficher(ligne_cycle(cycle.resume))
    resumes = [cycle.resume for cycle in cycles]
    arguments.json.parent.mkdir(parents=True, exist_ok=True)
    arguments.json.write_text(nettoyer(json.dumps({"cycles": resumes}, ensure_ascii=False, indent=2)) + "\n",
                              encoding="utf-8")
    afficher(f"Journal : {chemin_journal(configuration).name} ; résumé : {arguments.json.name}")
    return 0 if all(resume["statut"] in ("termine", "aucune_anomalie") for resume in resumes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
