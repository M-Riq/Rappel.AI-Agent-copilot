import argparse
import json
from pathlib import Path
from statistics import mean
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._essai_commun import afficher, autoriser, difference, fermer, instant_usage, nettoyer
from src.rappel.contrats import ContexteIncident
from src.rappel.enquete.graphe import ADAPTATEUR_CANDIDATS, construire_prompt, extraire_json
from src.rappel.interfaces.llm import LLMBudgetEpuise, creer_llm


CATEGORIES = {"schema", "ressources", "reseau", "configuration"}


def analyser_reponse(reponse):
    mesure = {"parsable": False, "nb_candidats": 0, "categories": [],
              "categories_valides": False, "au_moins_trois": False, "valide": False}
    try:
        contenu = extraire_json(reponse)
        candidats = json.loads(contenu)
        mesure["parsable"] = True
        mesure["nb_candidats"] = len(candidats)
        mesure["categories"] = sorted({candidat.get("categorie") for candidat in candidats
                                       if isinstance(candidat, dict) and isinstance(candidat.get("categorie"), str)})
        mesure["categories_valides"] = bool(candidats) and all(
            isinstance(candidat, dict) and isinstance(candidat.get("categorie"), str)
            and candidat["categorie"] in CATEGORIES for candidat in candidats
        )
        mesure["au_moins_trois"] = len(candidats) >= 3
        ADAPTATEUR_CANDIDATS.validate_json(contenu)
        mesure["valide"] = True
    except (ValueError, TypeError, AttributeError):
        pass
    return mesure


def calculer_statistiques(mesures):
    def moyenne(nom):
        valeurs = [mesure.get(nom) for mesure in mesures]
        return mean(valeurs) if valeurs and all(valeur is not None for valeur in valeurs) else None
    categories = {categorie for mesure in mesures for categorie in mesure["categories"]}
    raisons = []
    taux = mean(mesure["valide"] for mesure in mesures) if mesures else 0.0
    if taux < 1:
        raisons.append("Au moins une reponse ne respecte pas le contrat du detective")
    manquantes = sorted(CATEGORIES - categories)
    if manquantes:
        raisons.append("Categories jamais proposees : " + ", ".join(manquantes))
    return {
        "taux_valides": taux, "nb_moyen_candidats": moyenne("nb_candidats"),
        "categories_manquantes": manquantes, "duree_moyenne_ms": moyenne("duree_ms"),
        "tokens_entree_moyens": moyenne("tokens_entree"), "tokens_sortie_moyens": moyenne("tokens_sortie"),
        "verdict": "A CORRIGER" if raisons else "OK", "raisons": raisons,
    }


def mesurer_detective(contexte, llm, repetitions=3, brut=False):
    prompt = construire_prompt(contexte)
    llm.debut_execution()
    mesures = []
    for repetition in range(1, repetitions + 1):
        avant = instant_usage(llm)
        debut = perf_counter()
        arret = False
        try:
            reponse = llm.completer(prompt, role="detective")
            mesure = analyser_reponse(reponse)
            if brut:
                afficher("Brut (400 caracteres max) : " + nettoyer(reponse)[:400])
        except LLMBudgetEpuise:
            afficher("Plafond LLM_MAX_APPELS atteint : repetitions restantes annulees.")
            arret = True
            mesure = analyser_reponse("")
        except Exception:
            afficher("Appel detective indisponible ; details sensibles non affiches.")
            mesure = analyser_reponse("")
        apres = instant_usage(llm)
        mesure.update(duree_ms=(perf_counter() - debut) * 1000,
                      tokens_entree=difference(avant[0], apres[0]),
                      tokens_sortie=difference(avant[1], apres[1]))
        mesures.append(mesure)
        afficher(f"{repetition} | JSON={mesure['parsable']} | candidats={mesure['nb_candidats']} | "
                 f"categories_valides={mesure['categories_valides']} | >=3={mesure['au_moins_trois']} | "
                 f"valide={mesure['valide']} | duree_ms={mesure['duree_ms']:.2f} | "
                 f"tokens={mesure['tokens_entree']}/{mesure['tokens_sortie']}")
        if arret:
            break
    return calculer_statistiques(mesures)


def main(argv=None, fabrique_llm=None):
    parseur = argparse.ArgumentParser(description="Essai manuel du detective, sans lancement automatique.")
    parseur.add_argument("--fixture", default="fixtures/contexte_p1.json")
    parseur.add_argument("--repetitions", type=int, default=3)
    parseur.add_argument("--brut", action="store_true")
    parseur.add_argument("--confirmer", action="store_true")
    arguments = parseur.parse_args(argv)
    if arguments.repetitions < 1:
        parseur.error("--repetitions doit etre positif")
    if not autoriser(arguments.confirmer, arguments.repetitions * 2):
        return 2
    llm = None
    try:
        contexte = ContexteIncident.model_validate_json(Path(arguments.fixture).read_text(encoding="utf-8"))
        llm = (fabrique_llm or creer_llm)()
        statistiques = mesurer_detective(contexte, llm, arguments.repetitions, arguments.brut)
        afficher("\nStatistique | Valeur")
        for nom, valeur in statistiques.items():
            afficher(f"{nom} | {valeur}")
        return 0 if statistiques["verdict"] == "OK" else 1
    except Exception:
        afficher("Essai impossible : verifier fixture et configuration. Aucun detail sensible affiche.")
        return 1
    finally:
        if llm is not None:
            fermer(llm)


if __name__ == "__main__":
    raise SystemExit(main())