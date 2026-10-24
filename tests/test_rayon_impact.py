import pytest

from rappel.action.rayon_impact import AUTO, REFUS, VALIDATION, evaluer_rayon_impact, expliquer_rayon_impact
from rappel.config import Configuration, charger
from rappel.enquete.modeles import CorrectifPropose


def correctif(**changements):
    donnees = {"description": "d", "patch": "{}", "niveau_risque": "validation", "elements_touches": ["etl_demo"],
               "reversible": True, "justification": "j", "confiance": 0.95}
    donnees.update(changements)
    return CorrectifPropose(**donnees)


def test_auto():
    assert evaluer_rayon_impact(correctif()) == AUTO
    assert evaluer_rayon_impact(correctif().model_dump()) == AUTO


@pytest.mark.parametrize("changements", [
    {"confiance": 0.75}, {"elements_touches": ["a", "b"]}, {"elements_touches": ["service_paiement"]},
])
def test_validation(changements):
    niveau, raisons = expliquer_rayon_impact(correctif(**changements))
    assert niveau == VALIDATION and raisons


@pytest.mark.parametrize("changements", [
    {"reversible": False}, {"elements_touches": ["a", "b", "c", "d"]}, {"confiance": 0.3},
])
def test_refus(changements):
    assert evaluer_rayon_impact(correctif(**changements)) == REFUS


def test_limites_exactes():
    configuration = Configuration()
    assert evaluer_rayon_impact(correctif(confiance=configuration.rayon_seuil_auto)) == AUTO
    assert evaluer_rayon_impact(correctif(confiance=configuration.rayon_seuil_auto - 1e-9)) == VALIDATION
    assert evaluer_rayon_impact(correctif(confiance=configuration.rayon_seuil_validation)) == VALIDATION
    assert evaluer_rayon_impact(correctif(confiance=configuration.rayon_seuil_validation - 1e-9)) == REFUS
    trois = correctif(elements_touches=["a", "b", "c"])
    assert evaluer_rayon_impact(trois) == VALIDATION


def test_seuils_en_configuration(monkeypatch):
    monkeypatch.setenv("RAYON_SEUIL_AUTO", "0.99")
    monkeypatch.setenv("RAYON_MAX_ELEMENTS", "1")
    monkeypatch.setenv("RAYON_ELEMENTS_CRITIQUES", "etl")
    assert charger().rayon_seuil_auto == 0.99
    assert evaluer_rayon_impact(correctif()) == VALIDATION
    assert evaluer_rayon_impact(correctif(elements_touches=["a", "b"])) == REFUS
    with pytest.raises(ValueError):
        Configuration(rayon_seuil_auto=0.5, rayon_seuil_validation=0.7)
