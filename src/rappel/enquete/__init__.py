"""Moteur d'enquête : graphe LangGraph, boucle SQL et adaptateurs vers le squelette."""

_EXPORTS = {"MetriquesEnquete", "ResultatEnquete", "creer_graphe", "ecrire_evenements_jsonl", "enqueter"}


def __getattr__(nom):
    # Import paresseux : charger les modèles ou protocoles n'importe pas LangGraph.
    if nom in _EXPORTS:
        from rappel.enquete import graphe

        return getattr(graphe, nom)
    raise AttributeError(nom)
