"""L'abstention interdit de conclure lorsque la confiance est insuffisante."""

import math

from rappel.config import charger


def decider(hypothese, contexte):
    confiance = hypothese.get("confiance", 0)
    if (
        isinstance(confiance, bool)
        or not isinstance(confiance, (int, float))
        or not math.isfinite(confiance)
        or not 0 <= confiance <= 1
    ):
        raise ValueError("La confiance doit être un nombre compris entre 0 et 1.")
    abstention = confiance < charger().seuil_abstention
    return {
        "abstention": abstention,
        "incident_id": contexte["incident_id"],
        "raison": "Confiance insuffisante : recueillir davantage de preuves." if abstention else "Seuil de confiance atteint.",
    }
