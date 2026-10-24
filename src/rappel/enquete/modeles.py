from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator


class Contrat(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntreeLog(Contrat):
    template: str
    compte: int = Field(ge=0)


class CommitRecent(Contrat):
    hash: str
    message: str
    date: str


class SchemaIncident(Contrat):
    attendu: list[str]
    observe: list[str]


class ContexteIncident(Contrat):
    incident_id: str
    horodatage: str = Field(json_schema_extra={"format": "date-time"})
    signature: str
    logs: list[EntreeLog]
    commits_recents: list[CommitRecent]
    schema: SchemaIncident
    metriques: dict[str, float]

    @field_validator("horodatage")
    @classmethod
    def valider_horodatage(cls, valeur: str) -> str:
        try:
            if "T" not in valeur and "t" not in valeur:
                raise ValueError
            datetime.fromisoformat(valeur)
        except ValueError as erreur:
            raise ValueError("horodatage doit etre une date-heure ISO 8601") from erreur
        return valeur


class Hypothese(Contrat):
    id: str
    incident_id: str
    texte: str
    statut: Literal["ouverte", "retenue", "refutee"]
    preuves: list[str]
    confiance: float = Field(ge=0, le=1)
    raison_rejet: str | None

    @model_validator(mode="after")
    def valider_statut(self) -> Self:
        if self.statut == "retenue" and not self.preuves:
            raise ValueError("Une hypothese retenue doit contenir au moins une preuve")
        if self.statut == "refutee" and (
            self.raison_rejet is None or not self.raison_rejet.strip()
        ):
            raise ValueError("Une hypothese refutee doit avoir une raison_rejet non vide")
        return self

    @model_serializer(mode="wrap")
    def serialiser(self, gestionnaire, info):
        donnees = gestionnaire(self)
        # Contrat hypothese : raison_rejet est requis et nullable, meme avec exclude_none.
        if info.exclude_none and isinstance(donnees, dict) and "raison_rejet" not in donnees:
            donnees["raison_rejet"] = None
        return donnees


class EnregistrementMemoire(Contrat):
    signature: str
    cause_confirmee: str
    hypotheses_refutees: list[str]
    correctif_essaye: str
    resultat: Literal["reussi", "echoue"]
    date: str


class CorrectifPropose(Contrat):
    description: str
    patch: str
    niveau_risque: Literal["auto", "validation", "refus"]
    elements_touches: list[str]
    reversible: bool
    justification: str
    confiance: float = Field(ge=0, le=1)


class EvenementEnquete(Contrat):
    incident_id: str
    etape: int = Field(ge=1)
    type: Literal["hypothese", "preuve", "rejet", "debat", "decision", "abstention"]
    acteur: Literal["detective", "sceptique", "historien", "systeme"]
    texte: str
    horodatage: str
    modele: str | None = None
    requete_sql: str | None = None

    @model_validator(mode="after")
    def valider_requete_sql(self) -> Self:
        if self.type == "preuve" and self.requete_sql is not None and not self.requete_sql.strip():
            raise ValueError("Une preuve avec requete_sql doit contenir une requete non vide")
        return self