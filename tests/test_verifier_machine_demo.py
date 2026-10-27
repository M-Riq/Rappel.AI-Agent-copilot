"""Vérification de la machine de démo : sondes injectées, aucun appel réseau réel."""

from dataclasses import replace

import pytest
import requests

from rappel.config import Configuration
from scripts import check_meterless, check_postgres, verifier_machine_demo as verif

GO = 1024 ** 3
JETON = "jeton-" + "factice-zetaris-123"


def statuts(lignes):
    return {ligne["case"]: ligne["statut"] for ligne in lignes}


def test_systeme_seuils():
    ok = verif.verifier_systeme(memoire=lambda: 15.7 * GO, disque=lambda: 20 * GO, version=(3, 11, 9),
                                outil=lambda nom: "/usr/bin/git", docker=lambda: ("REUSSI", "démon actif"))
    assert set(statuts(ok).values()) == {"REUSSI"}
    ko = verif.verifier_systeme(memoire=lambda: 8 * GO, disque=lambda: 5 * GO, version=(3, 13, 0),
                                outil=lambda nom: None, docker=lambda: ("ECHOUE", "docker introuvable"))
    assert statuts(ko) == {"RAM": "ECHOUE", "Disque": "ECHOUE", "Python": "ECHOUE", "git": "ECHOUE", "Docker": "ECHOUE"}
    inconnu = verif.verifier_systeme(memoire=lambda: None, disque=lambda: 20 * GO, version=(3, 12),
                                     outil=lambda nom: "git", docker=lambda: ("REUSSI", ""))
    assert statuts(inconnu)["RAM"] == "NON_MESURE"


def test_configuration_sans_valeur_affichee():
    configuration = replace(Configuration(), zetaris_api_key=JETON, meterless_runtime_token="",
                            meterless_model_rapide="", meterless_model_profond="")
    simule = statuts(verif.verifier_configuration(configuration))
    assert simule["Config zetaris"] == "REUSSI" and simule["Config meterless"] == "NON_MESURE"
    confirme = verif.verifier_configuration(configuration, confirmer=True)
    assert statuts(confirme)["Config meterless"] == "ECHOUE"
    assert JETON not in str(confirme)


def test_requete_deux_sources_simulee():
    ligne = verif.requete_deux_sources_simulee(Configuration())
    assert ligne["statut"] == "REUSSI" and "simulé" in ligne["detail"]


@pytest.fixture
def sans_reseau(monkeypatch):
    def interdit(*args, **kwargs):
        raise AssertionError("appel réseau interdit sans --confirmer")
    monkeypatch.setattr(requests, "get", interdit)
    monkeypatch.setattr(requests, "post", interdit)
    monkeypatch.setattr(verif, "verifier_systeme", lambda: [verif.ligne("RAM", "REUSSI", "16 Go")])


def test_sans_confirmer_aucun_appel_reel(sans_reseau, tmp_path, capsys):
    code = verif.main(["--json", str(tmp_path / "v.json")])
    sortie = capsys.readouterr().out
    assert code == 0
    for nom in ("Zetaris SELECT 1", "Zetaris tables Rappel", "Meterless", "Postgres"):
        assert f"{nom} | NON_MESURE | --confirmer requis" in sortie
    assert (tmp_path / "v.json").exists()


def test_avec_confirmer_services_simules(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ZETARIS_API_KEY", JETON)
    monkeypatch.setattr(verif, "verifier_systeme", lambda: [])

    from rappel.interfaces import tools_zetaris
    vrai_run_sql = tools_zetaris.run_sql

    def faux_run_sql(sql, *, configuration=None, scenario="p1"):
        if configuration.source_provider == "fichier":
            return vrai_run_sql(sql, configuration=configuration, scenario=scenario)
        if sql == "SELECT 1 AS one":
            return {"requete": sql, "lignes": [{"one": 1}]}
        if sql.startswith("SELECT 1 AS reachable FROM "):
            return {"requete": sql, "lignes": []}
        raise RuntimeError(f"Zetaris a refusé la requête pour {JETON}")

    monkeypatch.setattr("rappel.interfaces.tools_zetaris.run_sql", faux_run_sql)
    monkeypatch.setattr(check_meterless, "main", lambda: print(f"Connexion Meterless impossible {JETON}") or 1)
    monkeypatch.setattr(check_postgres, "main", lambda: print("Aller-retour : OK") or 0)
    code = verif.main(["--confirmer", "--json", str(tmp_path / "v.json")])
    sortie = capsys.readouterr().out
    assert code == 1
    assert "2 sources (simulé) | REUSSI" in sortie and "Zetaris SELECT 1 | REUSSI" in sortie
    assert "Zetaris tables Rappel | REUSSI" in sortie
    assert "Meterless | ECHOUE" in sortie and "Postgres | REUSSI" in sortie
    assert JETON not in sortie and JETON not in (tmp_path / "v.json").read_text(encoding="utf-8")
