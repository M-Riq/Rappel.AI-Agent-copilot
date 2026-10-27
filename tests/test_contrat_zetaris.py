"""Contrat de schéma lu dans Zetaris : traduction des types, ordre, erreurs, intégration P1."""

from types import SimpleNamespace

import pytest

from rappel.ingestion import contrat_zetaris
from rappel.ingestion.contexte_demo import construire_contexte
from rappel.pipeline_demo.pipeline import injecter, schema_attendu

ZETARIS = SimpleNamespace(source_provider="zetaris")
CONTRAT = [  # tel que renvoyé par Zetaris, volontairement dans le désordre
    {"column_name": "status", "expected_type": "string"},
    {"column_name": "amount", "expected_type": "decimal(12,2)"},
    {"column_name": "transaction_id", "expected_type": "string"},
    {"column_name": "buyer_id", "expected_type": "string"},
    {"column_name": "recipient_id", "expected_type": "string"},
    {"column_name": "transaction_date", "expected_type": "timestamp"},
]


@pytest.fixture
def zetaris_simule(monkeypatch):
    appels = []

    def faux_run_sql(sql, configuration=None):
        appels.append(sql)
        return {"requete": sql, "lignes": CONTRAT}

    monkeypatch.setattr(contrat_zetaris.tools_zetaris, "run_sql", faux_run_sql)
    return appels


@pytest.mark.parametrize(("source", "cible"), [
    ("string", "text"), ("decimal(12,2)", "decimal"), (" Timestamp ", "text"), ("bigint", "integer"),
])
def test_normaliser_type(source, cible):
    assert contrat_zetaris.normaliser_type(source) == cible


def test_type_inconnu_refuse():
    with pytest.raises(ValueError, match="inconnu"):
        contrat_zetaris.normaliser_type("binary")


def test_mode_fichier_refuse():
    with pytest.raises(ValueError, match="SOURCE_PROVIDER=zetaris"):
        contrat_zetaris.schema_attendu_zetaris(SimpleNamespace(source_provider="fichier"))


def test_requete_passe_le_garde_fou_et_ordre_respecte(zetaris_simule):
    ordre = ["transaction_id", "buyer_id", "amount", "recipient_id", "transaction_date", "status"]
    schema = contrat_zetaris.schema_attendu_zetaris(ZETARIS, ordre=ordre)
    assert [colonne.split(":")[0] for colonne in schema] == ordre
    assert "amount:decimal" in schema
    assert zetaris_simule[0].endswith("LIMIT 50") and "rappel.schema_contract" in zetaris_simule[0]


def test_contrat_vide_refuse(monkeypatch):
    monkeypatch.setattr(contrat_zetaris.tools_zetaris, "run_sql", lambda sql, configuration=None: {"lignes": []})
    with pytest.raises(ValueError, match="Contrat vide"):
        contrat_zetaris.schema_attendu_zetaris(ZETARIS)


def test_p1_detecte_avec_contrat_zetaris(tmp_path, zetaris_simule):
    injecter("p1", tmp_path)
    contexte, infos = construire_contexte(tmp_path, configuration=ZETARIS)
    assert infos["source_schema_attendu"] == "zetaris:rappel.schema_contract"
    assert contexte["schema"]["attendu"] == schema_attendu(tmp_path)  # contrat Zetaris = config locale
    assert contexte["signature"] == "schema_mismatch:transactions_etl"
    assert any(ecart["colonne"] == "amount" for ecart in infos["ecarts_schema"])


def test_mode_fichier_inchange(tmp_path, zetaris_simule):
    injecter("p1", tmp_path)
    _, infos = construire_contexte(tmp_path, configuration=SimpleNamespace(source_provider="fichier"))
    assert infos["source_schema_attendu"] == "pipeline_demo" and zetaris_simule == []