"""Données synthétiques : reproductibles, cohérentes et sans colonne « total »."""

from decimal import Decimal

import pytest

from scripts.generate_demo_data import COLONNES, CONTRAT, STATUTS, generer


def test_reproductible():
    assert generer(500) == generer(500)


def test_volume_et_identifiants_uniques():
    lignes = generer(500)
    assert len(lignes) == 500
    assert len({ligne["transaction_id"] for ligne in lignes}) == 500


def test_valeurs_coherentes():
    for ligne in generer(500):
        assert tuple(ligne) == COLONNES
        montant = Decimal(ligne["amount"])
        assert Decimal("1.00") <= montant <= Decimal("5000.00")
        assert montant == montant.quantize(Decimal("0.01"))
        assert ligne["status"] in STATUTS
        assert ligne["buyer_id"].startswith("B") and ligne["recipient_id"].startswith("R")


def test_dates_triees():
    dates = [ligne["transaction_date"] for ligne in generer(500)]
    assert dates == sorted(dates)


def test_contrat_attend_amount_et_pas_total():
    colonnes = [colonne for _, colonne, _, _ in CONTRAT]
    assert colonnes == list(COLONNES)
    assert "amount" in colonnes and "total" not in colonnes


@pytest.mark.parametrize("nombre", [99, 1001])
def test_volume_hors_bornes_refuse(nombre):
    with pytest.raises(ValueError):
        generer(nombre)