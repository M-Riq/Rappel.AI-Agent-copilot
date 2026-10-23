"""Recette globale G1-G4 : du pipeline de démo en panne jusqu'au retour au vert.

G1 : reset -> P1 -> contexte -> enquête -> correctif -> action auto -> pipeline vert -> mémoire écrite
G2 : P2 (mémoire conservée) <= 60 % des événements de P1 et moins d'appels LLM
G3 : P3 -> abstention, aucun correctif, empreinte du pipeline inchangée
G4 : 5 cycles de suite sans intervention
En mode fake/fichier, chaque case réussie est « simulé » et sa partie réelle NON_MESURE.
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
from rappel.action.executeur import EXECUTE, executer_correctif
from rappel.config import charger
from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import MemoireEnquete, creer_llm_enquete
from rappel.ingestion.contexte_demo import construire_contexte
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.pipeline_demo import empreinte_dossier, injecter, lire_etat, reset

REUSSI, ECHOUE, NON_MESURE = "REUSSI", "ECHOUE", "NON_MESURE"
LIBELLES = {
    "G1": "P1 de bout en bout : enquête, correctif auto, retour au vert, mémoire écrite",
    "G2": "P2 avec mémoire : ≤ 60 % des événements de P1 et moins d'appels LLM",
    "G3": "P3 : abstention, aucun correctif, pipeline inchangé (empreinte SHA-256)",
    "G4": "5 cycles de suite sans intervention",
}
NB_CYCLES = 5


def cycle(fabrique_llm, dossier=None) -> dict:
    """Un parcours P1 -> P2 -> P3 avec une mémoire neuve partagée par P1 et P2."""
    with TemporaryDirectory() as temporaire:
        memoire = MemoireEnquete.json(Path(temporaire) / "memoire.json")

        def enquete(scenario):
            injecter(scenario, dossier)
            contexte, _ = construire_contexte(dossier)
            return enqueter(contexte, memoire, fabrique_llm(), AbstentionSeuil(), mode_preuves="sql")

        reset(dossier)
        p1 = enquete("p1")
        action = executer_correctif(p1.correctif, dossier=dossier) if p1.correctif else None
        vert = lire_etat(dossier)["statut"] == "vert"
        souvenirs = memoire.chercher(p1.enregistrement.signature) if p1.enregistrement else []
        g1 = bool(p1.correctif and "colonne renommée" in p1.correctif.justification and action
                  and action["statut"] == EXECUTE and vert and souvenirs and souvenirs[-1].hypotheses_refutees)
        p2 = enquete("p2")
        ratio = p2.metriques.nb_evenements / p1.metriques.nb_evenements if p1.metriques.nb_evenements else None
        g2 = ratio is not None and ratio <= 0.6 and p2.metriques.nb_appels_llm < p1.metriques.nb_appels_llm
        injecter("p3", dossier)
        avant = empreinte_dossier(dossier)
        contexte, _ = construire_contexte(dossier)
        p3 = enqueter(contexte, memoire, fabrique_llm(), AbstentionSeuil(), mode_preuves="sql")
        g3 = p3.decision_abstention.abstient and p3.correctif is None and empreinte_dossier(dossier) == avant
        reset(dossier)
        return {
            "G1": g1, "G2": g2, "G3": g3,
            "details": {
                "G1": f"action {action['statut'] if action else 'aucune'}, pipeline {'vert' if vert else 'rouge'}, "
                      f"{len(souvenirs)} souvenir(s)",
                "G2": f"événements {p2.metriques.nb_evenements}/{p1.metriques.nb_evenements}, "
                      f"appels LLM {p2.metriques.nb_appels_llm}/{p1.metriques.nb_appels_llm}",
                "G3": "abstention, empreinte inchangée" if g3 else "conclusion ou modification inattendue",
            },
        }


def evaluer(fabrique_llm=None, dossier=None, cycles=NB_CYCLES) -> dict:
    configuration = charger()
    llm_reel = configuration.llm_provider != "fake"
    fabrique = fabrique_llm or (lambda: creer_llm_enquete(configuration))
    resultats = []
    for _ in range(cycles):
        try:
            resultats.append(cycle(fabrique, dossier))
        except Exception:
            resultats.append({"G1": False, "G2": False, "G3": False, "details": {"G1": "erreur pendant le cycle"}})
    premier = resultats[0]

    def case(identifiant, reussi, detail):
        return {"case": identifiant, "libelle": LIBELLES[identifiant], "statut": REUSSI if reussi else ECHOUE,
                "detail": detail, "simule": not llm_reel,
                "reel": None if llm_reel else NON_MESURE,
                "raison_reel": None if llm_reel else "service réel requis : Nightshift (et Zetaris/Postgres selon les modes)"}

    cases = [case(identifiant, premier[identifiant], premier["details"].get(identifiant, ""))
             for identifiant in ("G1", "G2", "G3")]
    reussis = sum(all(resultat[identifiant] for identifiant in ("G1", "G2", "G3")) for resultat in resultats)
    cases.append(case("G4", reussis == cycles and cycles >= NB_CYCLES, f"{reussis}/{cycles} cycle(s) complets réussis"))
    return {
        "modes": {"llm": configuration.llm_provider, "source": configuration.source_provider,
                  "memoire": "json temporaire", "pipeline": "démo isolée (simulée)", "simulation": True},
        "cases": cases,
        "echecs": [element["case"] for element in cases if element["statut"] == ECHOUE],
    }


def main(argv=None, fabrique_llm=None):
    parseur = argparse.ArgumentParser(description="Recette globale G1-G4 sur le pipeline de démo.")
    parseur.add_argument("--confirmer", action="store_true")
    parseur.add_argument("--json", type=Path, default=RACINE / "data" / "recette_globale.json")
    parseur.add_argument("--cycles", type=int, default=NB_CYCLES)
    parseur.add_argument("--dossier", help="Dossier du pipeline de démo (défaut : PIPELINE_DEMO_DIR)")
    arguments = parseur.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    try:
        configuration = charger()
        if not autoriser(arguments.confirmer, arguments.cycles * 3 * configuration.max_tours_enquete,
                         executions=arguments.cycles * 3):
            return 2
        rapport = evaluer(fabrique_llm, arguments.dossier, arguments.cycles)
        afficher("Modes : " + ", ".join(f"{cle}={valeur}" for cle, valeur in rapport["modes"].items()))
        afficher("Case | Statut | Partie reelle | Libelle | Detail")
        for element in rapport["cases"]:
            reel = "-" if element["reel"] is None else f"{element['reel']} : {element['raison_reel']}"
            afficher(f"{element['case']} | {element['statut']}{' (simule)' if element['simule'] else ''} | {reel} | "
                     f"{element['libelle']} | {element['detail']}")
        afficher("Bilan : " + ("aucun ECHOUE" if not rapport["echecs"] else "ECHOUE : " + ", ".join(rapport["echecs"])))
        destination = arguments.json.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(nettoyer(json.dumps(rapport, ensure_ascii=False, indent=2)) + "\n", encoding="utf-8")
        return 0 if not rapport["echecs"] else 1
    except Exception:
        afficher("Recette globale impossible : verifier la configuration ; aucun detail sensible affiche.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
