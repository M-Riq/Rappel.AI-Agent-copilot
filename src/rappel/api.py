from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict

from .contrats import ContexteIncident, CorrectifPropose, EvenementEnquete
from .enquete import enqueter
from .interfaces.abstention import Abstention, AbstentionSeuil
from .interfaces.llm import LLM, creer_llm
from .interfaces.memoire import Memoire, MemoireJSON


class DecisionHumaine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["appliquer", "refuser"]


class ResultatAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resultat: Literal["reussi", "echoue"]


def creer_app(
    memoire: Memoire, llm: LLM, abstention: Abstention,
    executeur_enquete: Callable[[Callable[[], None]], object] | None = None,
    executeur_action: Callable[[str, CorrectifPropose], None] | None = None,
) -> FastAPI:
    incidents = {}
    verrou = RLock()
    verrou_execution = RLock()
    travailleurs = ThreadPoolExecutor(max_workers=1, thread_name_prefix="enquete")
    soumettre = executeur_enquete or travailleurs.submit

    @asynccontextmanager
    async def cycle_vie(application):
        yield
        travailleurs.shutdown(wait=True)

    application = FastAPI(
        title="Rappel - API d'enquete", lifespan=cycle_vie,
        description="API locale de hackathon, sans authentification. Ne pas exposer publiquement.",
    )

    def obtenir(incident_id):
        incident = incidents.get(incident_id)
        if incident is None:
            raise HTTPException(404, "Incident inconnu")
        return incident

    def ajouter_decision(incident, texte):
        incident["evenements"].append(EvenementEnquete(
            incident_id=incident["contexte"].incident_id,
            etape=len(incident["evenements"]) + 1,
            type="decision", acteur="systeme", texte=texte,
            horodatage=datetime.now(timezone.utc).isoformat(),
        ))

    def executer(contexte):
        def recevoir(evenement):
            with verrou:
                obtenir(contexte.incident_id)["evenements"].append(evenement.model_copy(deep=True))

        try:
            # Les dependances partagees ont des budgets et une memoire mutables.
            with verrou_execution:
                resultat = enqueter(contexte, memoire, llm, abstention, sur_evenement=recevoir)
            with verrou:
                incident = obtenir(contexte.incident_id)
                incident["resultat"] = resultat
                incident["statut"] = "termine"
        except Exception:
            with verrou:
                incident = obtenir(contexte.incident_id)
                incident["statut"] = "erreur"
                incident["message"] = "Enquete impossible. Consulter la configuration et reessayer."

    @application.get("/sante")
    def sante():
        return {"statut": "ok"}

    @application.post("/incidents", status_code=202)
    def creer_incident(contexte: ContexteIncident):
        with verrou:
            if contexte.incident_id in incidents:
                raise HTTPException(409, "Incident deja enregistre")
            incidents[contexte.incident_id] = {
                "contexte": contexte, "statut": "en_cours", "evenements": [],
                "resultat": None, "decision": None,
            }
        try:
            soumettre(lambda: executer(contexte))
        except Exception:
            with verrou:
                incident = obtenir(contexte.incident_id)
                incident["statut"] = "erreur"
                incident["message"] = "Impossible de demarrer l'enquete."
        return {"incident_id": contexte.incident_id, "statut": "en_cours"}

    @application.get("/incidents/{incident_id}")
    def lire_incident(incident_id: str):
        with verrou:
            incident = obtenir(incident_id)
            reponse = {"incident_id": incident_id, "statut": incident["statut"]}
            if incident["statut"] == "erreur":
                reponse["message"] = incident["message"]
            if incident["statut"] == "termine":
                resultat = incident["resultat"]
                reponse.update(
                    hypotheses=resultat.hypotheses, correctif=resultat.correctif,
                    decision_abstention=resultat.decision_abstention, metriques=resultat.metriques,
                    decision_humaine=incident["decision"],
                )
            return jsonable_encoder(reponse)

    @application.get("/incidents/{incident_id}/evenements", response_model=list[EvenementEnquete])
    def lire_evenements(incident_id: str, apres: int = Query(default=0, ge=0)):
        with verrou:
            return [element.model_copy(deep=True) for element in obtenir(incident_id)["evenements"]
                    if element.etape > apres]

    @application.post("/correctifs/{incident_id}/decision", status_code=202)
    def decider_correctif(incident_id: str, corps: DecisionHumaine):
        with verrou:
            incident = obtenir(incident_id)
            resultat = incident["resultat"]
            if resultat is None or resultat.correctif is None:
                raise HTTPException(409, "Aucun correctif disponible")
            if incident["decision"] is not None:
                raise HTTPException(409, "Decision deja prise")
            incident["decision"] = corps.decision
            correctif = resultat.correctif.model_copy(deep=True)
            ajouter_decision(incident, f"Decision humaine : {corps.decision}")
        if corps.decision == "appliquer" and executeur_action is not None:
            def action():
                try:
                    executeur_action(incident_id, correctif)
                except Exception:
                    with verrou:
                        ajouter_decision(obtenir(incident_id), "Execution Action indisponible")
            try:
                soumettre(action)
            except Exception:
                with verrou:
                    ajouter_decision(obtenir(incident_id), "Execution Action indisponible")
        return {"incident_id": incident_id, "decision": corps.decision}

    @application.post("/incidents/{incident_id}/resultat-action", status_code=202)
    def enregistrer_resultat_action(incident_id: str, corps: ResultatAction):
        with verrou:
            incident = obtenir(incident_id)
            if incident["statut"] != "termine":
                raise HTTPException(409, "Enquete non terminee")
            signature = incident["contexte"].signature
        try:
            with verrou_execution:
                mise_a_jour = memoire.mettre_a_jour_resultat(signature, corps.resultat)
        except Exception:
            raise HTTPException(500, "Mise a jour de la memoire impossible") from None
        with verrou:
            ajouter_decision(obtenir(incident_id), f"Resultat Action : {corps.resultat}; memoire mise a jour={mise_a_jour}")
        return {"incident_id": incident_id, "resultat": corps.resultat, "memoire_mise_a_jour": mise_a_jour}

    return application


app = creer_app(MemoireJSON(), creer_llm(), AbstentionSeuil())