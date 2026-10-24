import json
import sys
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import requests
from dotenv import dotenv_values
from jsonschema import ValidationError

from rappel.config import RACINE, Configuration, charger
from rappel.interfaces import abstention, llm, memoire, source
from rappel.interfaces.tools_zetaris import RUN_SQL_TOOL, run_sql


def reponse(donnees, statut=200):
    resultat = MagicMock(status_code=statut)
    resultat.json.return_value = donnees
    return resultat


def test_configuration_par_defaut():
    configuration = Configuration()
    assert (configuration.llm_provider, configuration.source_provider, configuration.memoire_provider) == (
        "fake", "fichier", "json"
    )
    assert configuration.max_tours_enquete == 6
    assert "psycopg" not in sys.modules
    assert "confidentiel" not in repr(
        replace(configuration, meterless_runtime_token="confidentiel", zetaris_api_key="confidentiel", database_url="confidentiel")
    )
    exemple = dotenv_values(RACINE / ".env.example")
    assert exemple["DATABASE_URL"] == configuration.database_url
    assert exemple["METERLESS_RUNTIME_TOKEN"] == ""
    assert exemple["ZETARIS_API_KEY"] == ""


@pytest.mark.parametrize("valeurs", [
    {"llm_provider": "inconnu"}, {"source_provider": "inconnu"},
    {"memoire_provider": "inconnu"}, {"seuil_abstention": float("nan")},
    {"seuil_abstention": 2}, {"max_tours_enquete": 0},
])
def test_configuration_invalide(valeurs):
    with pytest.raises(ValueError):
        Configuration(**valeurs)


def test_fake_llm():
    resultat = llm.completer("Cherche une cause", "detective", [RUN_SQL_TOOL])
    assert resultat == llm.FakeLLM().completer("Cherche une cause", "detective")
    assert resultat["modele"] == "fake-deterministe"
    assert resultat["tool_calls"] == []


@pytest.mark.parametrize("role, modele", [
    ("detective", "voie-rapide"), ("outil", "voie-rapide"),
    ("sceptique", "voie-profonde"), ("conclusion", "voie-profonde"),
    ("correctif", "voie-profonde"), ("historien", "modele-repli"),
])
def test_meterless_entetes_lanes(monkeypatch, role, modele):
    configuration = Configuration(
        llm_provider="meterless", meterless_runtime_token="jeton-test-factice",
        meterless_model="modele-repli",
        meterless_model_rapide="voie-rapide", meterless_model_profond="voie-profonde",
    )
    appels = []
    donnees = {
        "model": "nemotron-effectif",
        "choices": [{"message": {"content": None, "tool_calls": [{"id": "outil-1"}]}}],
        "usage": {"total_tokens": 12},
    }

    def poster(url, **params):
        appels.append((url, params))
        return reponse(donnees)

    monkeypatch.setattr(llm.requests, "post", poster)
    resultat = llm.MeterlessLLM(configuration).completer("enquêter", role, [RUN_SQL_TOOL])
    url, params = appels[0]
    assert url == "http://127.0.0.1:8088/v1/chat/completions"
    assert params["headers"] == {
        "Authorization": "Bearer " + "jeton-test-factice",
        "meterless-app": "rappel", "meterless-scope": "user",
    }
    assert params["json"]["model"] == modele
    assert params["json"]["tools"] == [RUN_SQL_TOOL]
    # Délais séparés connexion / lecture (180 s configurable, METERLESS_DELAI_S).
    assert params["timeout"] == (10.0, 180.0)
    assert resultat == {
        "texte": "", "tool_calls": [{"id": "outil-1"}],
        "modele": "nemotron-effectif", "usage": {"total_tokens": 12},
    }


def test_meterless_repli_et_401(monkeypatch):
    configuration = Configuration(meterless_runtime_token="jeton-test-factice")
    poster = MagicMock(return_value=reponse({"choices": [{"message": {"content": "Bonjour"}}]}))
    monkeypatch.setattr(llm.requests, "post", poster)
    resultat = llm.MeterlessLLM(configuration).completer("Bonjour", "detective")
    assert resultat["modele"] == "meterless/auto"
    assert "tools" not in poster.call_args.kwargs["json"]
    poster.return_value = reponse({}, 401)
    with pytest.raises(RuntimeError, match="jeton Runtime Nightshift incorrect"):
        llm.MeterlessLLM(configuration).completer("Bonjour", "correctif")


def test_llm_fournisseur_reel_et_erreurs(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "meterless")
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "jeton-test-factice")
    poster = MagicMock(side_effect=requests.ConnectionError("identifiant-confidentiel"))
    monkeypatch.setattr(llm.requests, "post", poster)
    with pytest.raises(RuntimeError, match="indisponible") as erreur:
        llm.completer("Bonjour", "detective")
    assert "identifiant-confidentiel" not in str(erreur.value)
    poster.side_effect = None
    poster.return_value = reponse({"choices": []})
    with pytest.raises(RuntimeError, match="invalide"):
        llm.completer("Bonjour", "detective")
    with pytest.raises(ValueError, match="TOKEN"):
        llm.MeterlessLLM(Configuration()).entetes()
    with pytest.raises(ValueError, match="TOKEN"):
        llm.MeterlessLLM(Configuration()).completer("Bonjour", "detective")


def test_zetaris_rest(monkeypatch):
    configuration = Configuration(source_provider="zetaris", zetaris_api_key="cle-test-factice")
    appels = []

    def poster(url, **params):
        appels.append((url, params))
        return reponse({"rows": [{"one": 1}]})

    monkeypatch.setattr(requests, "post", poster)
    for _ in range(2):
        assert run_sql("SELECT 1 AS one", configuration=configuration) == {
            "requete": "SELECT 1 AS one", "lignes": [{"one": 1}]
        }
    for url, params in appels:
        assert url == "http://localhost:8888/api/v1.0/sql-editor/sqls/run"
        assert params["headers"]["Authorization"] == "Bearer " + "cle-test-factice"
        assert params["headers"]["X-Org-ID"] == "1"
        UUID(params["headers"]["X-Request-ID"])
        assert params["json"] == {"sql": "SELECT 1 AS one"}
        # Délais séparés connexion / lecture (ZETARIS_DELAI_S).
        assert params["timeout"] == (10.0, 60.0)
    assert appels[0][1]["headers"]["X-Request-ID"] != appels[1][1]["headers"]["X-Request-ID"]
    assert RUN_SQL_TOOL["function"]["name"] == "run_sql"


@pytest.mark.parametrize("sql", [
    "", "  ", "-- commentaire", "SELECT 1; SELECT 2", "SELECT 1;;",
    "SELECT 'non terminé", "/* non terminé", "SELECT 1; /* oui */ DELETE FROM logs",
    "SELECT $$a;b$$", "SELECT 1 /* /* imbriqué */",
    "SELECT 1; -- commentaire\rSELECT 2",
])
def test_sql_invalide_sans_reseau(sql):
    with pytest.raises(ValueError):
        run_sql(sql, configuration=Configuration(source_provider="zetaris", zetaris_api_key="factice"))


def test_sql_fichier_et_source():
    assert run_sql("SELECT 1 AS one; -- fin")["lignes"] == [{"one": 1}]
    assert run_sql("SELECT ';' AS point")["lignes"] == [{"point": ";"}]
    assert run_sql("SELECT * FROM pipeline.logs")["lignes"][0]["compte"] == 214
    assert run_sql(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'orders' ORDER BY ordinal_position"
    )["lignes"][-1] == {"column_name": "total", "data_type": "decimal"}
    preuve = run_sql(
        "SELECT c.hash, c.message, l.template, l.compte FROM pipeline.commits c "
        "JOIN pipeline.logs l ON l.horodatage > c.date WHERE c.message LIKE '%amount%'"
    )
    assert preuve["lignes"][0]["hash"] == "a1b2c3"
    assert source.lire("p1")["logs"][0]["compte"] == 214
    assert run_sql("SELECT * FROM pipeline.commits", scenario="p3")["lignes"] == []
    with pytest.raises(ValueError):
        source.lire("../../.env")
    with pytest.raises(ValueError):
        run_sql("ATTACH DATABASE '/tmp/interdit' AS danger")
    with pytest.raises(ValueError):
        run_sql("DELETE FROM pipeline.logs")


def test_source_zetaris_et_erreurs(monkeypatch):
    monkeypatch.setenv("SOURCE_PROVIDER", "zetaris")
    monkeypatch.setenv("ZETARIS_API_KEY", "factice")
    poster = MagicMock(return_value=reponse([{"one": 1}]))
    monkeypatch.setattr(requests, "post", poster)
    assert source.lire("sql", {"sql": "SELECT 1 AS one"})["lignes"] == [{"one": 1}]
    poster.return_value = reponse({"inattendu": []})
    with pytest.raises(RuntimeError, match="Format"):
        run_sql("SELECT 1")
    poster.side_effect = requests.ConnectionError("cle-confidentielle")
    with pytest.raises(RuntimeError) as erreur:
        run_sql("SELECT 1")
    assert "cle-confidentielle" not in str(erreur.value)


def enregistrement():
    return {
        "signature": "schema_mismatch:orders_etl",
        "cause_confirmee": "Colonne renommée",
        "hypotheses_refutees": ["Disque plein"],
        "correctif_essaye": "Utiliser total", "resultat": "reussi", "date": "2026-10-22",
    }


def test_memoire_json(tmp_path, monkeypatch):
    chemin = tmp_path / "data" / "memoire.json"
    monkeypatch.setenv("MEMOIRE_JSON_PATH", str(chemin))
    assert memoire.chercher("inconnue") == []
    memoire.ecrire(enregistrement())
    memoire.ecrire({**enregistrement(), "signature": "autre"})
    resultat = memoire.chercher(enregistrement()["signature"])
    assert resultat == [enregistrement()]
    assert resultat[0]["hypotheses_refutees"] == ["Disque plein"]
    assert len(json.loads(chemin.read_text())) == 2
    assert list(chemin.parent.iterdir()) == [chemin]
    with pytest.raises(ValidationError):
        memoire.ecrire({"signature": "incomplet"})
    assert len(json.loads(chemin.read_text())) == 2


def test_memoire_corrompue_non_ecrasee(tmp_path):
    chemin = tmp_path / "memoire.json"
    chemin.write_text("invalide")
    with pytest.raises(ValueError):
        memoire.MemoireJSON(chemin).ecrire(enregistrement())
    assert chemin.read_text() == "invalide"


def test_memoire_postgres_paresseuse_et_parametree(monkeypatch):
    connexion = MagicMock()
    curseur = connexion.__enter__.return_value.cursor.return_value.__enter__.return_value
    curseur.fetchall.return_value = [
        ("schema_mismatch:orders_etl", "Colonne renommée", ["Disque plein"], "Utiliser total", "reussi", date(2026, 10, 22))
    ]
    connecter = MagicMock(return_value=connexion)
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connecter, Error=RuntimeError))
    monkeypatch.setenv("MEMOIRE_PROVIDER", "postgres")
    memoire.ecrire(enregistrement())
    sql, params = curseur.execute.call_args.args
    assert "VALUES (%s, %s, %s::jsonb" in sql
    assert json.loads(params[2]) == ["Disque plein"]
    assert memoire.chercher(enregistrement()["signature"]) == [enregistrement()]
    assert curseur.execute.call_args.args[1] == (enregistrement()["signature"],)


@pytest.mark.parametrize("confiance, attendu", [(0.2, True), (0.6, False), (0.9, False)])
def test_abstention(confiance, attendu):
    assert abstention.decider({"confiance": confiance}, {"incident_id": "p3"})["abstention"] is attendu


def test_abstention_configurable(monkeypatch):
    monkeypatch.setenv("SEUIL_ABSTENTION", "0.9")
    assert abstention.decider({"confiance": 0.8}, {"incident_id": "p3"})["abstention"]
    with pytest.raises(ValueError):
        abstention.decider({"confiance": float("nan")}, {"incident_id": "p3"})
    assert charger().seuil_abstention == 0.9
