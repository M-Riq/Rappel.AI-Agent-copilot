"""API Rappel : contrat Phase 0/1 du squelette et moteur d'enquête en arrière-plan.

Contrat du squelette conservé (tests/test_api.py, ui/app.py) :
- GET /healthz (alias /sante) : statut et modes llm, source, memoire ;
- POST /incidents : 201 {"id"}, 422 si le JSON Schema échoue, 409 si doublon ;
- GET /incidents/{id}/evenements : vue d'affichage. En mode fake, l'incident « p1 »
  des fixtures est servi comme avant (et n'est jamais présenté comme une enquête
  réelle) ; hors mode fake, ce sont les événements de l'enquête réelle ;
- POST /correctifs/{id}/decision : décision enregistrée, jamais d'exécution de patch.

Ajouts : chaque POST /incidents lance l'enquête du moteur dans un thread ; fil en direct
(GET /incidents/{id}/enquete/evenements), résultat (GET /incidents/{id}), liste
(GET /incidents), retour Action, mémoire (lecture et réinitialisation confirmée).
Le moteur (LangGraph) est importé au lancement d'une enquête : /healthz reste léger.
"""

import json
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from jsonschema import ValidationError
from pydantic import BaseModel, ConfigDict, StrictBool, field_validator

from rappel.config import RACINE, charger
from rappel.contrats import valider

# État partagé de l'application globale (les tests du squelette les manipulent).
_INCIDENTS = {}
_DECISIONS = {}
_ENQUETES = {}
_VERROU = RLock()
CHAMPS_FIXTURE = ("signature", "logs", "commits_recents", "schema", "metriques")
MESSAGE_ERREUR = "Enquête impossible. Consulter la configuration et réessayer."
MESSAGE_MOTEUR_ABSENT = "Moteur d'enquête indisponible : installer requirements.txt (langgraph)."


class DecisionCorrectif(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["appliquer", "refuser"]


class IncidentDepuisDemo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str | None = None


class ResultatAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resultat: Literal["reussi", "echoue"]


class ReinitialisationMemoire(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmer: StrictBool
    incidents: StrictBool = False

    @field_validator("confirmer")
    @classmethod
    def confirmation_obligatoire(cls, valeur: bool) -> bool:
        if not valeur:
            raise ValueError("confirmer doit valoir true pour effacer la mémoire")
        return valeur


def _maintenant():
    return datetime.now(timezone.utc).isoformat()


def _fixture(nom):
    return json.loads((RACINE / "fixtures" / nom).read_text(encoding="utf-8"))


def mode_memoire(memoire):
    """json, postgres ou fallback-json selon le backend effectivement utilise."""
    from rappel.interfaces.memoire import MemoireJSON, MemoirePostgres

    backend = getattr(memoire, "backend", memoire)
    if getattr(backend, "mode", None):
        return backend.mode
    if isinstance(backend, MemoireJSON):
        return "json"
    if isinstance(backend, MemoirePostgres):
        return "postgres"
    return type(backend).__name__


def _json_contrat(objet):
    return objet.model_dump(mode="json", exclude_none=True)


def creer_app(
    memoire=None, llm=None, abstention=None, *,
    executeur_enquete: Callable[[Callable[[], None]], object] | None = None,
    traceur=None, mode_preuves: Literal["sql", "outils"] = "sql", fabrique_run_sql=None,
    incidents: dict | None = None, decisions: dict | None = None, enquetes: dict | None = None,
    verrou=None,
) -> FastAPI:
    """Dépendances optionnelles : sinon choisies à chaque enquête selon la configuration."""
    if mode_preuves not in ("sql", "outils"):
        raise ValueError("mode_preuves doit valoir sql ou outils")
    from rappel.enquete.traceur import creer_traceur, proteger_traceur

    incidents = {} if incidents is None else incidents
    decisions = {} if decisions is None else decisions
    enquetes = {} if enquetes is None else enquetes
    verrou = RLock() if verrou is None else verrou
    verrou_execution = RLock()
    suivi = proteger_traceur(creer_traceur() if traceur is None else traceur)
    travailleurs = ThreadPoolExecutor(max_workers=1, thread_name_prefix="enquete")
    en_cours = set()

    def soumettre_par_defaut(tache):
        futur = travailleurs.submit(tache)
        en_cours.add(futur)
        futur.add_done_callback(en_cours.discard)
        return futur

    soumettre = executeur_enquete or soumettre_par_defaut

    @asynccontextmanager
    async def cycle_vie(application):
        yield
        # Attendre les enquêtes lancées sans fermer l'exécuteur : l'application peut redémarrer.
        wait(list(en_cours))

    application = FastAPI(
        title="Rappel — API d'enquête", lifespan=cycle_vie,
        description="API locale sans authentification : ne pas l'exposer publiquement.",
    )

    def memoire_courante():
        if memoire is not None:
            return memoire
        from rappel.enquete.adaptateurs import MemoireEnquete

        return MemoireEnquete()

    def etat_enquete(identifiant):
        """État de l'enquête d'un incident enregistré, sinon None (verrou tenu par l'appelant)."""
        return enquetes.get(identifiant) if identifiant in incidents else None

    def obtenir(identifiant):
        etat = etat_enquete(identifiant)
        if etat is None:
            raise HTTPException(404, "Incident inconnu.")
        return etat

    def ajouter_decision(identifiant, etat, texte):
        from rappel.enquete.modeles import EvenementEnquete

        etat["evenements"].append(EvenementEnquete(
            incident_id=identifiant, etape=len(etat["evenements"]) + 1, type="decision",
            acteur="systeme", texte=texte, horodatage=_maintenant(),
        ))

    def executer(identifiant, contexte, jeton):
        def courant():
            etat = enquetes.get(identifiant)
            return etat if etat is not None and etat["jeton"] is jeton else None

        def recevoir(evenement):
            with verrou:
                etat = courant()
                if etat is not None:
                    etat["evenements"].append(evenement.model_copy(deep=True))

        try:
            from rappel.enquete import enqueter
            from rappel.enquete.adaptateurs import creer_llm_enquete
            from rappel.interfaces.abstention import AbstentionSeuil
        except ImportError:
            with verrou:
                etat = courant()
                if etat is not None:
                    etat.update(statut="erreur", message=MESSAGE_MOTEUR_ABSENT)
            return
        try:
            # Budgets LLM et mémoire sont mutables : une enquête à la fois.
            with verrou_execution:
                memoire_utilisee = memoire_courante()
                resultat = enqueter(
                    contexte, memoire_utilisee, llm or creer_llm_enquete(),
                    abstention or AbstentionSeuil(), sur_evenement=recevoir, traceur=suivi,
                    mode_preuves=mode_preuves, fabrique_run_sql=fabrique_run_sql,
                    # Le résultat n'est connu qu'après l'action : souvenir marqué non confirmé.
                    resultat_confirme=False,
                )
            with verrou:
                etat = courant()
                if etat is not None:
                    etat.update(resultat=resultat, statut="termine", memoire_mode=mode_memoire(memoire_utilisee))
        except Exception:
            # Aucun détail d'exception exposé ni journalisé (secrets possibles).
            with verrou:
                etat = courant()
                if etat is not None:
                    etat.update(statut="erreur", message=MESSAGE_ERREUR)

    def sante():
        configuration = charger()
        reponse = {
            "statut": "ok",
            "llm": configuration.llm_provider,
            "source": configuration.source_provider,
            "memoire": configuration.memoire_provider,
        }
        if configuration.memoire_provider == "postgres":
            from rappel.interfaces.memoire import mode_memoire_effectif

            # Cle en plus seulement pour Postgres : mode observe, sans connexion supplementaire.
            reponse["memoire_mode_effectif"] = mode_memoire_effectif()
        return reponse

    application.get("/healthz")(sante)
    application.get("/sante")(sante)

    @application.post("/incidents", status_code=201)
    def creer_incident(contexte: dict):
        try:
            valider("contexte_incident", contexte)
        except ValidationError:
            raise HTTPException(422, "ContexteIncident non conforme au contrat.") from None
        return enregistrer_incident(contexte)

    @application.post("/incidents/depuis-demo", status_code=201)
    def creer_incident_depuis_demo(corps: IncidentDepuisDemo | None = None):
        """Option : contexte construit depuis l'état courant du pipeline de démo (simulé)."""
        from rappel.ingestion.contexte_demo import construire_contexte

        try:
            contexte, infos = construire_contexte(incident_id=corps.incident_id if corps else None)
        except (OSError, KeyError, ValueError):
            raise HTTPException(409, "Pipeline de démo absent ou illisible : lancer l'injecteur (reset, p1, p2 ou p3).") from None
        reponse = enregistrer_incident(contexte)
        return {**reponse, "source": "pipeline_demo", "simulation": True, "signature": contexte["signature"],
                "statut_pipeline": infos["statut_pipeline"], "reducteur_logs": infos["reducteur_logs"]}

    def enregistrer_incident(contexte):
        identifiant = contexte["incident_id"]
        jeton = object()
        with verrou:
            if identifiant in incidents:
                raise HTTPException(409, "Cet incident existe déjà.")
            incidents[identifiant] = contexte
            decisions.pop(identifiant, None)
            enquetes[identifiant] = {
                "jeton": jeton, "statut": "en_cours", "evenements": [], "resultat": None,
                "message": None, "cree_le": _maintenant(), "resultat_action": None,
                "llm": charger().llm_provider, "memoire_mode": None,
            }
        try:
            soumettre(lambda: executer(identifiant, contexte, jeton))
        except Exception:
            with verrou:
                etat = enquetes.get(identifiant)
                if etat is not None and etat["jeton"] is jeton:
                    etat.update(statut="erreur", message="Impossible de démarrer l'enquête.")
        return {"id": identifiant}

    @application.get("/incidents")
    def lister_incidents(
        limite: int = Query(default=50, ge=1, le=200),
        statut: Literal["en_cours", "termine", "erreur"] | None = None,
    ):
        with verrou:
            resumes = []
            for identifiant in reversed(list(incidents)):
                etat = enquetes.get(identifiant)
                if etat is None or (statut is not None and etat["statut"] != statut):
                    continue
                resultat = etat["resultat"]
                resumes.append({
                    "incident_id": identifiant, "signature": incidents[identifiant]["signature"],
                    "statut": etat["statut"], "cree_le": etat["cree_le"],
                    "nb_evenements": len(etat["evenements"]),
                    "a_correctif": resultat is not None and resultat.correctif is not None,
                    "abstention": resultat.decision_abstention.abstient if resultat is not None else None,
                    "decision_humaine": decisions.get(identifiant),
                    "resultat_action": etat["resultat_action"],
                })
                if len(resumes) == limite:
                    break
            return resumes

    def comparer_war_room(identifiant: str, confirmer: bool):
        """Même incident rejoué sans puis avec war room, chacun sur une mémoire vide temporaire."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from rappel.enquete import enqueter
        from rappel.enquete.adaptateurs import MemoireEnquete, creer_llm_enquete
        from rappel.interfaces.abstention import AbstentionSeuil

        configuration = charger()
        with verrou:
            contexte = incidents.get(identifiant)
        if contexte is None:
            raise HTTPException(404, f"Incident inconnu : {identifiant}.")
        if configuration.llm_provider != "fake" and not confirmer:
            raise HTTPException(409, "Rejeu avec un LLM réel : ajouter confirmer=true (deux enquêtes, appels facturés).")

        def mesurer(war_room_active):
            with TemporaryDirectory() as dossier:
                resultat = enqueter(
                    contexte, MemoireEnquete.json(Path(dossier) / "memoire.json"), llm or creer_llm_enquete(),
                    abstention or AbstentionSeuil(), mode_preuves=mode_preuves, fabrique_run_sql=fabrique_run_sql,
                    war_room_active=war_room_active,
                )
            metriques = resultat.metriques
            retenue = next((h for h in resultat.hypotheses if h.statut == "retenue"), None)
            return {
                "war_room_active": war_room_active, "nb_appels_llm": metriques.nb_appels_llm,
                "nb_evenements": metriques.nb_evenements, "nb_tours_war_room": metriques.nb_tours_war_room,
                "nb_appels_llm_war_room": metriques.nb_appels_llm_war_room, "nb_objections": metriques.nb_objections,
                "nb_objections_sans_reponse": metriques.nb_objections_sans_reponse,
                "confiance_hypothese": None if retenue is None else retenue.confiance,
                "confiance_decision": resultat.decision_abstention.confiance,
                "abstention": resultat.decision_abstention.abstient,
                "preuves_citees": [] if retenue is None else list(retenue.preuves),
            }

        try:
            with verrou_execution:
                sans, avec = mesurer(False), mesurer(True)
        except Exception:
            raise HTTPException(500, "Rejeu impossible ; aucun détail sensible exposé.") from None
        simulation = configuration.llm_provider == "fake"
        return {
            "mode": "war_room", "incident": identifiant, "sans": sans, "avec": avec,
            "confiance_avant": sans["confiance_hypothese"], "confiance_apres": avec["confiance_hypothese"],
            "difference": {"appels_llm": avec["nb_appels_llm"] - sans["nb_appels_llm"],
                           "evenements": avec["nb_evenements"] - sans["nb_evenements"]},
            "preuves_inchangees": sans["preuves_citees"] == avec["preuves_citees"],
            "simulation": simulation,
            "note": ("simulé : LLM fake, le débat est scripté et ne mesure pas un vrai modèle" if simulation
                     else "rejeu réel : deux enquêtes sur une mémoire vide temporaire"),
        }

    @application.get("/comparaison")
    def comparer(a: str, b: str | None = None, mode: Literal["incidents", "war_room"] = "incidents",
                 confirmer: bool = False):
        if mode == "war_room":
            return comparer_war_room(a, confirmer)
        if b is None:
            raise HTTPException(422, "Paramètre b requis pour comparer deux incidents.")
        with verrou:
            resultats = {}
            for identifiant in (a, b):
                if etat_enquete(identifiant) is None:
                    raise HTTPException(404, f"Incident inconnu : {identifiant}.")
            for identifiant in (a, b):
                etat = etat_enquete(identifiant)
                if etat["statut"] != "termine" or etat["resultat"] is None:
                    raise HTTPException(409, f"Enquête non terminée : {identifiant}.")
                resultats[identifiant] = (asdict(etat["resultat"].metriques), etat["llm"])
        metriques_a, llm_a = resultats[a]
        metriques_b, llm_b = resultats[b]

        def reduction(valeur_a, valeur_b):
            if valeur_a in (None, 0) or valeur_b is None:
                return None
            return round(100 * (valeur_a - valeur_b) / valeur_a, 2)

        def tokens(metriques):
            if metriques["nb_tokens_entree"] is None or metriques["nb_tokens_sortie"] is None:
                return None
            return metriques["nb_tokens_entree"] + metriques["nb_tokens_sortie"]

        def rapport(valeur_a, valeur_b):
            return None if not valeur_a else round(valeur_b / valeur_a, 4)

        evenements = rapport(metriques_a["nb_evenements"], metriques_b["nb_evenements"])
        appels = rapport(metriques_a["nb_appels_llm"], metriques_b["nb_appels_llm"])
        return {
            "a": a, "b": b, "metriques": {"a": metriques_a, "b": metriques_b},
            "reductions_pct": {
                "evenements": reduction(metriques_a["nb_evenements"], metriques_b["nb_evenements"]),
                "hypotheses_testees": reduction(metriques_a["nb_hypotheses_testees"], metriques_b["nb_hypotheses_testees"]),
                "appels_llm": reduction(metriques_a["nb_appels_llm"], metriques_b["nb_appels_llm"]),
                "tokens": reduction(tokens(metriques_a), tokens(metriques_b)),
                "duree": reduction(metriques_a["duree_ms"], metriques_b["duree_ms"]),
            },
            # Critere A9 du cahier : b <= 60 % des evenements de a ET moins d'appels LLM.
            "regle_60pc": {
                "evenements_b_sur_a": evenements, "appels_b_sur_a": appels,
                "respecte": evenements is not None and evenements <= 0.6 and appels is not None and appels < 1,
            },
            "simulation": "fake" in (llm_a, llm_b),
            "note": "la durée n'est pas une preuve de gain avec un LLM simulé",
        }

    @application.get("/incidents/{identifiant}")
    def lire_incident(identifiant: str):
        with verrou:
            etat = obtenir(identifiant)
            reponse = {
                "incident_id": identifiant, "statut": etat["statut"], "llm": etat["llm"],
                "memoire_mode": etat["memoire_mode"],
                # En mode fake, le raisonnement est simulé : jamais présenté comme réel.
                "enquete_simulee": etat["llm"] == "fake",
            }
            if etat["statut"] == "erreur":
                reponse["message"] = etat["message"]
            resultat = etat["resultat"]
            if etat["statut"] == "termine" and resultat is not None:
                reponse.update(
                    hypotheses=[_json_contrat(hypothese) for hypothese in resultat.hypotheses],
                    correctif=None if resultat.correctif is None else _json_contrat(resultat.correctif),
                    decision_abstention=asdict(resultat.decision_abstention),
                    metriques=asdict(resultat.metriques),
                    mode_preuves=resultat.mode_preuves, source_preuves=resultat.source_preuves,
                    preuves_simulees=(resultat.source_preuves or "").startswith("simule"),
                    decision_humaine=decisions.get(identifiant),
                    resultat_action=etat["resultat_action"],
                )
            return jsonable_encoder(reponse)

    @application.get("/incidents/{identifiant}/enquete/evenements")
    def lire_fil_enquete(identifiant: str, apres: int = Query(default=0, ge=0),
                         jusqua: int | None = Query(default=None, ge=0)):
        """Fil en direct (étapes > apres) ; avec jusqua, rejeu en lecture seule jusqu'à cette étape incluse."""
        if jusqua is not None and jusqua < apres:
            raise HTTPException(422, "jusqua doit être supérieur ou égal à apres.")
        with verrou:
            etat = obtenir(identifiant)
            return [_json_contrat(evenement) for evenement in etat["evenements"]
                    if evenement.etape > apres and (jusqua is None or evenement.etape <= jusqua)]

    @application.get("/incidents/{identifiant}/evenements")
    def lire_evenements(identifiant: str):
        with verrou:
            contexte = incidents.get(identifiant)
            etat = etat_enquete(identifiant)
            if identifiant != "p1" and contexte is None:
                raise HTTPException(404, "Incident inconnu.")
            reels = [_json_contrat(evenement) for evenement in etat["evenements"]] if etat else []
        if charger().llm_provider != "fake":
            if etat is None:
                raise HTTPException(
                    501, "La fixture P1 n'est servie qu'en mode fake : soumettre l'incident pour une enquête réelle."
                )
            return reels
        # Mode fake : vue d'affichage de la fixture P1, comme dans le squelette Phase 0/1.
        contexte_p1 = _fixture("contexte_p1.json")
        contexte = contexte or contexte_p1
        if any(contexte[champ] != contexte_p1[champ] for champ in CHAMPS_FIXTURE):
            return []
        return [{**evenement, "incident_id": identifiant} for evenement in _fixture("evenements_p1.json")]

    @application.post("/correctifs/{identifiant}/decision")
    def decider_correctif(identifiant: str, decision: DecisionCorrectif):
        with verrou:
            etat = etat_enquete(identifiant)
            resultat = etat["resultat"] if etat else None
            if resultat is not None and resultat.correctif is not None:
                decisions[identifiant] = decision.decision
                correctif = resultat.correctif
                if not (charger().action_reelle_demo and decision.decision == "appliquer"):
                    ajouter_decision(identifiant, etat, f"Décision humaine : {decision.decision} ; aucun patch exécuté")
                    return {
                        "id": identifiant, "decision": decision.decision, "execute": False,
                        "message": "Décision enregistrée ; aucun patch exécuté par l'API.",
                    }
        if resultat is not None and resultat.correctif is not None:
            # ACTION_REELLE_DEMO=1 : execution sur le pipeline de demo isole uniquement.
            from rappel.action.executeur import ECHEC_RESTAURE, EXECUTE, executer_correctif

            with verrou_execution:
                action = executer_correctif(correctif, decision.decision)
                resultat_action = {EXECUTE: "reussi", ECHEC_RESTAURE: "echoue"}.get(action["statut"])
                if resultat_action is not None and resultat.enregistrement is not None:
                    try:
                        memoire_courante().mettre_a_jour_resultat(resultat.enregistrement.signature, resultat_action)
                    except Exception:
                        resultat_action = None
            with verrou:
                if resultat_action is not None:
                    etat["resultat_action"] = resultat_action
                ajouter_decision(identifiant, etat, f"Décision humaine : appliquer ; action sur le pipeline de démo : "
                                 f"{action['statut']} ({action['message']})")
            return {
                "id": identifiant, "decision": decision.decision, "execute": action["statut"] == EXECUTE,
                "message": action["message"], "action": action,
            }
        with verrou:
            if identifiant == "p1" and charger().llm_provider == "fake":
                decisions[identifiant] = decision.decision
                return {
                    "id": identifiant, "decision": decision.decision, "execute": False,
                    "message": "Décision enregistrée en mémoire ; aucun patch exécuté en Phase 0/1.",
                }
            if etat is not None and etat["statut"] == "en_cours":
                raise HTTPException(409, "Enquête en cours : aucun correctif disponible pour l'instant.")
            if etat is not None:
                raise HTTPException(409, "Aucun correctif disponible (abstention ou erreur).")
        raise HTTPException(404, "Correctif inconnu.")

    @application.post("/incidents/{identifiant}/resultat-action", status_code=202)
    def enregistrer_resultat_action(identifiant: str, corps: ResultatAction):
        with verrou:
            etat = obtenir(identifiant)
            if etat["statut"] != "termine":
                raise HTTPException(409, "Enquête non terminée.")
            signature = incidents[identifiant]["signature"]
        try:
            with verrou_execution:
                mise_a_jour = memoire_courante().mettre_a_jour_resultat(signature, corps.resultat)
        except Exception:
            raise HTTPException(500, "Mise à jour de la mémoire impossible.") from None
        with verrou:
            etat = obtenir(identifiant)
            etat["resultat_action"] = corps.resultat
            ajouter_decision(identifiant, etat, f"Résultat Action : {corps.resultat} ; mémoire mise à jour={mise_a_jour}")
        return {"incident_id": identifiant, "resultat": corps.resultat, "memoire_mise_a_jour": mise_a_jour}

    def graphe_complet():
        from rappel.memoire.graphe_relations import depuis_memoire

        with verrou:
            connus = []
            for identifiant, contexte in incidents.items():
                etat = enquetes.get(identifiant) or {}
                resultat = etat.get("resultat")
                enregistrement = getattr(resultat, "enregistrement", None)
                connus.append({"incident_id": identifiant, "signature": contexte["signature"],
                               "enregistrement": None if enregistrement is None else enregistrement.model_dump(mode="json")})
        try:
            with verrou_execution:
                courante = memoire_courante()
                return depuis_memoire(courante, connus), mode_memoire(courante)
        except Exception:
            raise HTTPException(500, "Lecture de la mémoire impossible.") from None

    @application.get("/graphe")
    def lire_graphe():
        """Graphe de relations (déterministe, sans LLM) : incidents, signatures, hypothèses, causes, correctifs."""
        from rappel.memoire.graphe_relations import en_mermaid

        graphe, mode = graphe_complet()
        return {**graphe, "mermaid": en_mermaid(graphe), "memoire_mode": mode}

    @application.get("/graphe/{identifiant}")
    def lire_graphe_incident(identifiant: str, profondeur: int = Query(default=2, ge=1, le=4)):
        from rappel.memoire.graphe_relations import en_mermaid, voisinage

        graphe, mode = graphe_complet()
        noeud = f"incident:{identifiant}"
        if not any(element["id"] == noeud for element in graphe["noeuds"]):
            raise HTTPException(404, "Incident absent du graphe de relations.")
        sous_graphe = voisinage(graphe, noeud, profondeur)
        return {**sous_graphe, "incident": identifiant, "mermaid": en_mermaid(sous_graphe), "memoire_mode": mode}

    @application.get("/boucle/etat")
    def lire_etat_boucle():
        """Dernier cycle de la boucle des 4 agents (journal, lecture seule, sans secret)."""
        from rappel.orchestration.boucle import lire_dernier_cycle

        return lire_dernier_cycle()

    @application.get("/memoire")
    def lire_memoire(signature: str | None = None):
        try:
            with verrou_execution:
                courante = memoire_courante()
                souvenirs = courante.lister() if signature is None else courante.chercher(signature)
                from rappel.enquete.graphe import statut_souvenir

                statuts = [statut_souvenir(courante, souvenir) for souvenir in souvenirs]
        except Exception:
            raise HTTPException(500, "Lecture de la mémoire impossible.") from None
        refutations = {}
        for souvenir in souvenirs:
            textes = refutations.setdefault(souvenir.signature, [])
            textes.extend(texte for texte in souvenir.hypotheses_refutees if texte not in textes)
        return jsonable_encoder({
            "souvenirs": [souvenir.model_dump(mode="json") for souvenir in souvenirs],
            "refutations_par_signature": refutations,
            # reussi, echoue ou non_confirme (souvenir provisoire, action non encore confirmée).
            "statuts_resultat": statuts,
        })

    @application.post("/memoire/reinitialiser")
    def reinitialiser_memoire(corps: ReinitialisationMemoire):
        def verifier_en_cours():
            if any(enquetes.get(identifiant, {}).get("statut") == "en_cours" for identifiant in incidents):
                raise HTTPException(409, "Réinitialisation interdite pendant une enquête.")

        with verrou:
            verifier_en_cours()
        with verrou_execution, verrou:
            verifier_en_cours()
            try:
                nombre = memoire_courante().vider()
            except Exception:
                raise HTTPException(500, "Réinitialisation de la mémoire impossible.") from None
            supprimes = len(incidents) if corps.incidents else 0
            if corps.incidents:
                incidents.clear()
                enquetes.clear()
                decisions.clear()
            return {"souvenirs_supprimes": nombre, "incidents_supprimes": supprimes}

    return application


app = creer_app(incidents=_INCIDENTS, decisions=_DECISIONS, enquetes=_ENQUETES, verrou=_VERROU)
