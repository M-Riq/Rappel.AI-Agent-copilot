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


class AbstentionSeuil:
    """Politique du moteur d'enquête ; même seuil que decider() (SEUIL_ABSTENTION)."""

    def __init__(self, seuil=None):
        self.seuil = charger().seuil_abstention if seuil is None else seuil

    def decider(self, hypothese, nb_sources, incident_similaire):
        from rappel.enquete.protocoles import DecisionAbstention

        # Bonus de corroboration ; la comparaison au seuil reste celle de decider().
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
