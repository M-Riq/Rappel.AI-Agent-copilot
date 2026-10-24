"""Pipeline de démo isolé (Module B) : génération, pannes P1/P2/P3 et remise à zéro."""

from rappel.pipeline_demo.pipeline import (
    NOMS_SCENARIOS, dossier_demo, empreinte_dossier, executer_pipeline, injecter, lire_etat, reset,
    schema_attendu, schema_observe,
)

__all__ = [
    "NOMS_SCENARIOS", "dossier_demo", "empreinte_dossier", "executer_pipeline", "injecter", "lire_etat",
    "reset", "schema_attendu", "schema_observe",
]
