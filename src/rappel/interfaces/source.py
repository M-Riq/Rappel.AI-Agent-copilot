"""Lecture contrôlée des contextes locaux ou d'une preuve SQL Zetaris."""

import json

from rappel.config import RACINE, charger
from rappel.interfaces.tools_zetaris import run_sql


def lire(source, params=None):
    params = params or {}
    configuration = charger()
    if configuration.source_provider == "zetaris":
        return run_sql(params["sql"], configuration=configuration)
    if source not in ("p1", "p2", "p3"):
        raise ValueError("Source fichier inconnue : utiliser p1, p2 ou p3.")
    return json.loads(
        (RACINE / "fixtures" / f"contexte_{source}.json").read_text(encoding="utf-8")
    )
