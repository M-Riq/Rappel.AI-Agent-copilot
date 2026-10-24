"""Recette réelle en une commande : Nightshift, puis Zetaris, puis Postgres, puis le reste.

  python scripts/recette_reelle.py --confirmer

--confirmer est obligatoire (appels facturés). Sans les clés d'un service, chaque étape qui en
dépend est NON_MESURE avec la raison, jamais ECHOUE. Étapes :
  1. nightshift  check_meterless (catalogue + une réponse)
  2. zetaris     check_zetaris (SELECT 1 AS one)
  3. postgres    check_postgres (SELECT 1, tables, aller-retour annulé)
  4. A14         le modèle renvoyé contient « nemotron » sur la voie rapide ET la voie profonde
  5. A15         P1 rejouée 5 fois : même conclusion au moins 4 fois
  6. boucle      P1 -> P2 -> P3 complète (Ingestion, Enquête, Mémoire, Action) en réel
  7. war_room    débat réel, plafond affiché avant lancement, arrêt au plafond
  8. sponsors    collecte des preuves sponsors (Zetaris, Meterless, Nemotron)
Rapport : data/recette_reelle.json, sans aucune clé (valeurs masquées).
"""

import argparse
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "src"))

from scripts._essai_commun import ecrire_rapport, executer_main, ETIQUETTE_FAUX_SERVICES, afficher, annoncer_faux_services, nettoyer
from rappel.config import charger

REUSSI, ECHOUE, NON_MESURE = "REUSSI", "ECHOUE", "NON_MESURE"
ORDRE = ("nightshift", "zetaris", "postgres", "A14", "A15", "boucle", "war_room", "sponsors")
REPETITIONS_A15 = 5
SEUIL_A15 = 4
PROMPT_VOIE = ("Role: {role}. Reponds en une phrase : connexion operationnelle.\nDONNEES:\n"
               '{{"hypothese": {{"texte": "verification de la voie", "requetes": []}}}}')


def etape(nom, statut, detail):
    detail = nettoyer(str(detail))
    url = os.environ.get("DATABASE_URL")
    if url:
        detail = detail.replace(url, "[MASQUE]")
    return {"etape": nom, "statut": statut, "detail": detail}


def cles_presentes(configuration) -> dict:
    """Présence des clés (jamais leur valeur) et raison si absentes."""
    manque_nightshift = [nom.upper() for nom in ("meterless_runtime_token", "meterless_model_rapide",
                                                 "meterless_model_profond") if not getattr(configuration, nom)]
    raisons = {
        "nightshift": "clés absentes : " + ", ".join(manque_nightshift) if manque_nightshift else None,
        "zetaris": None if configuration.zetaris_api_key else "clé absente : ZETARIS_API_KEY",
        "postgres": None if os.environ.get("DATABASE_URL") else "DATABASE_URL non renseignée",
    }
    if raisons["postgres"] is None:
        try:
            importlib.import_module("psycopg")
        except ImportError:
            raisons["postgres"] = "pilote psycopg absent (pip install -r requirements-demo.txt)"
    return raisons


@contextmanager
def modes_reels(nightshift: bool, zetaris: bool, postgres: bool):
    """Force les fournisseurs réels disponibles pendant la recette, puis restaure l'environnement."""
    valeurs = {"LLM_PROVIDER": "meterless" if nightshift else None, "SOURCE_PROVIDER": "zetaris" if zetaris else None,
               "MEMOIRE_PROVIDER": "postgres" if postgres else None}
    anciennes = {nom: os.environ.get(nom) for nom in valeurs}
    try:
        for nom, valeur in valeurs.items():
            if valeur is not None:
                os.environ[nom] = valeur
        yield
    finally:
        for nom, valeur in anciennes.items():
            if valeur is None:
                os.environ.pop(nom, None)
            else:
                os.environ[nom] = valeur


def _script(nom):
    from scripts.verifier_machine_demo import _executer_script

    return _executer_script(importlib.import_module(f"scripts.{nom}").main)


def verifier_service(nom, raison, verifier):
    if raison:
        return etape(nom, NON_MESURE, raison)
    try:
        code, detail = verifier()
    except Exception:
        return etape(nom, ECHOUE, "vérification interrompue (détail masqué)")
    return etape(nom, REUSSI if code == 0 else ECHOUE, detail or f"code {code}")


def case_a14(fabrique_llm):
    llm = fabrique_llm()
    modeles = {}
    for voie, role in (("rapide", "hypotheses"), ("profonde", "conclusion")):
        llm.completer(PROMPT_VOIE.format(role=role), role=role)
        info = getattr(llm, "derniere_reponse_info", None) or {}
        modeles[voie] = info.get("modele_repondu") or "non communiqué"
    autres = [f"le routage a choisi {modele} : fixer la voie {voie}" for voie, modele in modeles.items()
              if "nemotron" not in str(modele).lower()]
    if autres:
        return etape("A14", ECHOUE, " ; ".join(autres))
    return etape("A14", REUSSI, f"voie rapide : {modeles['rapide']} ; voie profonde : {modeles['profonde']}")


def case_a15(fabrique_llm):
    from scripts.recette_module_a import charger_contexte
    from scripts.stabilite_p1 import mesurer_stabilite

    stabilite = mesurer_stabilite(charger_contexte("p1"), fabrique_llm(), REPETITIONS_A15)
    identiques = stabilite["identiques"]
    return etape("A15", REUSSI if identiques >= SEUIL_A15 else ECHOUE,
                 f"{identiques}/{REPETITIONS_A15} conclusions identiques (seuil {SEUIL_A15})")


def case_boucle(fabrique_llm, memoire_postgres: bool, lancer=None):
    from rappel.enquete.adaptateurs import MemoireEnquete
    from rappel.orchestration.boucle import executer_sequence

    lancer = lancer or executer_sequence
    with TemporaryDirectory() as dossier:
        memoire = MemoireEnquete() if memoire_postgres else MemoireEnquete.json(Path(dossier) / "memoire.json")
        p1, p2, p3 = (cycle.resume for cycle in lancer(["p1", "p2", "p3"], memoire=memoire, fabrique_llm=fabrique_llm))
    e1, e2 = p1.get("enquete", {}), p2.get("enquete", {})
    controles = {
        "P1 conclut": p1["statut"] == "termine" and bool(e1.get("conclusion")),
        "P2 plus court (≤ 60 % des événements, moins d'appels)": bool(e1.get("nb_evenements")) and bool(e2) and
        e2["nb_evenements"] <= 0.6 * e1["nb_evenements"] and e2["nb_appels_llm"] < e1["nb_appels_llm"],
        "P3 abstention, pipeline inchangé": p3["statut"] == "termine" and p3.get("enquete", {}).get("abstention") is True
        and p3.get("action", {}).get("pipeline_inchange") is True,
    }
    echecs = [nom for nom, ok in controles.items() if not ok]
    detail = (f"événements {e1.get('nb_evenements')}/{e2.get('nb_evenements')}, appels {e1.get('nb_appels_llm')}/"
              f"{e2.get('nb_appels_llm')} ; action P1 : {p1.get('action', {}).get('statut')}")
    return etape("boucle", ECHOUE if echecs else REUSSI, ("échec : " + ", ".join(echecs) + " ; " if echecs else "") + detail)


def case_war_room(fabrique_llm):
    from rappel.enquete import enqueter
    from rappel.enquete.adaptateurs import MemoireEnquete
    from rappel.enquete.war_room import configuration as configuration_war_room
    from rappel.interfaces.abstention import AbstentionSeuil
    from scripts.recette_module_a import charger_contexte

    limites = configuration_war_room(active=True)
    afficher(f"War room réelle : au plus {limites.max_tours} tour(s) et {limites.max_appels_llm} appel(s) LLM "
             "(WAR_ROOM_MAX_TOURS, WAR_ROOM_MAX_APPELS_LLM) ; arrêt au plafond.")
    with TemporaryDirectory() as dossier:
        resultat = enqueter(charger_contexte("p1"), MemoireEnquete.json(Path(dossier) / "memoire.json"), fabrique_llm(),
                            AbstentionSeuil(), war_room_active=True, war_room_max_tours=limites.max_tours,
                            war_room_max_appels_llm=limites.max_appels_llm)
    metriques = resultat.metriques
    fin = next((evenement.texte for evenement in reversed(resultat.evenements)
                if evenement.texte.startswith("War room terminee : ")), None)
    borne = metriques.nb_tours_war_room <= limites.max_tours and metriques.nb_appels_llm_war_room <= limites.max_appels_llm
    detail = (f"{metriques.nb_tours_war_room} tour(s), {metriques.nb_appels_llm_war_room} appel(s) ; "
              + (fin.split(";", 1)[0] if fin else "aucun débat (pas d'hypothèse retenue)"))
    return etape("war_room", REUSSI if borne and fin else ECHOUE, detail)


def case_sponsors(fabrique_llm):
    from scripts.collecter_preuves_sponsors import collecter

    rapport = collecter(charger(), True, fabrique_llm)
    statuts = {nom: sponsor["statut"] for nom, sponsor in rapport["sponsors"].items()}
    detail = ", ".join(f"{nom} {statut}" for nom, statut in statuts.items())
    if ECHOUE in statuts.values():
        return etape("sponsors", ECHOUE, detail)
    return etape("sponsors", REUSSI if set(statuts.values()) == {REUSSI} else NON_MESURE, detail)


def executer(fabrique_llm=None, verifications=None, lancer_boucle=None) -> dict:
    """Toutes les étapes dans l'ordre ; une étape sans ses prérequis est NON_MESURE."""
    from rappel.enquete.adaptateurs import creer_llm_enquete

    configuration = charger()
    raisons = cles_presentes(configuration)
    verifications = verifications or {nom: (lambda nom=nom: _script(nom))
                                      for nom in ("check_meterless", "check_zetaris", "check_postgres")}
    resultats = {}
    with modes_reels(raisons["nightshift"] is None, raisons["zetaris"] is None, False):
        resultats["nightshift"] = verifier_service("nightshift", raisons["nightshift"], verifications["check_meterless"])
        resultats["zetaris"] = verifier_service("zetaris", raisons["zetaris"], verifications["check_zetaris"])
    resultats["postgres"] = verifier_service("postgres", raisons["postgres"], verifications["check_postgres"])
    nightshift_ok = resultats["nightshift"]["statut"] == REUSSI
    zetaris_ok = resultats["zetaris"]["statut"] == REUSSI
    postgres_ok = resultats["postgres"]["statut"] == REUSSI
    with modes_reels(nightshift_ok, zetaris_ok, postgres_ok):
        fabrique = fabrique_llm or (lambda: creer_llm_enquete(charger()))
        etapes_llm = (("A14", lambda: case_a14(fabrique)), ("A15", lambda: case_a15(fabrique)),
                      ("boucle", lambda: case_boucle(fabrique, postgres_ok, lancer_boucle)),
                      ("war_room", lambda: case_war_room(fabrique)), ("sponsors", lambda: case_sponsors(fabrique)))
        for nom, lancer in etapes_llm:
            if not nightshift_ok:
                raison = raisons["nightshift"] or "Nightshift non vérifié (étape 1)"
                resultats[nom] = etape(nom, NON_MESURE, f"service réel requis : Nightshift ({raison})")
            elif nom in ("boucle", "sponsors") and not zetaris_ok:
                resultats[nom] = etape(nom, NON_MESURE, "service réel requis : Zetaris "
                                       f"({raisons['zetaris'] or 'Zetaris non vérifié (étape 2)'})")
            else:
                try:
                    resultats[nom] = lancer()
                except Exception:
                    resultats[nom] = etape(nom, ECHOUE, "étape interrompue (détail masqué)")
    etapes = [resultats[nom] for nom in ORDRE]
    return {
        "etapes": etapes,
        "echecs": [element["etape"] for element in etapes if element["statut"] == ECHOUE],
        "non_mesures": [element["etape"] for element in etapes if element["statut"] == NON_MESURE],
        "cles": {nom: raison is None for nom, raison in raisons.items()},
    }


def main(argv=None, fabrique_llm=None, verifications=None, lancer_boucle=None) -> int:
    parseur = argparse.ArgumentParser(description="Recette réelle en une commande (Nightshift, Zetaris, Postgres).")
    parseur.add_argument("--confirmer", action="store_true", help="Obligatoire : autorise les appels réels")
    parseur.add_argument("--json", type=Path, default=RACINE / "data" / "recette_reelle.json")
    arguments = parseur.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if not arguments.confirmer:
        afficher("REFUS : la recette réelle appelle des services facturés. Relancer avec --confirmer.")
        return 2
    configuration = charger()
    appels = (2 + 1 + REPETITIONS_A15 * configuration.max_tours_enquete + 3 * configuration.max_tours_enquete
              + configuration.max_tours_enquete + 6 + configuration.max_tours_enquete)
    afficher(f"Ordre : {' -> '.join(ORDRE)}. Appels LLM prévus au maximum : {appels} "
             f"(LLM_MAX_APPELS={configuration.llm_max_appels} par enquête).")
    faux = annoncer_faux_services()
    rapport = executer(fabrique_llm, verifications, lancer_boucle)
    if faux:
        rapport["avertissement"] = ETIQUETTE_FAUX_SERVICES
    afficher("Étape | Statut | Détail")
    for element in rapport["etapes"]:
        afficher(f"{element['etape']} | {element['statut']} | {element['detail']}")
    afficher("Bilan : " + ("aucun ECHOUE" if not rapport["echecs"] else "ECHOUE sur " + ", ".join(rapport["echecs"]))
             + (f" ; NON_MESURE : {', '.join(rapport['non_mesures'])}" if rapport["non_mesures"] else ""))
    arguments.json.parent.mkdir(parents=True, exist_ok=True)
    ecrire_rapport(arguments.json, nettoyer(json.dumps(rapport, ensure_ascii=False, indent=2)) + "\n")
    afficher(f"Rapport écrit : {arguments.json.name}")
    return 0 if not rapport["echecs"] else 1


if __name__ == "__main__":
    raise SystemExit(executer_main(main))
