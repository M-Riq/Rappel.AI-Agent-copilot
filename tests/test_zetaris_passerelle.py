"""Format de réponse de la passerelle UI du Gateway Zetaris (cluster AWS)."""

from rappel.interfaces.tools_zetaris import extraire_lignes


def test_reponse_passerelle_headers_data():
    # Format observé le 26 oct. 2026 sur le cluster AWS : SELECT 1 AS one.
    donnees = {"headers": ["one"], "data": [["1"]], "total": 1, "timeUsed": 104}
    assert extraire_lignes(donnees) == [{"one": "1"}]


def test_reponse_passerelle_plusieurs_colonnes():
    donnees = {"headers": ["column_name", "data_type"], "data": [["id", "string"], ["total", "double"]]}
    assert extraire_lignes(donnees) == [
        {"column_name": "id", "data_type": "string"},
        {"column_name": "total", "data_type": "double"},
    ]


def test_reponse_passerelle_vide():
    assert extraire_lignes({"headers": ["one"], "data": []}) == []