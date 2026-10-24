"""Détecteur d'écart entre schéma attendu et schéma observé (format « nom:type »).

Détecteur Python standard. great_expectations n'est pas branché : s'il est installé, il est
signalé mais le détecteur Python reste celui utilisé (aucune intégration non vérifiée).
"""

import importlib.util

TYPE_INCONNU = "?"


def colonnes(schema: list[str]) -> dict[str, str]:
    resultat = {}
    for colonne in schema:
        nom, _, type_colonne = colonne.partition(":")
        nom = nom.strip()
        if nom and nom not in resultat:
            resultat[nom] = type_colonne.strip() or TYPE_INCONNU
    return resultat


def detecter_ecarts(attendu: list[str], observe: list[str]) -> list[dict]:
    """Colonnes manquantes, nouvelles et de type différent (unicité vérifiée dans chaque liste seulement)."""
    attendues, observees = colonnes(attendu), colonnes(observe)
    ecarts = []
    for nom, type_attendu in attendues.items():
        if nom not in observees:
            ecarts.append({"ecart": "manquante", "colonne": nom, "attendu": type_attendu, "observe": None})
        elif TYPE_INCONNU not in (type_attendu, observees[nom]) and type_attendu != observees[nom]:
            ecarts.append({"ecart": "type", "colonne": nom, "attendu": type_attendu, "observe": observees[nom]})
    for nom, type_observe in observees.items():
        if nom not in attendues:
            ecarts.append({"ecart": "nouvelle", "colonne": nom, "attendu": None, "observe": type_observe})
    return ecarts


def moteur_detection() -> tuple[str, str]:
    if importlib.util.find_spec("great_expectations") is not None:
        return "python", "great_expectations détecté mais non branché : détecteur Python utilisé"
    return "python", "great_expectations absent : détecteur Python utilisé"
