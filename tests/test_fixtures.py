import json
from pathlib import Path

import pytest

from src.rappel.contrats import ContexteIncident


DOSSIER_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def charger_contexte(nom_fichier: str) -> ContexteIncident:
    chemin = DOSSIER_FIXTURES / nom_fichier
    return ContexteIncident.model_validate_json(chemin.read_text(encoding="utf-8"))


def separer_colonnes(colonnes: list[str]) -> dict[str, str]:
    resultat = {}
    for colonne in colonnes:
        nom, type_colonne = colonne.split(":", 1)
        resultat[nom] = type_colonne
    return resultat


@pytest.mark.parametrize(
    "nom_fichier", ["contexte_p1.json", "contexte_p2.json", "contexte_p3.json"]
)
def test_contexte_fixture_valide(nom_fichier):
    chemin = DOSSIER_FIXTURES / nom_fichier
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    contexte = ContexteIncident.model_validate(donnees)
    assert contexte.model_dump() == donnees


def test_p1_renommage_colonne():
    contexte = charger_contexte("contexte_p1.json")
    attendu = separer_colonnes(contexte.schema.attendu)
    observe = separer_colonnes(contexte.schema.observe)
    assert attendu.keys() != observe.keys()


def test_p2_changement_type_colonne():
    contexte = charger_contexte("contexte_p2.json")
    attendu = separer_colonnes(contexte.schema.attendu)
    observe = separer_colonnes(contexte.schema.observe)
    assert attendu.keys() == observe.keys()
    assert any(attendu[nom] != observe[nom] for nom in attendu)


def test_p3_schema_inchange():
    contexte = charger_contexte("contexte_p3.json")
    assert contexte.schema.attendu == contexte.schema.observe