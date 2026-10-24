"""Rayon d'impact d'un correctif : auto, validation ou refus (case B8 du cahier).

- refus : correctif irréversible, trop d'éléments touchés, ou confiance trop faible ;
- auto : réversible, un seul élément, non critique, confiance au moins égale au seuil auto ;
- validation : tout le reste (plusieurs éléments, élément critique, confiance moyenne).
Les seuils viennent de rappel.config.Configuration (RAYON_*).
"""

from rappel.config import Configuration, charger

AUTO, VALIDATION, REFUS = "auto", "validation", "refus"


def _champ(correctif, nom):
    return correctif[nom] if isinstance(correctif, dict) else getattr(correctif, nom)


def expliquer_rayon_impact(correctif, configuration: Configuration | None = None) -> tuple[str, list[str]]:
    configuration = configuration or charger()
    elements = list(_champ(correctif, "elements_touches"))
    confiance = float(_champ(correctif, "confiance"))
    reversible = bool(_champ(correctif, "reversible"))
    critiques = {mot.strip().lower() for mot in configuration.rayon_elements_critiques.split(",") if mot.strip()}
    touches_critiques = [element for element in elements if any(mot in element.lower() for mot in critiques)]
    if not reversible:
        return REFUS, ["correctif irréversible"]
    if len(elements) > configuration.rayon_max_elements:
        return REFUS, [f"{len(elements)} éléments touchés (maximum {configuration.rayon_max_elements})"]
    if confiance < configuration.rayon_seuil_validation:
        return REFUS, [f"confiance {confiance:.2f} sous {configuration.rayon_seuil_validation}"]
    raisons = []
    if len(elements) != 1:
        raisons.append(f"{len(elements)} éléments touchés")
    if touches_critiques:
        raisons.append("élément critique : " + ", ".join(touches_critiques))
    if confiance < configuration.rayon_seuil_auto:
        raisons.append(f"confiance {confiance:.2f} sous le seuil auto {configuration.rayon_seuil_auto}")
    if raisons:
        return VALIDATION, raisons
    return AUTO, ["réversible, un seul élément non critique, confiance suffisante"]


def evaluer_rayon_impact(correctif, configuration: Configuration | None = None) -> str:
    return expliquer_rayon_impact(correctif, configuration)[0]
