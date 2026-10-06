import json
from pathlib import Path

import httpx
import pytest

from src.rappel.contrats import ContexteIncident
from src.rappel.enquete import enqueter
from src.rappel.interfaces.abstention import AbstentionSeuil
from src.rappel.interfaces.llm import (
    LLMAvecSecours,
    LLMBudgetEpuise,
    LLMClaude,
    LLMFaux,
    LLMIndisponible,
    LLMNvidia,
    UsageLLM,
    creer_llm,
)
from src.rappel.interfaces.memoire import MemoireJSON


CLE_FACTICE = "cle-factice-ne-pas-divulguer"
CANDIDATS = json.dumps([
    {"texte": "Cause generique schema", "categorie": "schema"},
    {"texte": "Cause generique ressources", "categorie": "ressources"},
    {"texte": "Cause generique reseau", "categorie": "reseau"},
])


def configurer_nvidia(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", CLE_FACTICE)
    monkeypatch.setenv("NVIDIA_MODEL", "modele-configure")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://nvidia.test/v1")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.25")
    monkeypatch.setenv("LLM_MAX_TOKENS", "77")
    monkeypatch.setenv("LLM_TIMEOUT_S", "5")
    monkeypatch.setenv("LLM_MAX_APPELS", "20")


def configurer_claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", CLE_FACTICE)
    monkeypatch.setenv("ANTHROPIC_MODEL", "modele-configure")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.25")
    monkeypatch.setenv("LLM_MAX_TOKENS", "77")
    monkeypatch.setenv("LLM_TIMEOUT_S", "5")
    monkeypatch.setenv("LLM_MAX_APPELS", "20")


def client_json(reponse, statut=200):
    def handler(request):
        return httpx.Response(statut, json=reponse, request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_nvidia_requete_usage_et_reponse(monkeypatch):
    configurer_nvidia(monkeypatch)
    requetes = []

    def handler(request):
        requetes.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "réponse NVIDIA"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        llm = LLMNvidia(client)
        assert llm.completer("prompt de test", "detective") == "réponse NVIDIA"
    finally:
        client.close()
    requete = requetes[0]
    assert str(requete.url) == "https://nvidia.test/v1/chat/completions"
    assert requete.headers["Authorization"] == f"Bearer {CLE_FACTICE}"
    corps = json.loads(requete.content)
    assert corps == {
        "model": "modele-configure",
        "messages": [
            {"role": "system", "content": "Role: detective"},
            {"role": "user", "content": "prompt de test"},
        ],
        "temperature": 0.25,
        "max_tokens": 77,
    }
    assert llm.usage == UsageLLM(11, 7, 1)


def test_claude_requete_usage_et_reponse(monkeypatch):
    configurer_claude(monkeypatch)
    requetes = []

    def handler(request):
        requetes.append(request)
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "réponse Claude"}],
            "usage": {"input_tokens": 13, "output_tokens": 9},
        }, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        llm = LLMClaude(client)
        assert llm.completer("prompt de test", "historien") == "réponse Claude"
    finally:
        client.close()
    requete = requetes[0]
    assert str(requete.url) == "https://api.anthropic.com/v1/messages"
    assert requete.headers["x-api-key"] == CLE_FACTICE
    assert requete.headers["anthropic-version"] == "2023-06-01"
    assert json.loads(requete.content) == {
        "model": "modele-configure", "max_tokens": 77, "temperature": 0.25,
        "messages": [{"role": "user", "content": "Role: historien\n\nprompt de test"}],
    }
    assert llm.usage == UsageLLM(13, 9, 1)


@pytest.mark.parametrize(
    ("fournisseur", "configurer", "classe"),
    [("nvidia", configurer_nvidia, LLMNvidia), ("claude", configurer_claude, LLMClaude)],
)
def test_factory_selection(monkeypatch, fournisseur, configurer, classe):
    configurer(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", fournisseur)
    assert isinstance(creer_llm(), classe)


def test_factory_faux_par_defaut(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_FALLBACK", raising=False)
    assert isinstance(creer_llm(), LLMFaux)


@pytest.mark.parametrize(
    ("fournisseur", "cle", "modele"),
    [("nvidia", "NVIDIA_API_KEY", "NVIDIA_MODEL"),
     ("claude", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL")],
)
def test_factory_cle_manquante(monkeypatch, fournisseur, cle, modele):
    monkeypatch.setenv("LLM_PROVIDER", fournisseur)
    monkeypatch.delenv(cle, raising=False)
    monkeypatch.setenv(modele, "modele-configure")
    with pytest.raises(ValueError, match=f"{cle} manquante") as erreur:
        creer_llm()
    assert CLE_FACTICE not in str(erreur.value)


@pytest.mark.parametrize(
    ("fournisseur", "cle", "modele"),
    [("nvidia", "NVIDIA_API_KEY", "NVIDIA_MODEL"),
     ("claude", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL")],
)
def test_factory_modele_manquant(monkeypatch, fournisseur, cle, modele):
    monkeypatch.setenv("LLM_PROVIDER", fournisseur)
    monkeypatch.setenv(cle, CLE_FACTICE)
    monkeypatch.delenv(modele, raising=False)
    with pytest.raises(ValueError, match=f"{modele} manquante"):
        creer_llm()


def test_factory_fournisseur_inconnu(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "inconnu")
    with pytest.raises(ValueError, match="Fournisseur LLM inconnu"):
        creer_llm()


def test_factory_fallback(monkeypatch):
    configurer_nvidia(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("LLM_FALLBACK", "faux")
    llm = creer_llm()
    assert isinstance(llm, LLMAvecSecours)
    assert isinstance(llm.principal, LLMNvidia)
    assert isinstance(llm.secours, LLMFaux)


def test_secours_apres_http_500(monkeypatch):
    configurer_nvidia(monkeypatch)
    client = client_json({"error": "details privés"}, statut=500)
    try:
        principal = LLMNvidia(client)
        secours = LLMFaux()
        llm = LLMAvecSecours(principal, secours)
        assert llm.completer("prompt") == LLMFaux().completer("prompt")
        assert llm.basculements == 1
        assert llm.usage.nb_appels == 2
    finally:
        client.close()


def test_budget_appels(monkeypatch):
    configurer_nvidia(monkeypatch)
    monkeypatch.setenv("LLM_MAX_APPELS", "1")
    client = client_json({"choices": [{"message": {"content": "ok"}}], "usage": {}})
    try:
        llm = LLMNvidia(client)
        assert llm.completer("premier") == "ok"
        with pytest.raises(LLMBudgetEpuise, match="LLM_MAX_APPELS"):
            llm.completer("second")
        assert llm.usage.nb_appels == 1
    finally:
        client.close()


def test_cles_absentes_des_exceptions_et_repr(monkeypatch):
    configurer_nvidia(monkeypatch)
    client = client_json({"error": CLE_FACTICE}, statut=500)
    try:
        llm = LLMNvidia(client)
        with pytest.raises(LLMIndisponible) as erreur:
            llm.completer("prompt")
        assert CLE_FACTICE not in str(erreur.value)
        assert CLE_FACTICE not in repr(erreur.value)
        assert CLE_FACTICE not in repr(llm)
    finally:
        client.close()


def test_erreur_reseau_sanitaire(monkeypatch):
    configurer_nvidia(monkeypatch)

    def handler(request):
        raise httpx.ConnectError(f"erreur {CLE_FACTICE}", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        llm = LLMNvidia(client)
        with pytest.raises(LLMIndisponible) as erreur:
            llm.completer("prompt")
        assert str(erreur.value) == "nvidia: erreur réseau"
        assert CLE_FACTICE not in repr(llm)
    finally:
        client.close()


def test_json_invalide_sanitaire(monkeypatch):
    configurer_nvidia(monkeypatch)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"not-json", request=request)
    ))
    try:
        with pytest.raises(LLMIndisponible, match="réponse JSON invalide"):
            LLMNvidia(client).completer("prompt")
    finally:
        client.close()


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
        Path("fixtures/contexte_p1.json").read_text(encoding="utf-8")
    )


def lancer_detective(llm, tmp_path):
    return enqueter(contexte_p1(), MemoireJSON(tmp_path / "memoire.json"), llm, AbstentionSeuil())


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
