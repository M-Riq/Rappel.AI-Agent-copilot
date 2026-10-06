import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._essai_commun import LLMCapture, afficher, autoriser, fermer
from src.rappel.contrats import ContexteIncident
from src.rappel.enquete import enqueter
from src.rappel.interfaces.abstention import AbstentionSeuil
from src.rappel.interfaces.llm import creer_llm
from src.rappel.interfaces.memoire import MemoireJSON


def comparer_resultats(sans, avec):
    def resume(resultat):
        metriques = resultat.metriques
        return {"duree_ms": metriques.duree_ms, "tokens_entree": metriques.nb_tokens_entree,
                "tokens_sortie": metriques.nb_tokens_sortie, "appels": metriques.nb_appels_llm,
                "abstient": resultat.decision_abstention.abstient, "correctif": resultat.correctif is not None}
    return {
        "sans_debat": resume(sans), "avec_debat": resume(avec),
        "decision_identique": (
            sans.decision_abstention.abstient == avec.decision_abstention.abstient
            and sans.correctif == avec.correctif
        ),
        "nb_objections": avec.metriques.nb_objections,
        "nb_objections_sans_reponse": avec.metriques.nb_objections_sans_reponse,
        "nb_appels_debat": avec.metriques.nb_appels_llm_war_room,
    }


def mesurer_comparaison(contexte, llm, tours=2, max_appels=6, brut=False):
    capture = LLMCapture(llm, brut)
    with TemporaryDirectory() as dossier:
        sans = enqueter(contexte, MemoireJSON(Path(dossier) / "sans.json"), capture,
                        AbstentionSeuil(), war_room_active=False)
        avec = enqueter(contexte, MemoireJSON(Path(dossier) / "avec.json"), capture,
                        AbstentionSeuil(), war_room_active=True,
                        war_room_max_tours=tours, war_room_max_appels_llm=max_appels)
    for evenement in avec.evenements:
        if evenement.type == "debat":
            afficher(f"{evenement.etape} | debat | {evenement.acteur} | {evenement.texte}")
    comparaison = comparer_resultats(sans, avec)
    afficher("\nComparaison | Valeur")
    for nom, valeur in comparaison.items():
        afficher(f"{nom} | {valeur}")
    afficher(f"Decision finale : abstient={avec.decision_abstention.abstient}, "
             f"confiance={avec.decision_abstention.confiance:.2f}, manque={avec.decision_abstention.manque}")
    return comparaison


def main(argv=None, fabrique_llm=None):
    parseur = argparse.ArgumentParser(description="Essai manuel comparatif de war room.")
    parseur.add_argument("--fixture", default="fixtures/contexte_p1.json")
    parseur.add_argument("--tours", type=int, default=2)
    parseur.add_argument("--max-appels", type=int, default=6)
    parseur.add_argument("--brut", action="store_true")
    parseur.add_argument("--confirmer", action="store_true")
    arguments = parseur.parse_args(argv)
    if arguments.tours < 0 or arguments.max_appels < 0:
        parseur.error("--tours et --max-appels doivent etre positifs ou nuls")
    if not autoriser(arguments.confirmer, 8 + min(arguments.max_appels, 8 * arguments.tours), executions=2):
        return 2
    llm = None
    try:
        contexte = ContexteIncident.model_validate_json(Path(arguments.fixture).read_text(encoding="utf-8"))
        llm = (fabrique_llm or creer_llm)()
        mesurer_comparaison(contexte, llm, arguments.tours, arguments.max_appels, arguments.brut)
        return 0
    except Exception:
        afficher("Comparaison impossible : verifier fixture, configuration et budget. Aucun detail sensible affiche.")
        return 1
    finally:
        if llm is not None:
            fermer(llm)


if __name__ == "__main__":
    raise SystemExit(main())