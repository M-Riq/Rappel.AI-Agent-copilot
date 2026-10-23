import json

import pytest

from rappel.enquete.adaptateurs import LLMFaux
from rappel.pipeline_demo import lire_etat
from scripts import recette_globale


def test_recette_globale_mode_fake(tmp_path):
    rapport = recette_globale.evaluer(cycles=5)
    assert rapport["echecs"] == []
    assert [element["case"] for element in rapport["cases"]] == ["G1", "G2", "G3", "G4"]
    assert all(element["simule"] and element["reel"] == recette_globale.NON_MESURE for element in rapport["cases"])
    assert rapport["cases"][3]["detail"] == "5/5 cycle(s) complets réussis"
    assert lire_etat()["statut"] == "vert"


class CorrectifInapplicable(LLMFaux):
    def completer(self, prompt, role="detective", tools=None):
        texte = super().completer(prompt, role, tools)
        if role == "correctif":
            donnees = json.loads(texte)
            donnees["patch"] = "aucun patch exploitable"
            return json.dumps(donnees)
        return texte


def test_echec_si_le_correctif_ne_repare_pas():
    rapport = recette_globale.evaluer(fabrique_llm=CorrectifInapplicable, cycles=5)
    statuts = {element["case"]: element["statut"] for element in rapport["cases"]}
    assert statuts["G1"] == recette_globale.ECHOUE and statuts["G4"] == recette_globale.ECHOUE
    assert statuts["G3"] == recette_globale.REUSSI


def test_main_json_et_codes(tmp_path):
    chemin = tmp_path / "rg.json"
    assert recette_globale.main(["--json", str(chemin), "--cycles", "5"]) == 0
    assert json.loads(chemin.read_text(encoding="utf-8"))["echecs"] == []
    assert recette_globale.main(["--json", str(chemin), "--cycles", "5"], fabrique_llm=CorrectifInapplicable) == 1


def test_reel_exige_confirmer(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "meterless")
    assert recette_globale.main(["--json", str(tmp_path / "x.json")], fabrique_llm=lambda: pytest.fail("interdit")) == 2
