import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from pydantic import TypeAdapter
from dotenv import load_dotenv

from scripts._essai_commun import afficher, autoriser, fermer, nettoyer
from src.rappel.contrats import ContexteIncident
from src.rappel.enquete import ResultatEnquete, enqueter
from src.rappel.enquete.war_room import configuration
from src.rappel.interfaces.abstention import AbstentionSeuil
from src.rappel.interfaces.llm import creer_llm
from src.rappel.interfaces.memoire import MemoireJSON


def charger_contextes():
    return {
        nom: ContexteIncident.model_validate_json(
            (RACINE / "fixtures" / f"contexte_{nom.lower()}.json").read_text(encoding="utf-8")
        )
        for nom in ("P1", "P2", "P3")
    }


def jouer_demo(llm, war_room=False, contextes=None):
    contextes = charger_contextes() if contextes is None else contextes
    resultats = {}
    with TemporaryDirectory() as dossier:
        partagee = MemoireJSON(Path(dossier) / "partagee.json")
        vide = MemoireJSON(Path(dossier) / "vide.json")
        for nom in ("P1", "P2", "P3"):
            try:
                resultats[nom] = enqueter(
                    contextes[nom], partagee if nom != "P3" else vide, llm,
                    AbstentionSeuil(), war_room_active=war_room,
                )
            except Exception:
                resultats[nom] = None
                afficher(f"{nom} : enquete impossible ; verifier la configuration et le budget.")
    return resultats


def controle_sante(resultats):
    raisons = []
    for nom in ("P1", "P2"):
        resultat = resultats.get(nom)
        if resultat is None or resultat.correctif is None or resultat.decision_abstention.abstient:
            raisons.append(f"{nom} doit conclure avec un correctif")
    resultat = resultats.get("P3")
    if resultat is None or not resultat.decision_abstention.abstient or resultat.correctif is not None:
        raisons.append("P3 doit s'abstenir sans correctif")
    return (1 if raisons else 0), raisons


def reduction(avant, apres):
    return None if avant == 0 else 100 * (avant - apres) / avant


def afficher_resultats(resultats):
    afficher("Scenario | Evenements | Hypotheses testees | Ecartees memoire | Appels LLM | Tokens entree/sortie | Duree ms | Decision")
    for nom in ("P1", "P2", "P3"):
        resultat = resultats[nom]
        if resultat is None:
            afficher(f"{nom} | n/a | n/a | n/a | n/a | n/a | n/a | ERREUR")
            continue
        metriques = resultat.metriques
        entree = "n/a" if metriques.nb_tokens_entree is None else metriques.nb_tokens_entree
        sortie = "n/a" if metriques.nb_tokens_sortie is None else metriques.nb_tokens_sortie
        decision = "abstention" if resultat.decision_abstention.abstient else (
            "correctif propose" if resultat.correctif is not None else "aucun correctif"
        )
        afficher(f"{nom} | {metriques.nb_evenements} | {metriques.nb_hypotheses_testees} | "
                 f"{metriques.nb_ecartees_par_memoire} | {metriques.nb_appels_llm} | "
                 f"{entree}/{sortie} | {metriques.duree_ms:.2f} | {decision}")
    premier, second = resultats["P1"], resultats["P2"]
    if premier is not None and second is not None:
        hypotheses = reduction(premier.metriques.nb_hypotheses_testees, second.metriques.nb_hypotheses_testees)
        evenements = reduction(premier.metriques.nb_evenements, second.metriques.nb_evenements)
        afficher("\nComparaison P1 vs P2 (reduction positive = economie) :")
        afficher(f"Hypotheses testees : {hypotheses:.2f}% de reduction" if hypotheses is not None else "Hypotheses testees : n/a")
        afficher(f"Evenements : {evenements:.2f}% de reduction" if evenements is not None else "Evenements : n/a")
        afficher(f"Duree : {premier.metriques.duree_ms:.2f} -> {second.metriques.duree_ms:.2f} ms ; "
                 f"difference={second.metriques.duree_ms - premier.metriques.duree_ms:+.2f} ms")
    afficher("\nFil des evenements P2 :")
    if second is not None:
        for evenement in second.evenements:
            texte = " ".join(evenement.texte.splitlines())
            afficher(f"{evenement.etape:02d} | {evenement.type} | {evenement.acteur} | {texte}")


def nettoyer_document(valeur):
    if isinstance(valeur, str):
        return nettoyer(valeur)
    if isinstance(valeur, list):
        return [nettoyer_document(element) for element in valeur]
    if isinstance(valeur, dict):
        return {cle: nettoyer_document(element) for cle, element in valeur.items()}
    return valeur


def exporter_json(resultats, chemin):
    destination = Path(chemin).resolve()
    if destination.is_relative_to(RACINE / "fixtures") or destination == RACINE / "data" / "memoire.json":
        raise ValueError("Destination protegee")
    adaptateur = TypeAdapter(ResultatEnquete)
    code, raisons = controle_sante(resultats)
    document = {
        "scenarios": {
            nom: {"statut": "erreur"} if resultat is None else {
                "statut": "termine", **adaptateur.dump_python(resultat, mode="json")
            }
            for nom, resultat in resultats.items()
        },
        "controle_sante": {"code": code, "raisons": raisons},
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(nettoyer_document(document), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def main(argv=None, fabrique_llm=None, contextes=None):
    parseur = argparse.ArgumentParser(description="Demo locale P1, P2, P3, sans serveur ni memoire persistante.")
    parseur.add_argument("--war-room", action="store_true")
    parseur.add_argument("--json", type=Path, metavar="CHEMIN")
    parseur.add_argument("--confirmer", action="store_true")
    arguments = parseur.parse_args(argv)
    llm = None
    try:
        load_dotenv(override=False)
        config = configuration(active=True) if arguments.war_room else None
        appels_prevus = 12 + (3 * min(config.max_appels_llm, 8 * config.max_tours) if config else 0)
        if not autoriser(arguments.confirmer, appels_prevus, executions=3):
            return 1
        if arguments.war_room:
            afficher(f"Debat : jusqu'a {config.max_appels_llm} appels supplementaires par scenario "
                     f"et {config.max_tours} tours, toujours sous LLM_MAX_APPELS.")
        llm = (fabrique_llm or creer_llm)()
        resultats = jouer_demo(llm, arguments.war_room, contextes)
        afficher_resultats(resultats)
        if arguments.json is not None:
            exporter_json(resultats, arguments.json)
            afficher("Export JSON termine.")
        code, raisons = controle_sante(resultats)
        afficher("Controle de sante : OK" if code == 0 else "Controle de sante : ECHEC ; " + "; ".join(raisons))
        return code
    except Exception:
        afficher("Demo impossible : verifier configuration, fixtures et destination JSON. Aucun detail sensible affiche.")
        return 1
    finally:
        if llm is not None:
            fermer(llm)


if __name__ == "__main__":
    raise SystemExit(main())