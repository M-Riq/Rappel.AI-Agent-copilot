import json
from pathlib import Path

import pytest

from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import LLMFaux, MemoireEnquete
from rappel.enquete.modeles import ContexteIncident
from rappel.enquete.protocoles import UsageLLM
from rappel.interfaces.abstention import AbstentionSeuil

# Les tests des fournisseurs NVIDIA, Claude et du secours de l'ancien depot ne sont pas
# repris : l'equipe passe uniquement par Meterless (voir tests/test_adaptateurs.py).

RACINE = Path(__file__).resolve().parents[1]
CANDIDATS = json.dumps([
    {"texte": "Cause generique schema", "categorie": "schema"},
    {"texte": "Cause generique ressources", "categorie": "ressources"},
    {"texte": "Cause generique reseau", "categorie": "reseau"},
])


class LLMSequence:
    def __init__(self, reponses):
        self.reponses = list(reponses)
        self.appels = []
        self.usage = UsageLLM(None, None, 0)

    def debut_execution(self):
        pass

    def completer(self, prompt, role="detective"):
        self.appels.append((prompt, role))
        self.usage.nb_appels += 1
        return self.reponses.pop(0)


def contexte_p1():
    return ContexteIncident.model_validate_json(
        (RACINE / "fixtures" / "contexte_p1.json").read_text(encoding="utf-8")
    )


def lancer_detective(llm, tmp_path):
    return enqueter(contexte_p1(), MemoireEnquete.json(tmp_path / "memoire.json"), llm, AbstentionSeuil(), mode_preuves="outils")


@pytest.mark.parametrize("enveloppe", [
    lambda contenu: f"```json\n{contenu}\n```",
    lambda contenu: f"Voici les candidats proposés :\n{contenu}\nFin.",
])
def test_detective_extrait_json_enveloppe(enveloppe, tmp_path):
    llm = LLMSequence([enveloppe(CANDIDATS)])
    resultat = lancer_detective(llm, tmp_path)
    assert len(resultat.hypotheses) == 3
    assert resultat.metriques.nb_appels_llm == 1
    assert "signature" in llm.appels[0][0]
    assert "commits_recents" in llm.appels[0][0]


def test_detective_relance_une_reponse_invalide(tmp_path):
    llm = LLMSequence(["pas du json", CANDIDATS])
    resultat = lancer_detective(llm, tmp_path)
    assert len(resultat.hypotheses) == 3
    assert resultat.metriques.nb_appels_llm == 2
    assert "réponse précédente était invalide" in llm.appels[1][0]


def test_detective_deux_reponses_invalides_abstient(tmp_path):
    llm = LLMSequence(["invalide", "toujours invalide"])
    resultat = lancer_detective(llm, tmp_path)
    assert resultat.hypotheses == []
    assert resultat.correctif is None
    assert resultat.decision_abstention.abstient
    assert any("candidats JSON invalides" in manque for manque in resultat.decision_abstention.manque)
    assert any(evenement.type == "abstention" and evenement.acteur == "systeme"
               for evenement in resultat.evenements)
    assert resultat.metriques.nb_appels_llm == 2


def test_llm_faux_compte_ses_appels():
    llm = LLMFaux()
    llm.completer("prompt")
    llm.completer("prompt")
    assert llm.usage.nb_appels == 2
    assert llm.usage.tokens_entree == 0
    assert llm.usage.tokens_sortie == 0
