"""Scripts de détection et de diagnostic contre les faux services locaux (NON une preuve sponsor)."""

import json
import os

import pytest

from scripts import decouvrir_zetaris, detecter_nightshift, preparer_env
from tests.faux_services import FauxNightshift, FauxZetaris, environnement_faux_services, reseau_local


@pytest.fixture
def local(monkeypatch):
    with reseau_local(monkeypatch):
        yield


def test_detection_en_ligne_non_autorise_hors_ligne(local, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN", raising=False)
    with FauxNightshift() as serveur:
        fichier = tmp_path / "proxy-token"
        fichier.write_text(serveur.jeton + "\n", encoding="utf-8")
        destination = tmp_path / "detection.json"
        ports = f"{serveur.port},9"
        assert detecter_nightshift.main(["--fichier-jeton", str(fichier), "--hotes", "127.0.0.1",
                                         "--ports", ports, "--json", str(destination), "--delai", "1"]) == 0
        sortie = capsys.readouterr().out
        rapport = json.loads(destination.read_text(encoding="utf-8"))
        etats = {sonde["port"]: sonde["etat"] for sonde in rapport["sondes"]}
        assert etats == {serveur.port: "en ligne", 9: "hors ligne"}
        assert all(requete["chemin"] == "/v1/models" for requete in serveur.requetes)
        assert rapport["proposition"]["METERLESS_BASE_URL"] == f"http://127.0.0.1:{serveur.port}/v1"
        assert rapport["proposition"]["METERLESS_MODEL_RAPIDE"] == "meterless/fast"
        assert rapport["proposition"]["METERLESS_MODEL_PROFOND"] == "meterless/deep"
        assert "aucun identifiant ne garantit nemotron" in sortie.lower()
        assert serveur.jeton not in sortie and serveur.jeton not in destination.read_text(encoding="utf-8")
        # Sans jeton puis avec le jeton de démonstration refusé : « non autorisé », distinct de « hors ligne ».
        assert detecter_nightshift.main(["--hotes", "127.0.0.1", "--ports", str(serveur.port),
                                         "--json", str(destination)]) == 1
        assert "jeton requis" in capsys.readouterr().out
        fichier.write_text("rt-local", encoding="utf-8")
        assert detecter_nightshift.main(["--fichier-jeton", str(fichier), "--hotes", "127.0.0.1",
                                         "--ports", str(serveur.port), "--json", str(destination)]) == 1
        assert "jeton refusé" in capsys.readouterr().out


def test_proposition_prefere_nemotron():
    sondes = [{"base": "http://h:1/v1", "etat": "en ligne", "ids": [
        "meterless/fast", "meterless/deep", "nvidia/nemotron-nano-rapide", "nvidia/nemotron-super-deep"]}]
    proposition = detecter_nightshift.proposer(sondes)
    assert proposition["METERLESS_MODEL_RAPIDE"] == "nvidia/nemotron-nano-rapide"
    assert proposition["METERLESS_MODEL_PROFOND"] == "nvidia/nemotron-super-deep"
    assert proposition["nemotron_garanti"] is True
    assert detecter_nightshift.proposer([]) == {"trouve": False}


@pytest.mark.parametrize("publier", [True, False])
def test_decouverte_zetaris(local, monkeypatch, tmp_path, capsys, publier):
    with FauxZetaris(chemin="/requetes/sql", cle_sql="statement", publier_openapi=publier) as serveur:
        environnement_faux_services(monkeypatch, zetaris=serveur)
        if publier:
            # Configuration fausse dans .env : l'OpenAPI publié doit la corriger.
            monkeypatch.setenv("ZETARIS_CHEMIN_SQL", "/sql-editor/sqls/run")
            monkeypatch.setenv("ZETARIS_CLE_SQL", "sql")
        destination = tmp_path / "decouverte.json"
        assert decouvrir_zetaris.main(["--json", str(destination)]) == 0
        rapport = json.loads(destination.read_text(encoding="utf-8"))
        sortie = capsys.readouterr().out
        assert rapport["select_1"]["statut"] == "REUSSI" and rapport["tables"]["manquantes"] == []
        dialecte = {sonde["construction"]: sonde["statut"] for sonde in rapport["dialecte"]}
        assert dialecte["LIMIT"] == "REUSSI" and dialecte["FETCH FIRST"] == "ECHOUE"  # SQLite du faux service
        if publier:
            assert rapport["proposition"] == {"ZETARIS_CHEMIN_SQL": "/requetes/sql", "ZETARIS_CLE_SQL": "statement"}
        else:
            assert "OpenAPI non publié" in sortie
        assert serveur.cle not in sortie and serveur.cle not in destination.read_text(encoding="utf-8")
        assert all(requete["methode"] == "GET" or requete["corps"].get("statement", "").upper().startswith("SELECT")
                   for requete in serveur.requetes)


def test_decouverte_sans_cle(monkeypatch, capsys):
    monkeypatch.delenv("ZETARIS_API_KEY", raising=False)
    assert decouvrir_zetaris.main([]) == 2
    assert "ZETARIS_API_KEY" in capsys.readouterr().out


def test_preparer_env_sans_ecraser_et_masque(tmp_path, monkeypatch, capsys):
    jeton = "jeton-" + "factice-env-55"
    monkeypatch.setenv("ZETARIS_API_KEY", "cle-" + "factice-env-66")
    detection = tmp_path / "detection.json"
    detection.write_text(json.dumps({"proposition": {
        "trouve": True, "METERLESS_BASE_URL": "http://127.0.0.1:8088/v1",
        "METERLESS_MODEL_RAPIDE": "meterless/fast", "METERLESS_MODEL_PROFOND": "meterless/deep"}}), encoding="utf-8")
    decouverte = tmp_path / "decouverte.json"
    decouverte.write_text(json.dumps({"select_1": {"statut": "REUSSI"},
                                      "proposition": {"ZETARIS_CLE_SQL": "statement"}}), encoding="utf-8")
    fichier = tmp_path / "proxy-token"
    fichier.write_text(jeton, encoding="utf-8")
    sortie = tmp_path / ".env"
    options = ["--fichier-jeton", str(fichier), "--detection", str(detection), "--decouverte", str(decouverte),
               "--sortie", str(sortie)]
    assert preparer_env.main(options + ["--simuler"]) == 0 and not sortie.exists()
    assert preparer_env.main(options) == 0
    contenu = sortie.read_text(encoding="utf-8")
    assert f"METERLESS_RUNTIME_TOKEN={jeton}" in contenu and "LLM_PROVIDER=meterless" in contenu
    assert "SOURCE_PROVIDER=zetaris" in contenu and "ZETARIS_CLE_SQL=statement" in contenu
    affiche = capsys.readouterr().out
    assert jeton not in affiche and "factice-env-66" not in affiche and "METERLESS_RUNTIME_TOKEN=[MASQUE]" in affiche
    sortie.write_text("ANCIEN=1\n", encoding="utf-8")
    assert preparer_env.main(options) == 2 and sortie.read_text(encoding="utf-8") == "ANCIEN=1\n"
    assert preparer_env.main(options + ["--forcer"]) == 0 and "LLM_PROVIDER=meterless" in sortie.read_text(encoding="utf-8")
    if os.name != "nt":
        assert oct(sortie.stat().st_mode & 0o777) == "0o600"
