"""Configuration explicite ; aucun service externe dans les modes par défaut."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RACINE = Path(__file__).resolve().parents[2]
load_dotenv(RACINE / ".env")


@dataclass(frozen=True)
class Configuration:
    llm_provider: str = "fake"
    meterless_base_url: str = "http://127.0.0.1:8088/v1"
    meterless_runtime_token: str = field(default="", repr=False)
    meterless_model: str = "meterless/auto"
    meterless_model_rapide: str = ""
    meterless_model_profond: str = ""
    source_provider: str = "fichier"
    zetaris_base_url: str = "http://localhost:8888/api/v1.0"
    zetaris_api_key: str = field(default="", repr=False)
    zetaris_org_id: str = "1"
    memoire_provider: str = "json"
    memoire_json_path: str = "data/memoire.json"
    database_url: str = field(
        default="postgresql://" + "rappel:" + "rappel@localhost:5432/rappel", repr=False
    )
    seuil_abstention: float = 0.6
    max_tours_enquete: int = 6
    llm_max_appels: int = 20
    # Appels d'outils natifs (tool_calls run_sql) ; 0 = JSON dans le texte (defaut).
    appel_outils: bool = False
    # Pipeline de demo isole (Module B) : seul dossier que l'action peut modifier.
    pipeline_demo_dir: str = "data/pipeline_demo"
    # Execution reelle d'un correctif accepte sur le pipeline de demo (jamais ailleurs) ; 0 par defaut.
    action_reelle_demo: bool = False
    # Rayon d'impact (B8) : auto / validation / refus.
    rayon_seuil_auto: float = 0.9
    rayon_seuil_validation: float = 0.6
    rayon_max_elements: int = 3
    rayon_elements_critiques: str = "production,paiement,facturation,securite"
    api_url: str = "http://127.0.0.1:8000"
    # Réseau des services réels : délais séparés connexion / lecture, relances, coupe-circuit.
    meterless_delai_s: float = 180.0
    zetaris_delai_s: float = 60.0
    reseau_delai_connexion_s: float = 10.0
    reseau_relances: int = 2
    reseau_attente_base_s: float = 1.0
    reseau_seuil_coupe_circuit: int = 3
    # Endpoint et clé du SQL dans le corps de l'API REST Zetaris (à confirmer sur l'instance).
    zetaris_chemin_sql: str = "/sql-editor/sqls/run"
    zetaris_cle_sql: str = "sql"
    # Journal de la boucle des 4 agents (JSON Lines, sans secret ; ce n'est pas un evenement du contrat).
    boucle_journal_path: str = "data/boucle_journal.jsonl"

    def __post_init__(self):
        for valeur, choix in (
            (self.llm_provider, ("fake", "meterless")),
            (self.source_provider, ("fichier", "zetaris")),
            (self.memoire_provider, ("json", "postgres")),
        ):
            if valeur not in choix:
                raise ValueError(f"Mode inconnu ; valeurs autorisées : {', '.join(choix)}")
        if not 0 <= self.seuil_abstention <= 1:
            raise ValueError("SEUIL_ABSTENTION doit être compris entre 0 et 1.")
        if self.max_tours_enquete < 1:
            raise ValueError("MAX_TOURS_ENQUETE doit être positif.")
        if self.llm_max_appels < 1:
            raise ValueError("LLM_MAX_APPELS doit être positif.")
        if not 0 <= self.rayon_seuil_validation <= self.rayon_seuil_auto <= 1:
            raise ValueError("RAYON_SEUIL_VALIDATION et RAYON_SEUIL_AUTO doivent vérifier 0 <= validation <= auto <= 1.")
        if self.rayon_max_elements < 1:
            raise ValueError("RAYON_MAX_ELEMENTS doit être positif.")
        if min(self.meterless_delai_s, self.zetaris_delai_s, self.reseau_delai_connexion_s) <= 0:
            raise ValueError("Les délais réseau doivent être strictement positifs.")
        if self.reseau_relances < 0 or self.reseau_attente_base_s < 0 or self.reseau_seuil_coupe_circuit < 1:
            raise ValueError("RESEAU_RELANCES, RESEAU_ATTENTE_BASE_S >= 0 et RESEAU_SEUIL_COUPE_CIRCUIT >= 1 requis.")
        if not self.zetaris_chemin_sql.startswith("/") or not self.zetaris_cle_sql.strip():
            raise ValueError("ZETARIS_CHEMIN_SQL doit commencer par / et ZETARIS_CLE_SQL être renseignée.")


def _booleen(nom, valeur):
    valeur = valeur.strip().lower()
    if valeur in ("", "0", "false", "non", "no"):
        return False
    if valeur in ("1", "true", "oui", "yes"):
        return True
    raise ValueError(f"{nom} doit valoir 0 ou 1.")


def charger() -> Configuration:
    valeurs = {
        nom: os.environ[nom.upper()]
        for nom in Configuration.__dataclass_fields__
        if nom.upper() in os.environ
    }
    if "LLM_APPEL_OUTILS" in os.environ:
        valeurs["appel_outils"] = _booleen("LLM_APPEL_OUTILS", os.environ["LLM_APPEL_OUTILS"])
    if "ACTION_REELLE_DEMO" in os.environ:
        valeurs["action_reelle_demo"] = _booleen("ACTION_REELLE_DEMO", os.environ["ACTION_REELLE_DEMO"])
    for nom, convertir in (
        ("seuil_abstention", float),
        ("max_tours_enquete", int),
        ("llm_max_appels", int),
        ("rayon_seuil_auto", float),
        ("rayon_seuil_validation", float),
        ("rayon_max_elements", int),
        ("meterless_delai_s", float),
        ("zetaris_delai_s", float),
        ("reseau_delai_connexion_s", float),
        ("reseau_relances", int),
        ("reseau_attente_base_s", float),
        ("reseau_seuil_coupe_circuit", int),
    ):
        if nom in valeurs:
            valeurs[nom] = convertir(valeurs[nom])
    return Configuration(**valeurs)
