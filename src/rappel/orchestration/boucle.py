"""Boucle des 4 agents : Ingestion -> Mémoire (lecture) -> Enquête -> Action -> retour en Mémoire.

- Ingestion : état du pipeline de démo (simulé), contexte construit et validé par le contrat ;
- Mémoire : souvenirs et hypothèses réfutées lus AVANT l'enquête ; l'enquête écrit son souvenir ;
- Enquête : moteur SQL, war room selon la configuration (WAR_ROOM_ACTIVE) ;
- Action : rayon d'impact calculé par le code ; exécution seulement si ACTION_REELLE_DEMO=1 et
  uniquement sur le pipeline de démo isolé, sinon proposition seule ;
- Retour : le résultat de l'action (réussi/échoué) est écrit dans la mémoire.

Chaque passage entre agents écrit une ligne dans le journal de boucle (JSON Lines). Ce journal
n'est PAS un événement du contrat : il ne contient que des statuts, durées et modes, jamais de
texte du modèle ni de secret.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import monotonic, perf_counter, sleep
from uuid import uuid4

from rappel.config import RACINE, Configuration, charger

AGENTS = ("ingestion", "memoire", "enquete", "action", "retour")
SIMULE, REEL = "simulé", "réel"
PROPOSITION = "proposition"
MOTS_SECRETS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE_URL")


@dataclass
class CycleBoucle:
    """Résumé JSON du cycle (sans secret) et résultat complet de l'enquête (ou None)."""

    resume: dict
    resultat: object | None = None


def chemin_journal(configuration: Configuration | None = None) -> Path:
    chemin = Path((configuration or charger()).boucle_journal_path)
    return chemin if chemin.is_absolute() else RACINE / chemin


def assainir(texte: str) -> str:
    """Masque toute valeur de variable d'environnement sensible qui apparaîtrait dans un texte."""
    texte = str(texte)
    for nom, valeur in os.environ.items():
        if valeur and len(valeur) >= 4 and any(mot in nom.upper() for mot in MOTS_SECRETS):
            texte = texte.replace(valeur, "[MASQUE]")
    return texte


def _maintenant() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def modes(configuration: Configuration, memoire=None) -> dict:
    from rappel.interfaces.memoire import mode_memoire

    mode_memoire_effectif = mode_memoire(memoire) if memoire is not None else configuration.memoire_provider
    return {
        "ingestion": SIMULE,  # le pipeline de démo est toujours simulé
        "enquete": REEL if configuration.llm_provider != "fake" and configuration.source_provider != "fichier" else SIMULE,
        "llm": configuration.llm_provider,
        "source": configuration.source_provider,
        "memoire": REEL if mode_memoire_effectif == "postgres" else SIMULE,
        "memoire_mode": mode_memoire_effectif,
        "action": SIMULE,  # seul le pipeline de démo isolé peut être modifié
    }


class Journal:
    def __init__(self, chemin: Path, cycle: str):
        self.chemin = Path(chemin)
        self.cycle = cycle
        self.lignes: list[dict] = []

    def ecrire(self, agent: str, etape: str, statut: str, debut: float, mode: str, detail: str = "") -> dict:
        ligne = {
            "horodatage": _maintenant(), "cycle": self.cycle, "agent": agent, "etape": etape,
            "statut": statut, "duree_ms": round((perf_counter() - debut) * 1000, 1), "mode": mode,
            "detail": assainir(detail)[:300],
        }
        self.lignes.append(ligne)
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        with self.chemin.open("a", encoding="utf-8") as fichier:
            fichier.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        return ligne


def _memoire_par_defaut():
    from rappel.enquete.adaptateurs import MemoireEnquete

    return MemoireEnquete()


def executer_boucle(scenario: str | None = None, *, memoire=None, fabrique_llm=None, dossier=None,
                    configuration: Configuration | None = None, journal: Path | None = None,
                    war_room_active: bool | None = None, incident_id: str | None = None) -> CycleBoucle:
    """Un cycle complet. Sans scénario, la boucle part de l'état courant du pipeline de démo."""
    from rappel.action.executeur import ECHEC_RESTAURE, EXECUTE, executer_correctif
    from rappel.enquete import enqueter
    from rappel.enquete.adaptateurs import creer_llm_enquete
    from rappel.ingestion.contexte_demo import construire_contexte
    from rappel.interfaces.abstention import AbstentionSeuil
    from rappel.pipeline_demo import empreinte_dossier, injecter

    configuration = configuration or charger()
    memoire = memoire if memoire is not None else _memoire_par_defaut()
    cycle = f"cycle-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:6]}"
    suivi = Journal(journal or chemin_journal(configuration), cycle)
    mode = modes(configuration, memoire)
    resume = {"cycle": cycle, "scenario": scenario, "incident_id": None, "signature": None, "statut": "en_cours",
              "simulation": SIMULE in (mode["enquete"], mode["ingestion"]), "modes": mode,
              "memoire": {}, "enquete": {}, "action": {}, "agents": suivi.lignes}

    # 1. Ingestion
    debut = perf_counter()
    try:
        if scenario is not None:
            injecter(scenario, dossier)
        contexte, infos = construire_contexte(dossier, incident_id)
    except (OSError, KeyError, ValueError):
        suivi.ecrire("ingestion", "contexte", "erreur", debut, mode["ingestion"], "pipeline de démo absent ou illisible")
        resume["statut"] = "erreur"
        return CycleBoucle(resume)
    resume.update(incident_id=contexte["incident_id"], signature=contexte["signature"])
    suivi.ecrire("ingestion", "contexte", "ok", debut, mode["ingestion"],
                 f"pipeline {infos['statut_pipeline']} ; {infos['nb_modeles']} modèle(s) de logs ; "
                 f"réducteur {infos['reducteur_logs']} ; contexte conforme au contrat")
    if infos["statut_pipeline"] != "rouge":
        suivi.ecrire("ingestion", "verdict", "aucune_anomalie", perf_counter(), mode["ingestion"],
                     "pipeline vert : aucune enquête lancée")
        resume["statut"] = "aucune_anomalie"
        return CycleBoucle(resume)

    # 2. Mémoire (lecture avant l'enquête)
    debut = perf_counter()
    souvenirs = memoire.chercher(contexte["signature"])
    refutees = memoire.hypotheses_refutees(contexte["signature"])
    resume["memoire"] = {"souvenirs_avant": len(souvenirs), "refutees_connues": len(refutees)}
    suivi.ecrire("memoire", "lecture", "ok", debut, mode["memoire"],
                 f"{len(souvenirs)} souvenir(s), {len(refutees)} hypothèse(s) réfutée(s) pour la signature")

    # 3. Enquête (le moteur écrit lui-même le souvenir s'il conclut)
    debut = perf_counter()
    llm = (fabrique_llm or (lambda: creer_llm_enquete(configuration)))()
    try:
        resultat = enqueter(contexte, memoire, llm, AbstentionSeuil(), mode_preuves="sql",
                            war_room_active=war_room_active, resultat_confirme=False)
    except Exception:
        suivi.ecrire("enquete", "enquete", "erreur", debut, mode["enquete"], "enquête interrompue (détail masqué)")
        resume["statut"] = "erreur"
        return CycleBoucle(resume)
    finally:
        fermer = getattr(llm, "fermer", None)
        if callable(fermer):
            fermer()
    metriques = resultat.metriques
    abstention = resultat.decision_abstention
    resume["enquete"] = {
        "conclusion": None if resultat.correctif is None else resultat.correctif.justification,
        "abstention": abstention.abstient, "manque": list(abstention.manque),
        "confiance": abstention.confiance, "nb_evenements": metriques.nb_evenements,
        "nb_appels_llm": metriques.nb_appels_llm, "source_preuves": resultat.source_preuves,
        "war_room": {"tours": metriques.nb_tours_war_room, "appels_llm": metriques.nb_appels_llm_war_room,
                     "objections": metriques.nb_objections},
    }
    suivi.ecrire("enquete", "enquete", "abstention" if abstention.abstient else "correctif", debut, mode["enquete"],
                 f"{metriques.nb_evenements} événement(s), {metriques.nb_appels_llm} appel(s) LLM, "
                 f"war room {metriques.nb_tours_war_room} tour(s), confiance {abstention.confiance:.2f}")
    debut = perf_counter()
    ecrit = resultat.enregistrement is not None
    resume["memoire"]["ecrit"] = ecrit
    suivi.ecrire("memoire", "ecriture", "ok" if ecrit else "rien_a_ecrire", debut, mode["memoire"],
                 "souvenir provisoire écrit (résultat à confirmer par l'action)" if ecrit else "abstention : aucun souvenir")

    # 4. Action
    debut = perf_counter()
    empreinte_avant = empreinte_dossier(dossier)
    correctif = resultat.correctif
    if correctif is None:
        action = {"statut": "aucune", "niveau": None, "message": "Abstention : aucune action."}
    elif not configuration.action_reelle_demo:
        action = {"statut": PROPOSITION, "niveau": correctif.niveau_risque,
                  "message": "ACTION_REELLE_DEMO=0 : correctif proposé, rien n'est exécuté."}
    else:
        action = executer_correctif(correctif, dossier=dossier, configuration=configuration)
    empreinte_apres = empreinte_dossier(dossier)
    resume["action"] = {"statut": action["statut"], "niveau": action.get("niveau"), "message": action["message"],
                        "empreinte_avant": empreinte_avant, "empreinte_apres": empreinte_apres,
                        "pipeline_inchange": empreinte_avant == empreinte_apres,
                        "pipeline": action.get("pipeline")}
    suivi.ecrire("action", "rayon_impact", action["statut"], debut, mode["action"],
                 f"niveau {action.get('niveau')} ; {action['message']}")

    # 5. Retour du résultat de l'action dans la mémoire
    debut = perf_counter()
    resultat_action = {EXECUTE: "reussi", ECHEC_RESTAURE: "echoue"}.get(action["statut"])
    if ecrit and resultat_action is not None:
        mis_a_jour = memoire.mettre_a_jour_resultat(contexte["signature"], resultat_action)
        resume["memoire"]["resultat"] = resultat_action
        suivi.ecrire("retour", "resultat_action", resultat_action if mis_a_jour else "non_mis_a_jour", debut,
                     mode["memoire"], f"résultat de l'action écrit en mémoire : {resultat_action}")
    else:
        resume["memoire"]["resultat"] = "non_confirme" if ecrit else None
        suivi.ecrire("retour", "resultat_action", "non_confirme" if ecrit else "sans_objet", debut, mode["memoire"],
                     "aucune action exécutée : résultat non confirmé" if ecrit else "aucun souvenir à mettre à jour")
    resume["statut"] = "termine"
    return CycleBoucle(resume, resultat)


def executer_sequence(scenarios: list[str], **options) -> list[CycleBoucle]:
    return [executer_boucle(scenario, **options) for scenario in scenarios]


def surveiller(*, intervalle_s: float = 5.0, max_cycles: int = 3, duree_max_s: float = 600.0, dossier=None,
               lancer=None, dormir=sleep, horloge=monotonic, arret=None, **options) -> list[CycleBoucle]:
    """Interroge l'état du pipeline et lance la boucle à chaque passage au rouge.

    S'arrête proprement au plafond de cycles, à la durée maximale ou quand `arret()` est vrai.
    Un même état rouge (même horodatage) ne déclenche qu'un cycle.
    """
    from rappel.pipeline_demo import lire_etat

    if max_cycles < 1:
        raise ValueError("max_cycles doit être positif")
    lancer = lancer or executer_boucle
    cycles: list[CycleBoucle] = []
    deja_traite = None
    fin = horloge() + duree_max_s
    while len(cycles) < max_cycles and horloge() < fin and not (arret and arret()):
        try:
            etat = lire_etat(dossier)
        except (OSError, ValueError, KeyError):
            etat = {}
        marque = etat.get("horodatage")
        if etat.get("statut") == "rouge" and marque != deja_traite:
            deja_traite = marque
            cycles.append(lancer(None, dossier=dossier, **options))
            continue
        dormir(intervalle_s)
    return cycles


def lire_dernier_cycle(chemin: Path | None = None) -> dict:
    """Dernier cycle du journal : agents, statuts et modes (lecture seule)."""
    chemin = Path(chemin) if chemin is not None else chemin_journal()
    if not chemin.is_file():
        return {"statut": "aucun_cycle", "cycle": None, "agents": []}
    lignes = []
    for texte in chemin.read_text(encoding="utf-8").splitlines():
        try:
            ligne = json.loads(texte)
        except ValueError:
            continue
        if isinstance(ligne, dict) and "cycle" in ligne:
            lignes.append(ligne)
    if not lignes:
        return {"statut": "aucun_cycle", "cycle": None, "agents": []}
    cycle = lignes[-1]["cycle"]
    agents = [ligne for ligne in lignes if ligne["cycle"] == cycle]
    modes_vus = sorted({ligne.get("mode") for ligne in agents if ligne.get("mode")})
    agents_vus = [ligne["agent"] for ligne in agents]
    termine = "retour" in agents_vus or any(ligne["statut"] in ("aucune_anomalie", "erreur") for ligne in agents)
    return {
        "statut": "termine" if termine else "incomplet",
        "cycle": cycle, "debut": agents[0]["horodatage"], "fin": agents[-1]["horodatage"],
        "agents": [{"agent": ligne["agent"], "etape": ligne["etape"], "statut": ligne["statut"],
                    "duree_ms": ligne["duree_ms"], "mode": ligne["mode"]} for ligne in agents],
        "modes": modes_vus, "simulation": SIMULE in modes_vus,
    }
