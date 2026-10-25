"""Recette réelle en une commande, avec de faux services (aucun réseau)."""

import json

import pytest

from rappel.enquete.adaptateurs import LLMFaux
from scripts import recette_reelle as recette

JETON = "jeton-" + "factice-reel-99"
CLES = ("METERLESS_RUNTIME_TOKEN", "METERLESS_MODEL_RAPIDE", "METERLESS_MODEL_PROFOND", "ZETARIS_API_KEY", "DATABASE_URL")


class LLMNemotron(LLMFaux):
    modele = "nvidia/nemotron-factice"

    def _appeler_fournisseur(self, prompt, role, tools):
        reponse = super()._appeler_fournisseur(prompt, role, tools)
        return {**reponse, "modele": f"{self.modele}-{'profond' if role in ('conclusion', 'correctif') else 'rapide'}"}


class LLMAutreModele(LLMNemotron):
    modele = "meterless/auto-choisi"


def interdit(*args, **kwargs):
    pytest.fail("aucun service ne doit être appelé")


@pytest.fixture(autouse=True)
def sans_cles(monkeypatch):
    for nom in CLES:
        monkeypatch.delenv(nom, raising=False)


@pytest.fixture
def cles_nightshift(monkeypatch):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", JETON)
    monkeypatch.setenv("METERLESS_MODEL_RAPIDE", "voie-rapide")
    monkeypatch.setenv("METERLESS_MODEL_PROFOND", "voie-profonde")


def statuts(rapport):
    return {element["etape"]: element["statut"] for element in rapport["etapes"]}


def test_confirmer_obligatoire(tmp_path, capsys):
    assert recette.main(["--json", str(tmp_path / "r.json")], fabrique_llm=interdit) == 2
    assert "--confirmer" in capsys.readouterr().out and not (tmp_path / "r.json").exists()


def test_sans_cles_tout_non_mesure_jamais_echoue(tmp_path):
    verifications = {nom: interdit for nom in ("check_meterless", "check_zetaris", "check_postgres")}
    destination = tmp_path / "r.json"
    assert recette.main(["--confirmer", "--json", str(destination)], fabrique_llm=interdit,
                        verifications=verifications) == 0
    rapport = json.loads(destination.read_text(encoding="utf-8"))
    assert [element["etape"] for element in rapport["etapes"]] == list(recette.ORDRE)
    assert set(statuts(rapport).values()) == {"NON_MESURE"} and rapport["echecs"] == []
    raisons = {element["etape"]: element["detail"] for element in rapport["etapes"]}
    assert "METERLESS_RUNTIME_TOKEN" in raisons["nightshift"] and "ZETARIS_API_KEY" in raisons["zetaris"]
    assert "DATABASE_URL" in raisons["postgres"] and "Nightshift" in raisons["A14"]


def test_nightshift_seul_et_zetaris_en_echec(cles_nightshift, monkeypatch, tmp_path):
    monkeypatch.setenv("ZETARIS_API_KEY", JETON + "-z")
    verifications = {"check_meterless": lambda: (0, "Modèle effectif : nemotron"),
                     "check_zetaris": lambda: (1, f"Connexion Zetaris impossible {JETON}-z"),
                     "check_postgres": interdit}
    destination = tmp_path / "r.json"
    code = recette.main(["--confirmer", "--json", str(destination)], fabrique_llm=LLMNemotron,
                        verifications=verifications)
    rapport = json.loads(destination.read_text(encoding="utf-8"))
    assert code == 1 and rapport["echecs"] == ["zetaris"]
    assert statuts(rapport) == {"nightshift": "REUSSI", "zetaris": "ECHOUE", "postgres": "NON_MESURE", "A14": "REUSSI",
                                "A15": "REUSSI", "boucle": "NON_MESURE", "war_room": "REUSSI", "sponsors": "NON_MESURE"}
    texte = destination.read_text(encoding="utf-8")
    assert JETON not in texte and "nemotron-factice-rapide" in texte and "nemotron-factice-profond" in texte


def test_a14_routage_hors_nemotron():
    resultat = recette.case_a14(LLMAutreModele)
    assert resultat["statut"] == "ECHOUE"
    # Format exact du cahier : « le routage a choisi <modèle> : fixer la voie » (voie précisée ensuite).
    assert resultat["detail"].startswith("le routage a choisi meterless/auto-choisi-rapide : fixer la voie rapide")


def test_war_room_plafond_affiche(capsys, monkeypatch):
    monkeypatch.setenv("WAR_ROOM_MAX_TOURS", "1")
    resultat = recette.case_war_room(LLMNemotron)
    assert resultat["statut"] == "REUSSI" and "plafond de tours atteint" in resultat["detail"]
    assert "au plus 1 tour(s)" in capsys.readouterr().out


class Cycle:
    def __init__(self, resume):
        self.resume = resume


def faux_cycles(p2_evenements):
    def lancer(scenarios, **options):
        return [Cycle({"statut": "termine", "enquete": {"conclusion": "colonne renommée", "nb_evenements": 20,
                                                         "nb_appels_llm": 5}, "action": {"statut": "execute"}}),
                Cycle({"statut": "termine", "enquete": {"conclusion": "x", "nb_evenements": p2_evenements,
                                                         "nb_appels_llm": 3}, "action": {"statut": "en_attente"}}),
                Cycle({"statut": "termine", "enquete": {"abstention": True, "nb_evenements": 18, "nb_appels_llm": 3},
                       "action": {"statut": "aucune", "pipeline_inchange": True}})]
    return lancer


def test_toutes_etapes_reussies_avec_faux_services(cles_nightshift, monkeypatch, tmp_path):
    monkeypatch.setenv("ZETARIS_API_KEY", JETON + "-z")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:" + "motdepasse-factice@hote/base")
    monkeypatch.setattr(recette, "cles_presentes", lambda configuration: {"nightshift": None, "zetaris": None,
                                                                            "postgres": None})
    monkeypatch.setattr(recette, "case_a15", lambda fabrique: recette.etape("A15", "REUSSI", "5/5"))
    monkeypatch.setattr(recette, "case_war_room", lambda fabrique: recette.etape("war_room", "REUSSI", "1 tour"))
    monkeypatch.setattr(recette, "case_sponsors", lambda fabrique: recette.etape("sponsors", "REUSSI", "ok"))
    verifications = {nom: (lambda: (0, "ok postgresql://u:motdepasse-factice@hote/base"))
                     for nom in ("check_meterless", "check_zetaris", "check_postgres")}
    rapport = recette.executer(LLMNemotron, verifications, faux_cycles(10))
    assert set(statuts(rapport).values()) == {"REUSSI"}
    assert "motdepasse-factice" not in json.dumps(rapport)
    trop_long = recette.executer(LLMNemotron, verifications, faux_cycles(15))
    assert statuts(trop_long)["boucle"] == "ECHOUE"


def test_modes_reels_restaure_l_environnement(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.delenv("SOURCE_PROVIDER", raising=False)
    with recette.modes_reels(True, True, False):
        import os
        assert os.environ["LLM_PROVIDER"] == "meterless" and os.environ["SOURCE_PROVIDER"] == "zetaris"
    import os
    assert os.environ["LLM_PROVIDER"] == "fake" and "SOURCE_PROVIDER" not in os.environ
