"""Construction d'un ContexteIncident (contrat contexte_incident) depuis le pipeline de démo.

Le contexte est assemblé à partir des fichiers du pipeline : logs réduits en modèles, commits
récents, schémas attendu/observé et métriques. Il est validé par le JSON Schema de l'équipe.
Les données viennent d'un pipeline simulé : `infos["simulation"]` le dit toujours.
"""

from datetime import datetime, timezone

from rappel.contrats import valider
from rappel.ingestion.ecarts_schema import detecter_ecarts, moteur_detection
from rappel.ingestion.reducteur_logs import reduire_logs
from rappel.pipeline_demo.pipeline import chemins, lire_etat, lire_json, schema_attendu, schema_observe

NB_COMMITS_RECENTS = 5
MOTS_DELAI = ("timeout", "timed out", "exceeded")


def _horodatage() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def signature_incident(composant: str, ecarts: list[dict], modeles: list[dict], statut: str) -> str:
    # Un pipeline vert n'a pas d'incident, meme si son bruit contient des delais resolus.
    if statut != "rouge":
        return f"aucune_anomalie:{composant}"
    if ecarts:
        return f"schema_mismatch:{composant}"
    if any(mot in modele["template"].lower() for modele in modeles for mot in MOTS_DELAI):
        return f"timeout:{composant}"
    return f"anomalie:{composant}"


def construire_contexte(dossier=None, incident_id: str | None = None) -> tuple[dict, dict]:
    """Retourne (contexte conforme au contrat, informations de construction)."""
    fichiers = chemins(dossier)
    etat = lire_etat(dossier)
    lignes = fichiers["logs"].read_text(encoding="utf-8").splitlines()
    modeles, reducteur = reduire_logs(lignes)
    commits = lire_json(fichiers["commits"])[-NB_COMMITS_RECENTS:]
    attendu, observe = schema_attendu(dossier), schema_observe(dossier)
    ecarts = detecter_ecarts(attendu, observe)
    horodatage = _horodatage()
    scenario = etat.get("scenario") or "demo"
    contexte = {
        "incident_id": incident_id or f"demo-{scenario}-{horodatage[:19].replace('-', '').replace(':', '')}",
        "horodatage": horodatage,
        "signature": signature_incident(etat["composant"], ecarts, modeles, etat["statut"]),
        "logs": modeles,
        "commits_recents": [{"hash": commit["hash"], "message": commit["message"], "date": commit["date"]}
                            for commit in commits],
        "schema": {"attendu": attendu, "observe": observe},
        "metriques": {"lignes_rejetees": etat["lignes_rejetees"], "latence_ms": etat["latence_ms"]},
    }
    valider("contexte_incident", contexte)
    detecteur, message_detecteur = moteur_detection()
    infos = {
        "reducteur_logs": reducteur, "nb_lignes_logs": len(lignes), "nb_modeles": len(modeles),
        "detecteur_schema": detecteur, "message_detecteur": message_detecteur, "ecarts_schema": ecarts,
        "statut_pipeline": etat["statut"], "scenario": scenario, "simulation": True,
    }
    return contexte, infos
