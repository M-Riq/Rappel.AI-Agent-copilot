"""Recette automatique du Module A (cahier MVP v4, cases A1 a A15).

Execute P1 et P2 (meme memoire) puis P3 (memoire vide) avec le moteur d'enquete en mode sql
et evalue chaque case : REUSSI, ECHOUE ou NON_MESURE. En mode fake ou fichier, les parties
qui exigent un service reel (Nightshift, Zetaris, Postgres) sont marquees « simule » et
NON_MESURE : rien de simule n'est presente comme reel.
"""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "src"))

from scripts._essai_commun import ETIQUETTE_FAUX_SERVICES, afficher, annoncer_faux_services, autoriser, nettoyer
from scripts.stabilite_p1 import mesurer_stabilite
from rappel.config import Configuration, charger
from rappel.contrats import valider
from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import MemoireEnquete, creer_llm_enquete
from rappel.enquete.boucle_sql import cle_texte
from rappel.enquete.modeles import ContexteIncident, EnregistrementMemoire
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.interfaces.llm import MeterlessLLM


REUSSI, ECHOUE, NON_MESURE = "REUSSI", "ECHOUE", "NON_MESURE"
LIBELLES = {
    "A1": "≥ 2 hypothèses avec statut sur contexte_p1.json",
    "A2": "toute hypothèse retenue cite ≥ 1 preuve réelle",
    "A3": "chaque preuve renvoie à une requête SQL exécutée (requete_sql)",
    "A4": "chaque étape écrit un EvenementEnquete valide (contrat)",
    "A5": "P1 conclut « colonne renommée » et le CorrectifPropose est valide",
    "A6": "arrêt au plafond de tours avec un message clair",
    "A7": "après P1, EnregistrementMemoire avec hypothèses réfutées écrit en mémoire",
    "A8": "P2 retrouve P1 et ne reteste aucune hypothèse réfutée",
    "A9": "P2 ≤ 60 % des événements de P1 et moins d'appels LLM",
    "A10": "mémoire contraire : l'agent change de décision",
    "A11": "P3 : abstention, aucun correctif, éléments manquants listés",
    "A12": "seuil calibré : P1 et P2 sans abstention, P3 avec",
    "A13": "voie et modèle changés par configuration, sans toucher au code",
    "A14": "en mode réel, le modèle renvoyé contient « nemotron »",
    "A15": "P1 rejouée 5 fois : même conclusion au moins 4 fois",
}
LIBELLES.update({
    "W1": "war room : débat borné par le plafond de tours et d'appels",
    "W2": "war room : le débat ne change jamais une preuve citée",
    "W3": "war room : arrêt clair (motif affiché)",
})
PLAFOND_ESSAI = 2
PLAFOND_WAR_ROOM = 1
MOTIFS_ARRET_WAR_ROOM = ("plafond de tours atteint", "aucune objection restante", "plafond d'appels", "JSON invalide",
                         "fournisseur LLM indisponible")
SEUIL_STABILITE = 4


def charger_contexte(nom):
    return ContexteIncident.model_validate_json(
        (RACINE / "fixtures" / f"contexte_{nom}.json").read_text(encoding="utf-8")
    )


def case(identifiant, statut, detail, simule=False, reel=None, raison_reel=None):
    return {"case": identifiant, "libelle": LIBELLES[identifiant], "statut": statut, "detail": detail,
            "simule": simule, "reel": reel, "raison_reel": raison_reel}


def verdict(condition):
    return REUSSI if condition else ECHOUE


def retenue(resultat):
    return next((hypothese for hypothese in resultat.hypotheses if hypothese.statut == "retenue"), None)


def requetes_executees(resultat):
    return {requete["id"]: requete for requete in resultat.requetes_sql if requete["executee"]}


def case_a14(resultats, llm_reel):
    if not llm_reel:
        return case("A14", NON_MESURE, "LLM simulé (fake) : aucun modèle réel à contrôler", simule=True,
                    reel=NON_MESURE, raison_reel="service réel requis : Nightshift")
    modeles = sorted({evenement.modele for resultat in resultats for evenement in resultat.evenements
                      if evenement.modele})
    if not modeles:
        return case("A14", ECHOUE, "aucun modèle renvoyé par le service dans les événements")
    autres = [modele for modele in modeles if "nemotron" not in modele.lower()]
    if autres:
        return case("A14", ECHOUE, f"le routage a choisi {autres[0]} : fixer la voie")
    return case("A14", REUSSI, f"modèles renvoyés : {', '.join(modeles)}")


def case_a13(configuration):
    essais = []
    for rapide, profond in (("voie-rapide-a", "voie-profonde-a"), ("voie-rapide-b", "voie-profonde-b")):
        llm = MeterlessLLM(replace(configuration, meterless_model_rapide=rapide, meterless_model_profond=profond))
        essais.append(
            all(llm.modele_pour_role(role) == rapide for role in ("hypotheses", "verdict"))
            and all(llm.modele_pour_role(role) == profond for role in ("conclusion", "correctif"))
        )
    return case("A13", verdict(all(essais)),
                "deux configurations METERLESS_MODEL_RAPIDE/PROFOND, aiguillage vérifié sans appel réseau")


def preuves_sql(resultat):
    return [evenement.requete_sql for evenement in resultat.evenements if evenement.type == "preuve" and evenement.requete_sql]


def cases_war_room(sans, borne, plafond_appels, llm_reel):
    """W1-W3 : même P1 sans débat et avec débat borné à PLAFOND_WAR_ROOM tour(s)."""
    metriques = borne.metriques
    w1 = 1 <= metriques.nb_tours_war_room <= PLAFOND_WAR_ROOM and metriques.nb_appels_llm_war_room <= plafond_appels
    retenue_sans, retenue_avec = retenue(sans), retenue(borne)
    w2 = (retenue_sans is not None and retenue_avec is not None and retenue_sans.preuves == retenue_avec.preuves
          and preuves_sql(sans) == preuves_sql(borne))
    fins = [evenement for evenement in borne.evenements if evenement.texte.startswith("War room terminee : ")]
    dernier_debat = max((evenement.etape for evenement in borne.evenements if evenement.type == "debat"), default=0)
    motif = fins[-1].texte.split(" : ", 1)[1].split(";", 1)[0] if fins else ""
    w3 = bool(fins) and fins[-1].etape > dernier_debat and any(motif.startswith(m) for m in MOTIFS_ARRET_WAR_ROOM)
    options = {"simule": not llm_reel, "raison_reel": None if llm_reel else "service réel requis : Nightshift"}
    return [
        case("W1", verdict(w1), f"{metriques.nb_tours_war_room} tour(s) pour un plafond de {PLAFOND_WAR_ROOM} ; "
             f"{metriques.nb_appels_llm_war_room} appel(s) pour un plafond de {plafond_appels}",
             reel=verdict(w1) if llm_reel else NON_MESURE, **options),
        case("W2", verdict(w2), "preuves de l'hypothèse retenue et requêtes SQL identiques avec et sans débat"
             if w2 else "une preuve citée diffère après le débat", reel=verdict(w2) if llm_reel else NON_MESURE, **options),
        case("W3", verdict(w3), f"arrêt : « {motif} »" if fins else "aucun message d'arrêt de la war room",
             reel=verdict(w3) if llm_reel else NON_MESURE, **options),
    ]


def evaluer(configuration: Configuration | None = None, fabrique_llm=None, repetitions=5, memoire_reelle=None):
    configuration = configuration or charger()
    llm_reel = configuration.llm_provider != "fake"
    source_reelle = configuration.source_provider == "zetaris"
    fabrique = fabrique_llm or (lambda: creer_llm_enquete(configuration))
    cases = []
    with TemporaryDirectory() as dossier:
        dossier = Path(dossier)
        partagee = MemoireEnquete.json(dossier / "partagee.json")

        def lancer(nom, memoire, **options):
            return enqueter(charger_contexte(nom), memoire, fabrique(), AbstentionSeuil(), mode_preuves="sql", **options)

        p1 = lancer("p1", partagee)
        souvenirs_p1 = partagee.chercher(charger_contexte("p1").signature)
        p2 = lancer("p2", partagee)
        p3 = lancer("p3", MemoireEnquete.json(dossier / "vide.json"))
        plafond = lancer("p1", MemoireEnquete.json(dossier / "plafond.json"), max_tours=PLAFOND_ESSAI)
        retenue_p1 = retenue(p1)
        contraire = None
        if retenue_p1 is not None:
            memoire_contraire = MemoireEnquete.json(dossier / "contraire.json")
            memoire_contraire.ecrire(EnregistrementMemoire(
                signature=charger_contexte("p1").signature, cause_confirmee="Diagnostic erroné (recette)",
                hypotheses_refutees=[retenue_p1.texte], correctif_essaye="Correctif écarté (recette)",
                resultat="echoue", date="2026-10-01",
            ))
            contraire = lancer("p1", memoire_contraire)
        stabilite = mesurer_stabilite(charger_contexte("p1"), fabrique(), repetitions)
        from rappel.enquete.war_room import configuration as configuration_war_room

        plafond_appels = configuration_war_room(active=True).max_appels_llm
        sans_debat = lancer("p1", MemoireEnquete.json(dossier / "sans_debat.json"), war_room_active=False)
        avec_debat = lancer("p1", MemoireEnquete.json(dossier / "avec_debat.json"), war_room_active=True,
                            war_room_max_tours=PLAFOND_WAR_ROOM, war_room_max_appels_llm=plafond_appels)
        war_room = cases_war_room(sans_debat, avec_debat, plafond_appels, llm_reel)

    # A1-A2 : hypotheses et preuves citees.
    cases.append(case("A1", verdict(len(p1.hypotheses) >= 2 and all(
        hypothese.statut in {"ouverte", "retenue", "refutee"} for hypothese in p1.hypotheses)),
        f"{len(p1.hypotheses)} hypothèse(s) : {[hypothese.statut for hypothese in p1.hypotheses]}"))
    executees = requetes_executees(p1)
    preuves_ok = all(
        hypothese.preuves and all(
            (requete := executees.get(preuve.split(" ", 1)[0])) is not None
            and requete["erreur"] is None and requete["nb_lignes"] > 0
            for preuve in hypothese.preuves
        )
        for hypothese in p1.hypotheses if hypothese.statut == "retenue"
    )
    cases.append(case("A2", verdict(retenue_p1 is not None and preuves_ok),
                      "chaque preuve retenue renvoie à une requête exécutée à résultat non vide"))

    # A3 : chaque preuve affichee porte le SQL execute.
    resultats = (p1, p2, p3)
    preuves = [(resultat, evenement) for resultat in resultats for evenement in resultat.evenements
               if evenement.type == "preuve"]
    a3 = bool(preuves) and all(
        evenement.requete_sql and evenement.requete_sql in {
            requete["sql"] for requete in requetes_executees(resultat).values()}
        for resultat, evenement in preuves
    )
    statut_a3 = verdict(a3)
    cases.append(case(
        "A3", statut_a3, f"{len(preuves)} preuve(s), source {resultats[0].source_preuves}",
        simule=not source_reelle, reel=statut_a3 if source_reelle else NON_MESURE,
        raison_reel=None if source_reelle else "service réel requis : Zetaris",
    ))

    # A4 : contrat des evenements.
    erreurs_a4 = 0
    for resultat in (*resultats, plafond):
        etapes = [evenement.etape for evenement in resultat.evenements]
        if etapes != list(range(1, len(etapes) + 1)):
            erreurs_a4 += 1
        for evenement in resultat.evenements:
            try:
                valider("evenement_enquete", evenement.model_dump(mode="json", exclude_none=True))
            except Exception:
                erreurs_a4 += 1
    cases.append(case("A4", verdict(erreurs_a4 == 0), f"{erreurs_a4} écart(s) au contrat ou aux étapes"))

    # A5 : conclusion et correctif.
    correctif_valide = False
    if p1.correctif is not None:
        try:
            valider("correctif_propose", p1.correctif.model_dump(mode="json", exclude_none=True))
            correctif_valide = True
        except Exception:
            pass
    renomme = p1.correctif is not None and "colonne renommée" in p1.correctif.justification.lower()
    cases.append(case("A5", verdict(renomme and correctif_valide),
                      "justification « colonne renommée » et correctif conforme" if renomme
                      else "la conclusion de P1 ne dit pas « colonne renommée »"))

    # A6 : plafond de tours.
    message = f"Plafond de {PLAFOND_ESSAI} tours atteint"
    a6 = (plafond.correctif is None and plafond.decision_abstention.abstient
          and any(evenement.texte.startswith(message) for evenement in plafond.evenements))
    cases.append(case("A6", verdict(a6), f"essai avec max_tours={PLAFOND_ESSAI} ; "
                      f"défaut MAX_TOURS_ENQUETE={configuration.max_tours_enquete}"))

    # A7 : enregistrement memoire apres P1.
    a7 = bool(souvenirs_p1) and bool(souvenirs_p1[-1].hypotheses_refutees)
    statut_a7 = verdict(a7)
    reel_a7, raison_a7 = NON_MESURE, "service réel requis : Postgres"
    if memoire_reelle is not None and souvenirs_p1:
        try:
            memoire_reelle.ecrire(souvenirs_p1[-1])
            relus = memoire_reelle.chercher(souvenirs_p1[-1].signature)
            mode = getattr(memoire_reelle.backend, "mode", type(memoire_reelle.backend).__name__)
            if mode == "postgres":
                reel_a7, raison_a7 = verdict(souvenirs_p1[-1] in relus), "écrit et relu dans Postgres"
            else:
                raison_a7 = f"Postgres injoignable (mode {mode}) : service réel requis"
        except Exception:
            reel_a7, raison_a7 = ECHOUE, "écriture ou lecture Postgres impossible"
    cases.append(case("A7", statut_a7, "mémoire JSON temporaire de la recette", simule=True,
                      reel=reel_a7, raison_reel=raison_a7))

    # A8 : P2 retrouve P1 et ne reteste rien de refute.
    refutees = {cle_texte(texte) for souvenir in souvenirs_p1 for texte in souvenir.hypotheses_refutees}
    retrouve = any(evenement.texte.startswith("incident similaire") for evenement in p2.evenements)
    reteste = [hypothese.texte for hypothese in p2.hypotheses if cle_texte(hypothese.texte) in refutees]
    cases.append(case("A8", verdict(retrouve and bool(refutees) and not reteste),
                      f"{p2.metriques.nb_ecartees_par_memoire} écartée(s) par la mémoire ; retestées : {reteste or 'aucune'}"))

    # A9 : economie de P2.
    ratio = p2.metriques.nb_evenements / p1.metriques.nb_evenements if p1.metriques.nb_evenements else None
    a9 = ratio is not None and ratio <= 0.6 and p2.metriques.nb_appels_llm < p1.metriques.nb_appels_llm
    cases.append(case("A9", verdict(a9), f"événements {p2.metriques.nb_evenements}/{p1.metriques.nb_evenements}"
                      f" ; appels LLM {p2.metriques.nb_appels_llm}/{p1.metriques.nb_appels_llm}"))

    # A10 : memoire contraire.
    if contraire is None:
        cases.append(case("A10", ECHOUE, "P1 n'a retenu aucune hypothèse : mémoire contraire impossible à tester"))
    else:
        retenue_contraire = retenue(contraire)
        change = retenue_contraire is None or cle_texte(retenue_contraire.texte) != cle_texte(retenue_p1.texte)
        cases.append(case("A10", verdict(change and contraire.correctif != p1.correctif),
                          "décision changée : " + ("abstention" if contraire.decision_abstention.abstient
                                                   else f"retenue {retenue_contraire.texte if retenue_contraire else '-'}")))

    # A11-A12 : abstention et calibration.
    a11 = p3.decision_abstention.abstient and p3.correctif is None and bool(p3.decision_abstention.manque)
    cases.append(case("A11", verdict(a11), "manque : " + "; ".join(p3.decision_abstention.manque)))
    a12 = (not p1.decision_abstention.abstient and not p2.decision_abstention.abstient
           and p3.decision_abstention.abstient)
    cases.append(case("A12", verdict(a12), f"seuil {configuration.seuil_abstention} ; confiances "
                      f"{p1.decision_abstention.confiance:.2f}/{p2.decision_abstention.confiance:.2f}/"
                      f"{p3.decision_abstention.confiance:.2f}"))

    cases.append(case_a13(configuration))
    cases.append(case_a14(resultats, llm_reel))

    # A15 : stabilite.
    statut_a15 = verdict(stabilite["identiques"] >= SEUIL_STABILITE and repetitions >= 5)
    cases.append(case(
        "A15", statut_a15, f"{stabilite['identiques']}/{stabilite['repetitions']} conclusions identiques",
        simule=not llm_reel, reel=statut_a15 if llm_reel else NON_MESURE,
        raison_reel=None if llm_reel else "service réel requis : Nightshift",
    ))
    return {
        "modes": {"llm": configuration.llm_provider, "source": configuration.source_provider,
                  "memoire": configuration.memoire_provider, "max_tours_enquete": configuration.max_tours_enquete,
                  "simulation": not llm_reel or not source_reelle},
        "cases": cases,
        "cases_war_room": war_room,
        "echecs": [element["case"] for element in cases + war_room if ECHOUE in (element["statut"], element["reel"])],
        "non_mesures": [element["case"] for element in cases if element["statut"] == NON_MESURE],
        "parties_reelles_non_mesurees": [element["case"] for element in cases if element["reel"] == NON_MESURE],
    }


def afficher_rapport(rapport):
    modes = rapport["modes"]
    afficher(f"Modes : llm={modes['llm']} source={modes['source']} memoire={modes['memoire']} "
             f"max_tours={modes['max_tours_enquete']}" + (" (SIMULATION)" if modes["simulation"] else ""))
    afficher("Case | Statut | Partie reelle | Libelle | Detail")
    for element in rapport["cases"]:
        statut = element["statut"] + (" (simule)" if element["simule"] else "")
        reel = "-" if element["reel"] is None else element["reel"] + (
            f" : {element['raison_reel']}" if element["raison_reel"] else "")
        afficher(f"{element['case']} | {statut} | {reel} | {element['libelle']} | {element['detail']}")
    for element in rapport.get("cases_war_room", []):
        statut = element["statut"] + (" (simule)" if element["simule"] else "")
        reel = "-" if element["reel"] is None else element["reel"] + (
            f" : {element['raison_reel']}" if element["raison_reel"] else "")
        afficher(f"{element['case']} | {statut} | {reel} | {element['libelle']} | {element['detail']}")
    afficher("Bilan : " + ("aucun ECHOUE" if not rapport["echecs"] else "ECHOUE : " + ", ".join(rapport["echecs"])))


def main(argv=None, fabrique_llm=None):
    parseur = argparse.ArgumentParser(description="Recette automatique A1-A15 du Module A.")
    parseur.add_argument("--confirmer", action="store_true", help="Autoriser les appels a un service reel")
    parseur.add_argument("--json", type=Path, default=RACINE / "data" / "recette.json")
    parseur.add_argument("--repetitions", type=int, default=5)
    arguments = parseur.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        # Console Windows : caracteres hors page de code remplaces plutot qu'une erreur.
        sys.stdout.reconfigure(errors="replace")
    try:
        configuration = charger()
        # P1, P2, P3, plafond, memoire contraire, puis les repetitions de stabilite.
        appels_prevus = (4 + arguments.repetitions) * configuration.max_tours_enquete + PLAFOND_ESSAI
        if not autoriser(arguments.confirmer, appels_prevus, executions=5 + arguments.repetitions):
            return 2
        memoire_reelle = None
        if configuration.memoire_provider == "postgres":
            if arguments.confirmer:
                memoire_reelle = MemoireEnquete()
            else:
                afficher("Memoire Postgres : relancer avec --confirmer pour ecrire le souvenir de P1 (A7 reel).")
        faux = annoncer_faux_services()
        rapport = evaluer(configuration, fabrique_llm, arguments.repetitions, memoire_reelle)
        if faux:
            rapport["avertissement"] = ETIQUETTE_FAUX_SERVICES
        afficher_rapport(rapport)
        destination = arguments.json.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(nettoyer(json.dumps(rapport, ensure_ascii=False, indent=2)) + "\n", encoding="utf-8")
        afficher(f"Rapport ecrit : {destination.name}")
        return 0 if not rapport["echecs"] else 1
    except Exception:
        afficher("Recette impossible : verifier la configuration et les services ; aucun detail sensible affiche.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
