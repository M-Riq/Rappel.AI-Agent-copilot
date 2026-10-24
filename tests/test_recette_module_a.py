import json
from types import SimpleNamespace

import pytest

from scripts import recette_module_a as recette
from rappel.enquete.adaptateurs import LLMFaux
from rappel.enquete.protocoles import lire_donnees


@pytest.fixture(scope="module")
def rapport():
    return recette.evaluer(repetitions=5)


def test_mode_fake_aucun_echec(rapport):
    assert rapport["echecs"] == []
    assert [element["case"] for element in rapport["cases"]] == [f"A{numero}" for numero in range(1, 16)]
    assert rapport["modes"]["simulation"] is True


def test_non_mesures_attendues_listees(rapport):
    # En mode fake/fichier : A14 n'est pas mesurable ; la partie reelle de A3, A7, A14, A15 non plus.
    assert rapport["non_mesures"] == ["A14"]
    assert rapport["parties_reelles_non_mesurees"] == ["A3", "A7", "A14", "A15"]
    raisons = {element["case"]: element["raison_reel"] for element in rapport["cases"]}
    assert raisons["A3"] == "service réel requis : Zetaris"
    assert raisons["A7"] == "service réel requis : Postgres"
    assert raisons["A15"] == raisons["A14"] == "service réel requis : Nightshift"
    assert all(element["simule"] for element in rapport["cases"] if element["case"] in {"A3", "A7", "A14", "A15"})


class ConclusionFausse(LLMFaux):
    def completer(self, prompt, role="detective", tools=None):
        texte = super().completer(prompt, role, tools)
        if role == "conclusion":
            return json.dumps({"cause_confirmee": "autre cause sans rapport", "requetes_citees": []})
        if role == "correctif":
            donnees = json.loads(texte)
            donnees["justification"] = "Cause differente (requetes q1)"
            return json.dumps(donnees)
        return texte


def test_llm_defaillant_fait_echouer_la_case(tmp_path):
    rapport = recette.evaluer(fabrique_llm=ConclusionFausse, repetitions=5)
    statuts = {element["case"]: element["statut"] for element in rapport["cases"]}
    assert statuts["A5"] == recette.ECHOUE and "A5" in rapport["echecs"]
    assert statuts["A1"] == recette.REUSSI


def test_a14_mode_reel_routage():
    evenement = lambda modele: SimpleNamespace(modele=modele)
    reel = [SimpleNamespace(evenements=[evenement("nvidia/Nemotron-Super"), evenement(None)])]
    assert recette.case_a14(reel, llm_reel=True)["statut"] == recette.REUSSI
    autre = [SimpleNamespace(evenements=[evenement("meterless/auto-choisi")])]
    resultat = recette.case_a14(autre, llm_reel=True)
    assert resultat["statut"] == recette.ECHOUE
    assert resultat["detail"] == "le routage a choisi meterless/auto-choisi : fixer la voie"
    assert recette.case_a14([SimpleNamespace(evenements=[])], llm_reel=True)["statut"] == recette.ECHOUE


def test_main_ecrit_le_rapport_et_code_zero(tmp_path, capsys):
    chemin = tmp_path / "recette.json"
    assert recette.main(["--json", str(chemin)]) == 0
    document = json.loads(chemin.read_text(encoding="utf-8"))
    assert document["echecs"] == [] and len(document["cases"]) == 15
    sortie = capsys.readouterr().out
    assert "SIMULATION" in sortie and "aucun ECHOUE" in sortie


def test_main_code_un_si_echec(tmp_path):
    assert recette.main(["--json", str(tmp_path / "r.json")], fabrique_llm=ConclusionFausse) == 1


def test_fournisseur_reel_exige_confirmer(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "meterless")
    assert recette.main(["--json", str(tmp_path / "r.json")], fabrique_llm=lambda: pytest.fail("Appel interdit")) == 2
    sortie = capsys.readouterr().out
    assert "--confirmer" in sortie and "LLM_MAX_APPELS" in sortie
    assert not (tmp_path / "r.json").exists()


def test_scripte_utilise_bien_les_donnees_du_moteur():
    # Garde-fou du test : le double scripte recoit des prompts structures du moteur.
    llm = ConclusionFausse()
    texte = llm.completer("Role: hypotheses.\nDONNEES:\n{}", "hypotheses")
    assert json.loads(texte) and lire_donnees("x\nDONNEES:\n{}") == {}
