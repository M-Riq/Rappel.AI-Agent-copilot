import json
from pathlib import Path

import pytest

from scripts import essai_detective, essai_llm, essai_war_room
from src.rappel.contrats import ContexteIncident
from src.rappel.interfaces.llm import LLMFaux


@pytest.fixture(autouse=True)
def environnement(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "faux")
    monkeypatch.delenv("LLM_FALLBACK", raising=False)
    monkeypatch.setenv("LLM_MAX_APPELS", "20")


def contexte():
    chemin = Path(__file__).resolve().parents[1] / "fixtures" / "contexte_p1.json"
    return ContexteIncident.model_validate_json(chemin.read_text(encoding="utf-8"))


def test_statistiques_detective_faux(capsys):
    llm = LLMFaux()
    statistiques = essai_detective.mesurer_detective(contexte(), llm, repetitions=3)
    assert statistiques["taux_valides"] == 1.0
    assert statistiques["nb_moyen_candidats"] == 4
    assert statistiques["categories_manquantes"] == []
    assert statistiques["tokens_entree_moyens"] == 0
    assert statistiques["tokens_sortie_moyens"] == 0
    assert statistiques["verdict"] == "OK"
    assert llm.usage.nb_appels == 3
    assert "Brut" not in capsys.readouterr().out


@pytest.mark.parametrize("reponse", ["invalide", '[{"texte":"cause","categorie":"autre"}]'])
def test_statistiques_invalides(reponse):
    mesure = essai_detective.analyser_reponse(reponse)
    mesure.update(duree_ms=1, tokens_entree=None, tokens_sortie=None)
    statistiques = essai_detective.calculer_statistiques([mesure])
    assert statistiques["verdict"] == "A CORRIGER"
    assert statistiques["raisons"]
    assert statistiques["tokens_entree_moyens"] is None


def test_comparaison_war_room_faux(capsys):
    comparaison = essai_war_room.mesurer_comparaison(contexte(), LLMFaux())
    assert comparaison["decision_identique"]
    assert comparaison["nb_objections"] == 1
    assert comparaison["nb_objections_sans_reponse"] == 0
    assert comparaison["nb_appels_debat"] == 3
    assert comparaison["sans_debat"]["appels"] == 1
    assert comparaison["avec_debat"]["appels"] == 4
    assert comparaison["avec_debat"]["tokens_entree"] == 0
    sortie = capsys.readouterr().out
    assert all(role in sortie for role in ("historien", "sceptique", "detective"))
    assert "Brut" not in sortie


@pytest.mark.parametrize("script", [essai_detective, essai_war_room, essai_llm])
def test_refus_fournisseur_reel_sans_confirmation(script, monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    appels = []
    assert script.main([], fabrique_llm=lambda: appels.append(True)) == 2
    assert appels == []
    sortie = capsys.readouterr().out
    assert "REFUS" in sortie and "--confirmer" in sortie
    assert "Appels prevus" in sortie and "Limite de depense" in sortie


@pytest.mark.parametrize("script", [essai_detective, essai_war_room, essai_llm])
def test_secours_reel_exige_confirmation(script, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK", "claude")
    assert script.main([], fabrique_llm=lambda: pytest.fail("Ne doit pas construire le fournisseur")) == 2


def test_budget_detective_respecte(monkeypatch):
    monkeypatch.setenv("LLM_MAX_APPELS", "1")
    llm = LLMFaux()
    statistiques = essai_detective.mesurer_detective(contexte(), llm, repetitions=3)
    assert llm.usage.nb_appels == 1
    assert statistiques["verdict"] == "A CORRIGER"


def test_brut_masque_cle_et_limite_longueur(monkeypatch, capsys):
    secret = "cle-factice-secrete"
    monkeypatch.setenv("NVIDIA_API_KEY", secret)
    class Echo(LLMFaux):
        def completer(self, prompt, role="detective"):
            super().completer(prompt, role)
            return secret + "x" * 1000
    essai_detective.mesurer_detective(contexte(), Echo(), repetitions=1, brut=True)
    sortie = capsys.readouterr().out
    assert secret not in sortie
    ligne = next(ligne for ligne in sortie.splitlines() if ligne.startswith("Brut"))
    assert len(ligne.split(" : ", 1)[1]) <= 400


def test_fil_debat_masque_cle(monkeypatch, capsys):
    secret = "cle-factice-secrete"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    class Echo(LLMFaux):
        def completer(self, prompt, role="detective"):
            texte = super().completer(prompt, role)
            if role == "sceptique" and prompt.startswith("{"):
                objet = json.loads(texte)
                if objet["objection"] is not None:
                    objet["objection"] = secret
                return json.dumps(objet)
            return texte
    essai_war_room.mesurer_comparaison(contexte(), Echo(), brut=True)
    assert secret not in capsys.readouterr().out


@pytest.mark.parametrize("script", [essai_detective, essai_war_room])
def test_confirmation_fournisseur_simule(script, monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "cle-factice-confirmee")
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "contexte_p1.json"
    assert script.main(["--confirmer", "--fixture", str(fixture)], fabrique_llm=LLMFaux) == 0
    assert "cle-factice-confirmee" not in capsys.readouterr().out


def test_prompt_identique_reutilise_fonction_graphe():
    class Espion(LLMFaux):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def completer(self, prompt, role="detective"):
            self.prompts.append(prompt)
            return super().completer(prompt, role)
    llm = Espion()
    incident = contexte()
    essai_detective.mesurer_detective(incident, llm, repetitions=3)
    assert llm.prompts == [essai_detective.construire_prompt(incident)] * 3


@pytest.mark.parametrize("script", [essai_detective, essai_war_room])
def test_exception_ne_divulgue_pas_secret(script, monkeypatch, capsys):
    secret = "secret-factice-erreur"
    monkeypatch.setenv("NVIDIA_API_KEY", secret)
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "contexte_p1.json"
    def echouer():
        raise RuntimeError(secret)
    assert script.main(["--fixture", str(fixture)], fabrique_llm=echouer) == 1
    assert secret not in capsys.readouterr().out


def test_connexion_faux_sans_confirmation(capsys):
    llm = LLMFaux()
    assert essai_llm.main([], fabrique_llm=lambda: llm) == 0
    assert llm.usage.nb_appels == 1
    sortie = capsys.readouterr().out
    assert "Fournisseur : faux" in sortie
    assert "Appels prevus" in sortie


def test_connexion_reelle_confirmee_simulee(monkeypatch, capsys):
    secret = "cle-factice-connexion"
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", secret)
    llm = LLMFaux()
    llm.modele = secret
    assert essai_llm.main(["--confirmer"], fabrique_llm=lambda: llm) == 0
    assert llm.usage.nb_appels == 1
    assert secret not in capsys.readouterr().out


def test_connexion_exception_sans_secret(monkeypatch, capsys):
    secret = "secret-factice-connexion"
    monkeypatch.setenv("NVIDIA_API_KEY", secret)
    def echouer():
        raise RuntimeError(secret)
    assert essai_llm.main([], fabrique_llm=echouer) == 1
    assert secret not in capsys.readouterr().out