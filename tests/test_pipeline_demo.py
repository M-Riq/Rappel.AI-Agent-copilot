import json
import sqlite3

import pytest

from rappel.pipeline_demo import empreinte_dossier, injecter, lire_etat, reset, schema_attendu, schema_observe
from rappel.pipeline_demo import injecteur
from rappel.pipeline_demo.pipeline import chemins, dossier_demo


def lignes_logs():
    return chemins()["logs"].read_text(encoding="utf-8").splitlines()


def commits():
    return json.loads(chemins()["commits"].read_text(encoding="utf-8"))


def test_dossier_isole_et_configurable(tmp_path, monkeypatch):
    assert dossier_demo() == (tmp_path / "pipeline_demo").resolve()
    monkeypatch.delenv("PIPELINE_DEMO_DIR")
    assert dossier_demo().as_posix().endswith("data/pipeline_demo")


def test_vert_au_depart(tmp_path):
    etat = reset()
    assert etat["statut"] == "vert" and etat["lignes_rejetees"] == 0 and etat["alertes"] == []
    with sqlite3.connect(chemins()["sortie"]) as base:
        assert base.execute("SELECT count(*) FROM transactions_propres").fetchone()[0] == 214
    journal = lignes_logs()
    assert 170 <= len(journal) <= 260
    assert any(" WARN " in ligne for ligne in journal) and not any(" ERROR " in ligne for ligne in journal)
    assert schema_observe() == schema_attendu()
    assert "a1b2c3" not in {commit["hash"] for commit in commits()}


def test_p1_panne_visible():
    etat = injecter("p1")
    assert etat["statut"] == "rouge" and etat["lignes_rejetees"] == 214
    journal = lignes_logs()
    assert sum("KeyError: 'amount'" in ligne for ligne in journal) == 214
    assert any(" ALERT " in ligne and "statut rouge" in ligne for ligne in journal)
    assert commits()[-1]["hash"] == "a1b2c3" and commits()[-1]["message"] == "rename amount->total"
    assert "total:decimal" in schema_observe() and "amount:decimal" in schema_attendu()


def test_p2_meme_famille_meme_colonne():
    etat = injecter("p2")
    assert etat["statut"] == "rouge" and etat["lignes_rejetees"] == 214
    assert "amount:text" in schema_observe() and "amount:decimal" in schema_attendu()
    assert any("TypeError: amount must be decimal, got text" in ligne for ligne in lignes_logs())
    assert commits()[-1]["message"] == "format amount with currency"


def test_p3_cause_externe_ambigue():
    nb_commits = len(reset() and commits())
    etat = injecter("p3")
    assert etat["statut"] == "rouge" and etat["lignes_rejetees"] == 0
    assert len(commits()) == nb_commits
    assert schema_observe() == schema_attendu()
    journal = lignes_logs()
    assert sum("TimeoutError" in ligne for ligne in journal) == 12
    assert sum("disk free" in ligne for ligne in journal) == 5
    assert any("latence source" in alerte for alerte in etat["alertes"])


def test_reset_idempotent():
    reset()
    premier = chemins()["entree"].read_bytes(), sorted(path.name for path in dossier_demo().rglob("*"))
    reset()
    assert (chemins()["entree"].read_bytes(), sorted(path.name for path in dossier_demo().rglob("*"))) == premier
    assert lire_etat()["statut"] == "vert"


@pytest.mark.parametrize("cycle", range(5))
def test_cinq_cycles_injection_reset(cycle):
    for scenario in ("p1", "p2", "p3"):
        assert injecter(scenario)["statut"] == "rouge"
        assert reset()["statut"] == "vert"
    assert empreinte_dossier() == empreinte_dossier()


def test_injecteur_ligne_de_commande(capsys):
    assert injecteur.main(["p1"]) == 0
    sortie = capsys.readouterr().out
    assert "ROUGE" in sortie and "ALERTE" in sortie
    assert injecteur.main(["reset"]) == 0
    assert "VERT" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        injecteur.main(["p9"])
