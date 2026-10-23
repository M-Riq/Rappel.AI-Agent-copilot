from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, StrictBool, field_validator

from .contrats import ContexteIncident, CorrectifPropose, EvenementEnquete
from .enquete import enqueter
from .interfaces.abstention import Abstention, AbstentionSeuil
from .interfaces.llm import LLM, creer_llm
from .interfaces.memoire import Memoire, MemoireJSON
from .interfaces.run_sql import RunSql, creer_run_sql, verifier_backend
from .interfaces.traceur import Traceur, creer_traceur, proteger_traceur


class DecisionHumaine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["appliquer", "refuser"]


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
            raise ValueError("confirmer doit etre true pour effacer la memoire")
        return valeur


def creer_app(
    memoire: Memoire, llm: LLM, abstention: Abstention,
    executeur_enquete: Callable[[Callable[[], None]], object] | None = None,
    executeur_action: Callable[[str, CorrectifPropose], None] | None = None,
    traceur: Traceur | None = None,
    mode_preuves: Literal["sql", "outils"] = "sql",
    fabrique_run_sql: Callable[[ContexteIncident], RunSql] | None = None,
) -> FastAPI:
    if mode_preuves not in {"sql", "outils"}:
        raise ValueError("mode_preuves doit etre sql ou outils")
    if mode_preuves == "sql" and fabrique_run_sql is None:
        # Erreur claire au demarrage si RUN_SQL_BACKEND n'est pas utilisable (ex. zetaris non branche).
        verifier_backend()
        fabrique_run_sql = creer_run_sql
    suivi = proteger_traceur(creer_traceur() if traceur is None else traceur)
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
                resultat = enqueter(contexte, memoire, llm, abstention,
                                    sur_evenement=recevoir, traceur=suivi,
                                    mode_preuves=mode_preuves, fabrique_run_sql=fabrique_run_sql)
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
                "cree_le": datetime.now(timezone.utc).isoformat(), "resultat_action": None,
            }
        try:
            soumettre(lambda: executer(contexte))
        except Exception:
            with verrou:
                incident = obtenir(contexte.incident_id)
                incident["statut"] = "erreur"
                incident["message"] = "Impossible de demarrer l'enquete."
        return {"incident_id": contexte.incident_id, "statut": "en_cours"}

    @application.get("/incidents")
    def lister_incidents(
        limite: int = Query(default=50, ge=1, le=200),
        statut: Literal["en_cours", "termine", "erreur"] | None = None,
    ):
        with verrou:
            resumes = []
            for incident_id, incident in reversed(list(incidents.items())):
                if statut is not None and incident["statut"] != statut:
                    continue
                resultat = incident["resultat"]
                resumes.append({
                    "incident_id": incident_id, "signature": incident["contexte"].signature,
                    "statut": incident["statut"], "cree_le": incident["cree_le"],
                    "nb_evenements": len(incident["evenements"]),
                    "a_correctif": resultat is not None and resultat.correctif is not None,
                    "abstention": resultat.decision_abstention.abstient if incident["statut"] == "termine" else None,
                    "decision_humaine": incident["decision"], "resultat_action": incident["resultat_action"],
                })
                if len(resumes) == limite:
                    break
            return resumes

    @application.get("/memoire")
    def lire_memoire(signature: str | None = None):
        try:
            with verrou_execution:
                souvenirs = memoire.lister() if signature is None else memoire.chercher(signature)
                refutations = {}
                for souvenir in souvenirs:
                    textes = refutations.setdefault(souvenir.signature, [])
                    for texte in souvenir.hypotheses_refutees:
                        if texte not in textes:
                            textes.append(texte)
                return jsonable_encoder({"souvenirs": souvenirs, "refutations_par_signature": refutations})
        except Exception:
            raise HTTPException(500, "Lecture de la memoire impossible") from None

    @application.post("/memoire/reinitialiser")
    def reinitialiser_memoire(corps: ReinitialisationMemoire):
        def verifier_en_cours():
            if any(incident["statut"] == "en_cours" for incident in incidents.values()):
                raise HTTPException(409, "Reinitialisation interdite pendant une enquete")
        with verrou:
            verifier_en_cours()
        with verrou_execution:
            with verrou:
                verifier_en_cours()
                try:
                    nombre = memoire.vider()
                except Exception:
                    raise HTTPException(500, "Reinitialisation de la memoire impossible") from None
                incidents_supprimes = len(incidents) if corps.incidents else 0
                if corps.incidents:
                    incidents.clear()
                return {"souvenirs_supprimes": nombre, "incidents_supprimes": incidents_supprimes}

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
                    mode_preuves=resultat.mode_preuves, source_preuves=resultat.source_preuves,
                    preuves_simulees=(resultat.source_preuves or "").startswith("simule"),
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
                with verrou:
                    incident = obtenir(incident_id)
                    mise_a_jour = memoire.mettre_a_jour_resultat(signature, corps.resultat)
                    incident["resultat_action"] = corps.resultat
                    ajouter_decision(incident, f"Resultat Action : {corps.resultat}; memoire mise a jour={mise_a_jour}")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(500, "Mise a jour de la memoire impossible") from None
        return {"incident_id": incident_id, "resultat": corps.resultat, "memoire_mise_a_jour": mise_a_jour}

    return application


app = creer_app(MemoireJSON(), creer_llm(), AbstentionSeuil())