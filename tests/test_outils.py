import pytest

from src.rappel.contrats import ContexteIncident
from src.rappel.enquete import outils


def contexte(schema=None, logs=None, commits=None):
    return ContexteIncident(
        incident_id="inc-outils", horodatage="2026-10-15T10:42:00Z", signature="test:composant",
        schema=schema or {"attendu": ["prix:float"], "observe": ["prix:str"]},
        logs=logs or [], commits_recents=commits or [], metriques={},
    )


def test_schema_types_et_sources():
    incident = contexte(
        logs=[{"template": "KeyError: column <*> not found", "compte": 8}],
        commits=[{"hash": "abc", "message": "change column type", "date": "2026-10-15"}],
    )
    preuves = outils.tester_schema(incident)
    assert {preuve.partition(":")[0] for preuve in preuves} == {"schema", "commits", "logs"}
    assert "prix" in preuves[0] and "float" in preuves[0] and "str" in preuves[0]


def test_schema_colonnes_manquantes_et_nouvelles():
    preuves = outils.ecarts_schema(contexte({"attendu": ["ancien:int"], "observe": ["nouveau:int"]}))
    assert len(preuves) == 2
    assert "ancien:int" in preuves[0]
    assert "nouveau:int" in preuves[1]


@pytest.mark.parametrize(
    ("outil", "template", "seuil"),
    [(outils.tester_ressources, "WARN mémoire saturée", outils.SEUIL_LOG_RESSOURCES),
     (outils.tester_reseau, "WARN connexion timeout", outils.SEUIL_LOG_RESEAU)],
)
def test_seuil_logs_strict(outil, template, seuil):
    assert outil(contexte(logs=[{"template": template, "compte": seuil}])) == []
    assert len(outil(contexte(logs=[{"template": template, "compte": seuil + 1}]))) == 1


def test_configuration():
    preuves = outils.tester_configuration(contexte(commits=[
        {"hash": "def", "message": "modifier paramètre de configuration", "date": "2026-10-15"}
    ]))
    assert len(preuves) == 1 and "def" in preuves[0]


def test_outils_sans_indices():
    incident = contexte({"attendu": ["prix:float"], "observe": ["prix:float"]})
    for outil in (outils.tester_schema, outils.tester_ressources, outils.tester_reseau, outils.tester_configuration):
        assert outil(incident) == []