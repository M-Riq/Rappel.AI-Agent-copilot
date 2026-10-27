"""Schéma attendu lu dans Zetaris (rappel.schema_contract) au lieu de la configuration locale."""

from rappel.config import charger
from rappel.interfaces import tools_zetaris
from rappel.interfaces.run_sql import TABLES_DONNEES_ZETARIS, valider_sql

SOURCE_ZETARIS = "zetaris:rappel.schema_contract"
SOURCE_LOCALE = "pipeline_demo"
DATASET = "transactions"
SQL_CONTRAT = (
    "SELECT column_name, expected_type FROM rappel.schema_contract "
    f"WHERE dataset_name = '{DATASET}'"
)
# Types du contrat (Zetaris/Spark) -> types connus du pipeline de démo.
TYPES_PIPELINE = {
    "string": "text", "varchar": "text", "text": "text",
    "timestamp": "text", "date": "text",
    "decimal": "decimal", "double": "decimal", "float": "decimal",
    "int": "integer", "integer": "integer", "bigint": "integer",
}


def normaliser_type(type_contrat: str) -> str:
    """'decimal(12,2)' -> 'decimal' ; type inconnu : erreur explicite, jamais de supposition."""
    base = str(type_contrat).strip().lower().split("(", 1)[0].strip()
    if base not in TYPES_PIPELINE:
        raise ValueError(f"type de contrat inconnu : {type_contrat!r}")
    return TYPES_PIPELINE[base]


def schema_attendu_zetaris(configuration=None, ordre=()) -> list[str]:
    """Colonnes 'nom:type' du contrat Zetaris, dans l'ordre de `ordre` si fourni."""
    configuration = configuration or charger()
    if configuration.source_provider != "zetaris":
        raise ValueError("Le contrat Zetaris exige SOURCE_PROVIDER=zetaris.")
    validation = valider_sql(SQL_CONTRAT, TABLES_DONNEES_ZETARIS)
    if validation.erreur is not None:
        raise ValueError(validation.erreur)
    lignes = tools_zetaris.run_sql(validation.requete, configuration=configuration)["lignes"]
    colonnes: dict[str, str] = {}
    for ligne in lignes:
        nom = str(ligne.get("column_name") or "").strip()
        if nom and nom not in colonnes:
            colonnes[nom] = normaliser_type(ligne.get("expected_type"))
    if not colonnes:
        raise ValueError(f"Contrat vide dans rappel.schema_contract pour {DATASET}.")
    rang = {nom: position for position, nom in enumerate(ordre)}
    noms = sorted(colonnes, key=lambda nom: rang.get(nom, len(rang)))
    return [f"{nom}:{colonnes[nom]}" for nom in noms]