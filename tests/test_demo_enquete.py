import json

import pytest

from scripts import demo_enquete
from src.rappel.interfaces.llm import LLMFaux


@pytest.fixture(autouse=True)
def environnement(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "faux")
    monkeypatch.delenv("LLM_FALLBACK", raising=False)
    monkeypatch.setenv("LLM_MAX_APPELS", "20")
    monkeypatch.delenv("WAR_ROOM_MAX_TOURS", raising=False)
    monkeypatch.delenv("WAR_ROOM_MAX_APPELS_LLM", raising=False)


@pytest.mark.parametrize("debat", [False, True])
def test_demo_resultats_et_memoire_isolee(debat, tmp_path, capsys):
    resultats = demo_enquete.jouer_demo(LLMFaux(), war_room=debat)
    assert resultats["P2"].metriques.nb_hypotheses_testees < resultats["P1"].metriques.nb_hypotheses_testees
    assert resultats["P2"].metriques.nb_ecartees_par_memoire == 3
    assert resultats["P3"].decision_abstention.abstient
    assert demo_enquete.controle_sante(resultats) == (0, [])
    demo_enquete.afficher_resultats(resultats)
    sortie = capsys.readouterr().out
    assert all(nom in sortie for nom in ("P1", "P2", "P3"))
    assert "75.00%" in sortie
    assert "Deja refutee en memoire" in sortie
    assert not (tmp_path / "data" / "memoire.json").exists()


def test_main_export_json_et_code_zero(tmp_path, capsys):
    chemin = tmp_path / "export" / "demo.json"
    assert demo_enquete.main(["--json", str(chemin)], fabrique_llm=LLMFaux) == 0
    document = json.loads(chemin.read_text(encoding="utf-8"))
    assert set(document["scenarios"]) == {"P1", "P2", "P3"}
    assert document["controle_sante"]["code"] == 0
    assert document["scenarios"]["P2"]["evenements"]
    assert document["scenarios"]["P3"]["correctif"] is None
    assert "Controle de sante : OK" in capsys.readouterr().out


def test_code_un_si_ambigu_conclut(capsys):
    contextes = demo_enquete.charger_contextes()
    donnees = contextes["P3"].model_dump()
    donnees["schema"] = {"attendu": ["ancien:int"], "observe": ["nouveau:int"]}
    contextes["P3"] = donnees
    assert demo_enquete.main(["--mode", "outils"], fabrique_llm=LLMFaux, contextes=contextes) == 1
    sortie = capsys.readouterr().out
    assert "ECHEC" in sortie and "P3 doit s'abstenir" in sortie


def test_refus_reel_avant_construction(monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    assert demo_enquete.main([], fabrique_llm=lambda: pytest.fail("Appel interdit")) == 1
    assert "--confirmer" in capsys.readouterr().out


def test_reduction_sans_division_zero():
    assert demo_enquete.reduction(4, 1) == 75
    assert demo_enquete.reduction(0, 0) is None


def test_export_masque_secret(tmp_path, monkeypatch):
    secret = "cle-factice-export"
    monkeypatch.setenv("NVIDIA_API_KEY", secret)
    resultats = demo_enquete.jouer_demo(LLMFaux())
    resultats["P2"].evenements[0].texte = secret
    chemin = tmp_path / "demo.json"
    demo_enquete.exporter_json(resultats, chemin)
    assert secret not in chemin.read_text(encoding="utf-8")
    assert json.loads(chemin.read_text(encoding="utf-8"))["scenarios"]["P2"]["evenements"][0]["texte"] == "[MASQUE]"