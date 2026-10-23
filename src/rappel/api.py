"""API Phase 0/1 : contextes et décisions en mémoire, sans exécuter de patch."""

import json
from threading import RLock
from typing import Literal

from fastapi import FastAPI, HTTPException
from jsonschema import ValidationError
from pydantic import BaseModel, ConfigDict

from rappel.config import RACINE, charger
from rappel.contrats import valider

app = FastAPI(title="Rappel — Phase 0/1")
_INCIDENTS = {}
_DECISIONS = {}
_VERROU = RLock()


class DecisionCorrectif(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["appliquer", "refuser"]


@app.get("/healthz")
def sante():
    configuration = charger()
    return {
        "statut": "ok",
        "llm": configuration.llm_provider,
        "source": configuration.source_provider,
        "memoire": configuration.memoire_provider,
    }


@app.post("/incidents", status_code=201)
def creer_incident(contexte: dict):
    try:
        valider("contexte_incident", contexte)
    except ValidationError:
        raise HTTPException(422, "ContexteIncident non conforme au contrat.") from None
    identifiant = contexte["incident_id"]
    with _VERROU:
        if identifiant in _INCIDENTS:
            raise HTTPException(409, "Cet incident existe déjà.")
        _INCIDENTS[identifiant] = contexte
    return {"id": identifiant}


@app.get("/incidents/{identifiant}/evenements")
def lire_evenements(identifiant: str):
    # p1 est aussi accessible sans ingestion pour démarrer l'interface locale.
    with _VERROU:
        if identifiant != "p1" and identifiant not in _INCIDENTS:
            raise HTTPException(404, "Incident inconnu.")
    if charger().llm_provider != "fake":
        raise HTTPException(501, "La boucle d'enquête réelle sera implémentée en Phase 2/3.")
    evenements = json.loads(
        (RACINE / "fixtures" / "evenements_p1.json").read_text(encoding="utf-8")
    )
    # La fixture P1 ne doit pas produire de faux diagnostics pour P2 ou P3.
    contexte_p1 = json.loads(
        (RACINE / "fixtures" / "contexte_p1.json").read_text(encoding="utf-8")
    )
    with _VERROU:
        contexte = _INCIDENTS.get(identifiant, contexte_p1)
    if any(
        contexte[champ] != contexte_p1[champ]
        for champ in ("signature", "logs", "commits_recents", "schema", "metriques")
    ):
        return []
    return [{**evenement, "incident_id": identifiant} for evenement in evenements]


@app.post("/correctifs/{identifiant}/decision")
def decider_correctif(identifiant: str, decision: DecisionCorrectif):
    if identifiant != "p1":
        raise HTTPException(404, "Correctif inconnu ; seul le correctif de fixture p1 existe.")
    with _VERROU:
        _DECISIONS[identifiant] = decision.decision
    return {
        "id": identifiant,
        "decision": decision.decision,
        "execute": False,
        "message": "Décision enregistrée en mémoire ; aucun patch exécuté en Phase 0/1.",
    }
