import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from rappel.config import Configuration


RACINE = Path(__file__).resolve().parents[1]


def charger_script(nom):
    spec = importlib.util.spec_from_file_location(nom, RACINE / "scripts" / f"{nom}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "modele,avertissement",
    [("nvidia/nemotron-3-super", False), ("autre-modele", True), (None, True)],
)
def test_meterless_catalogue_et_completion(monkeypatch, capsys, modele, avertissement):
    script = charger_script("check_meterless")
    configuration = Configuration()
    monkeypatch.setattr(script, "charger", lambda: configuration)
    llm = Mock()
    llm.tester_connexion.return_value = {
        "statut": "ok", "modeles": ["modele-catalogue"], "message": "Connexion OK."
    }
    llm.completer.return_value = {"texte": "Opérationnel", "modele": modele}
    constructeur = Mock(return_value=llm)
    monkeypatch.setattr(script, "MeterlessLLM", constructeur)
    assert script.main() == 0
    constructeur.assert_called_once_with(configuration)
    llm.tester_connexion.assert_called_once_with()
    assert llm.completer.call_args.args[0].startswith("Réponds")
    assert llm.completer.call_args.args[1] == "detective"
    sortie = capsys.readouterr().out
    assert "modele-catalogue" in sortie
    assert "Opérationnel" in sortie
    assert ("AVERTISSEMENT" in sortie) is avertissement
    assert "jeton-confidentiel" not in sortie


def test_zetaris_force_le_mode(monkeypatch, capsys):
    script = charger_script("check_zetaris")
    configuration = Configuration(source_provider="fichier")
    monkeypatch.setattr(script, "charger", lambda: configuration)
    executer = Mock(return_value={"requete": "SELECT 1 AS one", "lignes": [{"one": 1}]})
    monkeypatch.setattr(script, "run_sql", executer)
    assert script.main() == 0
    assert executer.call_args.args == ("SELECT 1 AS one",)
    effective = executer.call_args.kwargs["configuration"]
    assert effective.source_provider == "zetaris"
    assert configuration.source_provider == "fichier"
    assert json.loads(capsys.readouterr().out) == executer.return_value


@pytest.mark.parametrize("nom", ["check_meterless", "check_zetaris"])
def test_erreur_reseau_sans_secret(monkeypatch, capsys, nom):
    script = charger_script(nom)
    erreur = requests.ConnectionError("******example.invalid?token=cache")
    monkeypatch.setattr(script, "charger", Mock(side_effect=erreur))
    assert script.main() == 1
    sortie = capsys.readouterr().out
    assert "impossible" in sortie
    assert "secret" not in sortie
    assert "example.invalid" not in sortie
    assert "cache" not in sortie
