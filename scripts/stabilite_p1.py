import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from dotenv import load_dotenv

from scripts._essai_commun import afficher, autoriser, fermer
from scripts.demo_enquete import nettoyer_document
from src.rappel.contrats import ContexteIncident
from src.rappel.enquete import enqueter
from src.rappel.enquete.boucle_sql import ADAPTATEUR_CANDIDATS_SQL, MAX_TOURS, extraire_valeur_json
from src.rappel.enquete.graphe import ADAPTATEUR_CANDIDATS, extraire_json
from src.rappel.enquete.war_room import configuration
from src.rappel.interfaces.abstention import AbstentionSeuil
from src.rappel.interfaces.llm import creer_llm
from src.rappel.interfaces.memoire import MemoireJSON


SEUIL_STABILITE = 0.8


class ObservateurCandidats:
    def __init__(self, llm):
        self.llm = llm
        self.candidats = []

    def __getattr__(self, nom):
        return getattr(self.llm, nom)

    def completer(self, prompt, role="detective"):
        reponse = self.llm.completer(prompt, role=role)
        try:
            if role == "detective" and not prompt.startswith("{"):
                self.candidats = ADAPTATEUR_CANDIDATS.validate_json(extraire_json(reponse))
            elif role == "hypotheses":
                self.candidats = ADAPTATEUR_CANDIDATS_SQL.validate_json(extraire_valeur_json(reponse, list))
        except (ValueError, TypeError, AttributeError):
            pass
        return reponse

    def categorie_retenue(self, resultat):
        retenue = next((hypothese for hypothese in resultat.hypotheses
                        if hypothese.statut == "retenue"), None)
        if retenue is None:
            return None
        indice = int(retenue.id.rsplit(":hyp-", 1)[1]) - 1
        candidat = self.candidats[indice]
        # En mode sql la categorie est optionnelle : a defaut, le texte de l'hypothese.
        return candidat.categorie or candidat.texte


def calculer_stabilite(mesures):
    conclusions = Counter(
        (mesure["categorie"], mesure["abstient"], tuple(mesure["elements_touches"]))
        for mesure in mesures if mesure["statut"] == "termine"
    )
    majoritaire, identiques = conclusions.most_common(1)[0] if conclusions else (None, 0)
    proportion = identiques / len(mesures) if mesures else 0.0
    erreurs = sum(mesure["statut"] != "termine" for mesure in mesures)
    raisons = []
    if erreurs:
        raisons.append(f"{erreurs} repetition(s) en erreur, non comptees comme conclusions identiques")
    if proportion < SEUIL_STABILITE:
        raisons.append("Moins de 80 % des repetitions partagent la conclusion majoritaire")
    return {
        "repetitions": len(mesures), "identiques": identiques, "proportion": proportion,
        "conclusion_majoritaire": list(majoritaire[:2]) + [list(majoritaire[2])] if majoritaire else None,
        "distribution": [{"categorie": categorie, "abstient": abstient,
                          "elements_touches": list(elements), "nombre": nombre}
                         for (categorie, abstient, elements), nombre in conclusions.items()],
        "verdict": "OK" if proportion >= SEUIL_STABILITE else "A CORRIGER", "raisons": raisons,
    }


def mesurer_stabilite(contexte, llm, repetitions=5, war_room=False, mode_preuves="sql"):
    if repetitions < 1:
        raise ValueError("Nombre de repetitions positif requis")
    mesures = []
    with TemporaryDirectory() as dossier:
        for indice in range(1, repetitions + 1):
            observateur = ObservateurCandidats(llm)
            try:
                resultat = enqueter(
                    contexte, MemoireJSON(Path(dossier) / f"repetition-{indice}.json"),
                    observateur, AbstentionSeuil(), war_room_active=war_room and mode_preuves == "outils",
                    mode_preuves=mode_preuves,
                )
                mesure = {
                    "repetition": indice, "statut": "termine",
                    "categorie": observateur.categorie_retenue(resultat),
                    "abstient": resultat.decision_abstention.abstient,
                    "elements_touches": resultat.correctif.elements_touches if resultat.correctif else [],
                    "appels_llm": resultat.metriques.nb_appels_llm,
                    "duree_ms": resultat.metriques.duree_ms,
                }
            except Exception:
                mesure = {"repetition": indice, "statut": "erreur", "categorie": None,
                          "abstient": None, "elements_touches": [], "appels_llm": None, "duree_ms": None}
            mesures.append(mesure)
    return {"mesures": mesures, **calculer_stabilite(mesures)}


def afficher_rapport(rapport):
    afficher("Repetition | Statut | Categorie retenue | Abstention | Elements touches | Appels LLM | Duree ms")
    for mesure in rapport["mesures"]:
        duree = "n/a" if mesure["duree_ms"] is None else f"{mesure['duree_ms']:.2f}"
        afficher(f"{mesure['repetition']} | {mesure['statut']} | {mesure['categorie'] or 'aucune'} | "
                 f"{mesure['abstient']} | {mesure['elements_touches']} | {mesure['appels_llm']} | {duree}")
    afficher("Distribution des conclusions :")
    for conclusion in rapport["distribution"]:
        afficher(json.dumps(conclusion, ensure_ascii=True))
    afficher(f"Conclusion majoritaire : {rapport['identiques']}/{rapport['repetitions']} "
             f"identiques ({rapport['proportion']:.0%})")
    afficher(f"Verdict : {rapport['verdict']}")
    for raison in rapport["raisons"]:
        afficher("Raison : " + raison)


def main(argv=None, fabrique_llm=None):
    parseur = argparse.ArgumentParser(description="Mesure manuelle de stabilite A15, memoire vide a chaque repetition.")
    parseur.add_argument("--fixture", default="fixtures/contexte_p1.json")
    parseur.add_argument("--repetitions", type=int, default=5)
    parseur.add_argument("--json", type=Path, metavar="CHEMIN")
    parseur.add_argument("--war-room", action="store_true", help="Mode outils uniquement (hors MVP)")
    parseur.add_argument("--mode", choices=("sql", "outils"), default="sql")
    parseur.add_argument("--confirmer", action="store_true")
    arguments = parseur.parse_args(argv)
    if arguments.repetitions < 1:
        parseur.error("--repetitions doit etre positif")
    llm = None
    try:
        load_dotenv(override=False)
        config = configuration(active=True) if arguments.war_room and arguments.mode == "outils" else None
        if arguments.mode == "sql":
            maximum = arguments.repetitions * MAX_TOURS
        else:
            maximum = arguments.repetitions * (4 + (min(config.max_appels_llm, 8 * config.max_tours) if config else 0))
        if not autoriser(arguments.confirmer, maximum, executions=arguments.repetitions):
            return 2
        if (os.getenv("LLM_PROVIDER", "").strip().lower() or "faux") == "faux":
            afficher("Fournisseur faux : controle du mecanisme seulement, A15 reel reste a mesurer.")
        contexte = ContexteIncident.model_validate_json(Path(arguments.fixture).read_text(encoding="utf-8"))
        llm = (fabrique_llm or creer_llm)()
        rapport = mesurer_stabilite(contexte, llm, arguments.repetitions, arguments.war_room, arguments.mode)
        afficher_rapport(rapport)
        if arguments.json:
            destination = arguments.json.resolve()
            if destination.is_relative_to(RACINE / "fixtures") or destination == RACINE / "data" / "memoire.json":
                raise ValueError("Destination protegee")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(nettoyer_document(rapport), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
            afficher("Rapport JSON ecrit.")
        return 0 if rapport["verdict"] == "OK" else 1
    except Exception:
        afficher("Mesure impossible : verifier configuration, fixture et destination. Details sensibles masques.")
        return 1
    finally:
        if llm is not None:
            fermer(llm)


if __name__ == "__main__":
    raise SystemExit(main())