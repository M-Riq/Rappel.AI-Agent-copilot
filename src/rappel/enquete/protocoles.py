"""Protocoles du moteur d'enquête : ce que le graphe attend de ses dépendances."""

from dataclasses import dataclass
import json
from typing import Literal, Protocol

from rappel.enquete.modeles import EnregistrementMemoire, EvenementEnquete, Hypothese


VOIE_RAPIDE = "rapide"
VOIE_PROFONDE = "profonde"
ROLES_VOIE_PROFONDE = frozenset({"conclusion", "correctif"})
# Les prompts structurés se terminent par ce marqueur suivi d'un objet JSON de données.
MARQUEUR_DONNEES = "\nDONNEES:\n"


def voie_pour_role(role: str) -> str:
    return VOIE_PROFONDE if role in ROLES_VOIE_PROFONDE else VOIE_RAPIDE


def lire_donnees(prompt: str) -> dict:
    """Extrait l'objet JSON placé après MARQUEUR_DONNEES dans un prompt structuré."""
    position = prompt.rfind(MARQUEUR_DONNEES)
    if position == -1:
        raise ValueError("Prompt sans bloc DONNEES")
    donnees = json.loads(prompt[position + len(MARQUEUR_DONNEES):])
    if not isinstance(donnees, dict):
        raise ValueError("Le bloc DONNEES doit être un objet JSON")
    return donnees


@dataclass
class UsageLLM:
    tokens_entree: int | None = 0
    tokens_sortie: int | None = 0
    nb_appels: int = 0
    # Compteurs séparés par voie ("rapide" / "profonde").
    par_voie: dict[str, "UsageLLM"] | None = None


def usage_par_voie() -> dict[str, UsageLLM]:
    return {VOIE_RAPIDE: UsageLLM(), VOIE_PROFONDE: UsageLLM()}


class LLM(Protocol):
    usage: UsageLLM

    def completer(self, prompt: str, role: str = "detective") -> str: ...


class Memoire(Protocol):
    def lister(self) -> list[EnregistrementMemoire]: ...

    def vider(self) -> int: ...

    def chercher(self, signature: str) -> list[EnregistrementMemoire]: ...

    def ecrire(self, enregistrement: EnregistrementMemoire) -> None: ...

    def hypotheses_refutees(self, signature: str) -> list[str]: ...

    def mettre_a_jour_resultat(
        self, signature: str, resultat: Literal["reussi", "echoue"]
    ) -> bool: ...

    def ecrire_hypotheses(
        self, incident_id: str, signature: str, hypotheses: list[Hypothese]
    ) -> None: ...


@dataclass
class DecisionAbstention:
    abstient: bool
    confiance: float
    manque: list[str]


class Abstention(Protocol):
    def decider(
        self, hypothese: Hypothese, nb_sources: int, incident_similaire: bool
    ) -> DecisionAbstention: ...


class RunSql(Protocol):
    mode: str

    def executer(self, sql: str): ...


class Traceur(Protocol):
    def demarrer(self, incident_id: str, signature: str) -> None: ...

    def evenement(self, evenement: EvenementEnquete) -> None: ...

    def terminer(self, metriques: dict, decision: str) -> None: ...
