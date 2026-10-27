"""Construction d'un ContexteIncident (contrat contexte_incident) depuis le pipeline de démo.

Le contexte est assemblé à partir des fichiers du pipeline : logs réduits en modèles, commits
récents, schémas attendu/observé et métriques. Il est validé par le JSON Schema de l'équipe.
Les données viennent d'un pipeline simulé : `infos["simulation"]` le dit toujours.
En mode SOURCE_PROVIDER=zetaris, le schéma attendu vient de rappel.schema_contract.
"""

from datetime import datetime, timezone

from rappel.config import charger
from rappel.contrats import valider
from rappel.ingestion.contrat_zetaris import SOURCE_LOCALE, SOURCE_ZETARIS, schema_attendu_zetaris
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


def _schema_attendu(dossier, configuration) -> tuple[list[str], str]:
    """(schéma attendu, source) : contrat Zetaris en mode zetaris, configuration locale sinon."""
    local = schema_attendu(dossier)
    if configuration.source_provider != "zetaris":
        return local, SOURCE_LOCALE
    ordre = [colonne.split(":", 1)[0] for colonne in local]
    # Pas de repli silencieux sur le local : une erreur Zetaris remonte telle quelle.
    return schema_attendu_zetaris(configuration, ordre=ordre), SOURCE_ZETARIS


def construire_contexte(dossier=None, incident_id: str | None = None, configuration=None) -> tuple[dict, dict]:
    """Retourne (contexte conforme au contrat, informations de construction)."""
    configuration = configuration or charger()
    fichiers = chemins(dossier)
    etat = lire_etat(dossier)
    lignes = fichiers["logs"].read_text(encoding="utf-8").splitlines()
    modeles, reducteur = reduire_logs(lignes)
    commits = lire_json(fichiers["commits"])[-NB_COMMITS_RECENTS:]
    attendu, source_attendu = _schema_attendu(dossier, configuration)
    observe = schema_observe(dossier)
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
        "source_schema_attendu": source_attendu,
    }
    return contexte, infos