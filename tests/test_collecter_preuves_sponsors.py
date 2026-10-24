"""Collecte des preuves sponsors : rien de simulé n'est présenté comme réel."""

import json
from types import SimpleNamespace

from rappel.config import Configuration
from scripts import collecter_preuves_sponsors as collecte


def evenement(type_, modele=None, requete_sql=None):
    return SimpleNamespace(type=type_, modele=modele, requete_sql=requete_sql)


USAGE = {"nb_appels": 4, "tokens_entree": 100, "tokens_sortie": 50,
         "par_voie": {"rapide": {"nb_appels": 3}, "profonde": {"nb_appels": 1}}}


def test_mode_simule_tout_non_mesure(tmp_path, capsys):
    destination = tmp_path / "p.json"
    assert collecte.main(["--json", str(destination)]) == 0
    rapport = json.loads(destination.read_text(encoding="utf-8"))
    assert {s["statut"] for s in rapport["sponsors"].values()} == {"NON_MESURE"}
    assert rapport["modes"]["simulation"] is True
    assert rapport["sponsors"]["zetaris"]["requetes"]
    assert "colonne renommée" in rapport["conclusion"]
    assert "PAS des preuves sponsor" in capsys.readouterr().out


def test_reel_sans_confirmer_refuse(monkeypatch, tmp_path):
    monkeypatch.setenv("SOURCE_PROVIDER", "zetaris")
    assert collecte.main(["--json", str(tmp_path / "p.json")]) == 2
    monkeypatch.setenv("SOURCE_PROVIDER", "fichier")
    monkeypatch.setenv("LLM_PROVIDER", "meterless")
    assert collecte.main(["--json", str(tmp_path / "p.json")]) == 2
    assert not (tmp_path / "p.json").exists()


def test_evaluation_reelle_reussie():
    configuration = Configuration(llm_provider="meterless", source_provider="zetaris")
    resultat = SimpleNamespace(source_preuves="zetaris", evenements=[
        evenement("hypothese", "nvidia/nemotron-nano"), evenement("preuve", requete_sql="SELECT 1 LIMIT 50"),
        evenement("decision", "nvidia/nemotron-super"),
    ])
    sponsors = collecte.evaluer(configuration, resultat, USAGE, confirme=True)
    assert {nom: s["statut"] for nom, s in sponsors.items()} == {"zetaris": "REUSSI", "meterless": "REUSSI", "nemotron": "REUSSI"}
    assert sponsors["meterless"]["par_voie"]["profonde"]["nb_appels"] == 1


def test_evaluation_reelle_routage_hors_nemotron():
    configuration = Configuration(llm_provider="meterless", source_provider="zetaris")
    resultat = SimpleNamespace(source_preuves="simule-sqlite", evenements=[evenement("hypothese", "autre-modele")])
    sponsors = collecte.evaluer(configuration, resultat, USAGE, confirme=True)
    assert sponsors["nemotron"]["statut"] == "ECHOUE"
    assert sponsors["nemotron"]["detail"] == "le routage a choisi autre-modele : fixer la voie"
    assert sponsors["zetaris"]["statut"] == "ECHOUE"


def test_non_confirme_jamais_reussi():
    configuration = Configuration(llm_provider="meterless", source_provider="zetaris")
    resultat = SimpleNamespace(source_preuves="zetaris", evenements=[evenement("preuve", "nemotron", "SELECT 1")])
    sponsors = collecte.evaluer(configuration, resultat, USAGE, confirme=False)
    assert {s["statut"] for s in sponsors.values()} == {"NON_MESURE"}
