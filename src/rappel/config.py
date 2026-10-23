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
    api_url: str = "http://127.0.0.1:8000"

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


def charger() -> Configuration:
    valeurs = {
        nom: os.environ[nom.upper()]
        for nom in Configuration.__dataclass_fields__
        if nom.upper() in os.environ
    }
    for nom, convertir in (
        ("seuil_abstention", float),
        ("max_tours_enquete", int),
        ("llm_max_appels", int),
    ):
        if nom in valeurs:
            valeurs[nom] = convertir(valeurs[nom])
    return Configuration(**valeurs)
