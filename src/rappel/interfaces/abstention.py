from dataclasses import dataclass
from typing import Protocol

from ..contrats import Hypothese


@dataclass
class DecisionAbstention:
    abstient: bool
    confiance: float
    manque: list[str]


class Abstention(Protocol):
    def decider(
        self, hypothese: Hypothese, nb_sources: int, incident_similaire: bool
    ) -> DecisionAbstention: ...


class AbstentionSeuil:
    def __init__(self, seuil: float = 0.6) -> None:
        self.seuil = seuil

    def decider(
        self, hypothese: Hypothese, nb_sources: int, incident_similaire: bool
    ) -> DecisionAbstention:
        # Formule provisoire, a calibrer sur les fixtures d'incident.
        confiance = hypothese.confiance
        if nb_sources >= 2:
            confiance += 0.1
        if incident_similaire:
            confiance += 0.1
        confiance = min(confiance, 1.0)

        manque = []
        if not hypothese.preuves:
            manque.append("preuve")
        if nb_sources < 2:
            manque.append("deuxième source indépendante")
        if not incident_similaire:
            manque.append("incident similaire en mémoire")
        if confiance < self.seuil:
            manque.append("confiance sous le seuil")

        return DecisionAbstention(
            abstient=confiance < self.seuil or not hypothese.preuves,
            confiance=confiance,
            manque=manque,
        )