"""Validation commune des formats partagés."""

import json
from functools import lru_cache

from jsonschema import Draft202012Validator, FormatChecker

from rappel.config import RACINE


@lru_cache
def validateur(nom):
    schema = json.loads(
        (RACINE / "contrats" / f"{nom}.schema.json").read_text(encoding="utf-8")
    )
    return Draft202012Validator(schema, format_checker=FormatChecker())


def valider(nom, donnees):
    validateur(nom).validate(donnees)
