import json
from pathlib import Path

import httpx
import pytest

from src.rappel.contrats import ContexteIncident, EnregistrementMemoire, EvenementEnquete, Hypothese
from src.rappel.enquete import enqueter, graphe
from src.rappel.enquete.war_room import ConfigurationWarRoom, executer_debat
from src.rappel.interfaces.abstention import AbstentionSeuil
from src.rappel.interfaces.llm import LLMAvecSecours, LLMFaux, LLMNvidia
from src.rappel.interfaces.memoire import MemoireJSON


@pytest.fixture(autouse=True)
def isoler_configuration(monkeypatch):
    for nom in ("WAR_ROOM_ACTIVE", "WAR_ROOM_MAX_TOURS", "WAR_ROOM_MAX_APPELS_LLM", "LLM_MAX_APPELS"):
        monkeypatch.delenv(nom, raising=False)


def charger(nom="contexte_p1.json"):
    chemin = Path(__file__).resolve().parents[1] / "fixtures" / nom
    return ContexteIncident.model_validate_json(chemin.read_text(encoding="utf-8"))


def lancer(tmp_path, contexte=None, llm=None, **options):
    return enqueter(
        contexte if contexte is not None else charger(),
        MemoireJSON(tmp_path / "memoire.json"), llm or LLMFaux(), AbstentionSeuil(), **options,
        mode_preuves="outils",
    )


class LLMScripte(LLMFaux):
    def __init__(self, citation="reelle", sceptique_invalide=False, detective_invalide=False):
        super().__init__()
        self.citation = citation
        self.sceptique_invalide = sceptique_invalide
        self.detective_invalide = detective_invalide

    def completer(self, prompt, role="detective"):
        if not prompt.startswith("{"):
            return super().completer(prompt, role)
        donnees = json.loads(prompt.split("\nReponse invalide", 1)[0])
        self._budget.consommer()
        self.usage.nb_appels += 1
        if role == "sceptique":
            if self.sceptique_invalide:
                return "JSON illisible"
            return json.dumps({"objection": "Il reste un doute", "preuve_manquante": "Corroboration"})
        if self.detective_invalide:
            return "JSON illisible"
        preuves = donnees["preuves"][:1] if self.citation == "reelle" else ["logs: preuve INVENTEE"]
        return json.dumps({"reponse": "Je confirme", "preuves_citees": preuves})


def test_moteur_verifie_preuve_inventee():
    class Script:
        def completer(self, prompt, role="detective"):
            if role == "sceptique":
                return json.dumps({"objection": "Indice insuffisant", "preuve_manquante": None})
            return json.dumps({"reponse": "Affirmation", "preuves_citees": ["preuve inventee"]})

    retenue = Hypothese(
        id="hyp", incident_id="inc", texte="Cause candidate", statut="retenue",
        preuves=["logs: preuve reelle"], confiance=0.6, raison_rejet=None,
    )
    etat = {"retenue": retenue, "hypotheses": [retenue], "souvenirs": [],
            "refutees_connues": [], "nb_appels_llm": 0}
    evenements = []
    executer_debat(etat, Script(), ConfigurationWarRoom(True, 1, 2),
                   lambda etat, type, acteur, texte: evenements.append((type, acteur, texte)))
    assert etat["retenue"].confiance == 0.5
    assert etat["nb_objections_sans_reponse"] == 1
    assert etat["nb_appels_llm_war_room"] == 2
    assert any("plafond de tours" in texte for _, _, texte in evenements)


def test_a11_trois_acteurs_et_evenements_valides(tmp_path):
    recus = []
    resultat = lancer(tmp_path, war_room_active=True, sur_evenement=recus.append)
    assert {element.acteur for element in resultat.evenements if element.type == "debat"} == {
        "historien", "sceptique", "detective"
    }
    assert resultat.metriques.nb_tours_war_room == 2
    assert resultat.metriques.nb_appels_llm_war_room == 3
    assert resultat.metriques.nb_objections == 1
    assert resultat.metriques.nb_objections_sans_reponse == 0
    assert recus == resultat.evenements
    for indice, element in enumerate(resultat.evenements, start=1):
        assert element.etape == indice
        assert EvenementEnquete.model_validate(element.model_dump()) == element


@pytest.mark.parametrize("maximum", [0, 1, 2])
def test_a11_plafond_appels(tmp_path, maximum):
    resultat = lancer(tmp_path, llm=LLMScripte(), war_room_active=True,
                      war_room_max_appels_llm=maximum)
    assert resultat.metriques.nb_appels_llm_war_room <= maximum
    assert resultat.correctif is not None
    assert any(element.type == "decision" and "plafond" in element.texte
               for element in resultat.evenements)


def test_a12_identique_au_graphe_sans_noeud(tmp_path, monkeypatch):
    monkeypatch.setattr(graphe, "maintenant", lambda: "2026-10-06T10:00:00+00:00")
    sans_debat = lancer(tmp_path / "desactive", war_room_active=False)
    ajouter_noeud = graphe.StateGraph.add_node
    ajouter_arete = graphe.StateGraph.add_edge

    def sans_noeud(self, nom, *args, **kwargs):
        if nom == "war_room":
            return self
        return ajouter_noeud(self, nom, *args, **kwargs)

    def sans_arete(self, depart, arrivee):
        if depart == "war_room":
            return self
        return ajouter_arete(self, depart, "evaluer_confiance" if arrivee == "war_room" else arrivee)

    monkeypatch.setattr(graphe.StateGraph, "add_node", sans_noeud)
    monkeypatch.setattr(graphe.StateGraph, "add_edge", sans_arete)
    reference = lancer(tmp_path / "reference", war_room_active=False)
    assert sans_debat.hypotheses == reference.hypotheses
    assert sans_debat.correctif == reference.correctif
    assert sans_debat.decision_abstention == reference.decision_abstention
    assert sans_debat.evenements == reference.evenements
    assert not any(element.type == "debat" for element in sans_debat.evenements)
    assert sans_debat.metriques.nb_appels_llm == reference.metriques.nb_appels_llm == 1


@pytest.mark.parametrize(("citation", "penalite"), [("inventee", 0.1), ("reelle", 0.0)])
def test_verification_citations_par_code(tmp_path, citation, penalite):
    resultat = lancer(tmp_path, llm=LLMScripte(citation), war_room_active=True, war_room_max_tours=1)
    retenue = next(hypothese for hypothese in resultat.hypotheses if hypothese.statut == "retenue")
    assert retenue.confiance == pytest.approx(0.95 - penalite)
    assert resultat.metriques.nb_objections_sans_reponse == (1 if penalite else 0)
    assert all("INVENTEE" not in preuve for preuve in retenue.preuves)


def test_objections_font_basculer_abstention(tmp_path):
    contexte = ContexteIncident(
        incident_id="inc-limite", horodatage="2026-10-06T10:00:00Z", signature="ecart:composant",
        schema={"attendu": ["valeur:int"], "observe": ["valeur:str"]},
        logs=[{"template": "ERROR column type mismatch", "compte": 20}],
        commits_recents=[], metriques={},
    )
    avant = lancer(tmp_path / "avant", contexte=contexte, war_room_active=False)
    apres = lancer(tmp_path / "apres", contexte=contexte, llm=LLMScripte("inventee"),
                   war_room_active=True)
    assert avant.correctif is not None and not avant.decision_abstention.abstient
    assert apres.metriques.nb_objections_sans_reponse == 2
    assert apres.decision_abstention.confiance == pytest.approx(0.5)
    assert apres.decision_abstention.abstient and apres.correctif is None


@pytest.mark.parametrize("role", ["sceptique", "detective"])
def test_json_invalide_arret_sans_penalite(tmp_path, role):
    llm = LLMScripte(sceptique_invalide=role == "sceptique", detective_invalide=role == "detective")
    resultat = lancer(tmp_path, llm=llm, war_room_active=True)
    retenue = next(hypothese for hypothese in resultat.hypotheses if hypothese.statut == "retenue")
    assert retenue.confiance == 0.95
    assert resultat.metriques.nb_objections_sans_reponse == 0
    assert resultat.metriques.nb_appels_llm_war_room == (2 if role == "sceptique" else 3)
    assert any(element.type == "decision" and f"JSON invalide du {role} apres une relance" in element.texte
               for element in resultat.evenements)


def test_decisions_faux_sur_trois_contextes(tmp_path):
    memoire = MemoireJSON(tmp_path / "partagee.json")
    llm = LLMFaux()
    for nom in ("contexte_p1.json", "contexte_p2.json"):
        resultat = enqueter(charger(nom), memoire, llm, AbstentionSeuil(), war_room_active=True, mode_preuves="outils")
        assert resultat.correctif is not None and not resultat.decision_abstention.abstient
        assert resultat.metriques.nb_tours_war_room == 2
        assert resultat.metriques.nb_objections_sans_reponse == 0
    ambigu = lancer(tmp_path / "vide", contexte=charger("contexte_p3.json"), war_room_active=True)
    assert ambigu.correctif is None and ambigu.decision_abstention.abstient
    assert ambigu.metriques.nb_appels_llm_war_room == 0
    assert not any(element.type == "debat" for element in ambigu.evenements)


def test_configuration_env_et_surcharge(tmp_path, monkeypatch):
    monkeypatch.setenv("WAR_ROOM_ACTIVE", "1")
    monkeypatch.setenv("WAR_ROOM_MAX_TOURS", "1")
    monkeypatch.setenv("WAR_ROOM_MAX_APPELS_LLM", "2")
    resultat = lancer(tmp_path / "env")
    assert resultat.metriques.nb_tours_war_room == 1
    assert resultat.metriques.nb_appels_llm_war_room == 2
    assert any("plafond de tours" in element.texte for element in resultat.evenements)
    desactive = lancer(tmp_path / "override", war_room_active=False,
                       war_room_max_tours=2, war_room_max_appels_llm=6)
    assert desactive.metriques.nb_tours_war_room == 0
    assert not any(element.type == "debat" for element in desactive.evenements)


def test_budget_global_arrete_debat(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MAX_APPELS", "2")
    resultat = lancer(tmp_path, war_room_active=True)
    assert resultat.metriques.nb_appels_llm == 2
    assert resultat.metriques.nb_appels_llm_war_room == 1
    assert resultat.metriques.nb_objections_sans_reponse == 0
    assert any("plafond d'appels global" in element.texte for element in resultat.evenements)


def test_plafond_physique_avec_secours(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "cle-factice")
    monkeypatch.setenv("NVIDIA_MODEL", "modele-factice")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://fournisseur.test/v1")
    candidats = LLMFaux().completer("")

    def handler(request):
        prompt = json.loads(request.content)["messages"][-1]["content"]
        if prompt.startswith("{"):
            return httpx.Response(500)
        return httpx.Response(200, json={"choices": [{"message": {"content": candidats}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        llm = LLMAvecSecours(LLMNvidia(client), LLMFaux())
        resultat = lancer(tmp_path, llm=llm, war_room_active=True, war_room_max_appels_llm=1)
    assert resultat.metriques.nb_appels_llm_war_room == 1
    assert resultat.metriques.nb_appels_llm == 2
    assert resultat.metriques.nb_objections_sans_reponse == 0
    assert any("plafond" in element.texte for element in resultat.evenements)


def test_historien_utilise_memoire_sans_appel_llm(tmp_path):
    contexte = charger()
    memoire = MemoireJSON(tmp_path / "memoire.json")
    souvenir = EnregistrementMemoire(
        signature=contexte.signature, cause_confirmee="Cause passee",
        hypotheses_refutees=["Autre hypothese deja refutee"],
        correctif_essaye="Correction passee", resultat="echoue", date="2026-10-01",
    )
    memoire.ecrire(souvenir)
    resultat = enqueter(contexte, memoire, LLMFaux(), AbstentionSeuil(), war_room_active=True, mode_preuves="outils")
    historien = next(element for element in resultat.evenements
                     if element.type == "debat" and element.acteur == "historien")
    for contenu in (souvenir.correctif_essaye, souvenir.date, souvenir.resultat,
                    souvenir.hypotheses_refutees[0]):
        assert contenu in historien.texte
    assert resultat.metriques.nb_appels_llm == 4
    assert resultat.metriques.nb_appels_llm_war_room == 3


def test_citation_proche_mais_pas_exacte_ne_suffit_pas(tmp_path):
    class CitationProche(LLMScripte):
        def completer(self, prompt, role="detective"):
            reponse = super().completer(prompt, role)
            if role == "detective" and prompt.startswith("{"):
                donnees = json.loads(reponse)
                donnees["preuves_citees"] = [preuve + " " for preuve in donnees["preuves_citees"]]
                return json.dumps(donnees)
            return reponse
    resultat = lancer(tmp_path, llm=CitationProche(), war_room_active=True, war_room_max_tours=1)
    assert resultat.metriques.nb_objections_sans_reponse == 1


def test_zero_tour_arret_sans_penalite(tmp_path):
    resultat = lancer(tmp_path, war_room_active=True, war_room_max_tours=0)
    assert resultat.metriques.nb_tours_war_room == 0
    assert resultat.metriques.nb_appels_llm_war_room == 0
    assert resultat.metriques.nb_objections_sans_reponse == 0
    assert any("plafond de tours" in element.texte for element in resultat.evenements)


def test_plancher_confiance(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MAX_APPELS", "30")
    resultat = lancer(tmp_path, llm=LLMScripte("inventee"), war_room_active=True,
                      war_room_max_tours=12, war_room_max_appels_llm=24)
    retenue = next(hypothese for hypothese in resultat.hypotheses if hypothese.statut == "retenue")
    assert retenue.confiance == 0.0
    assert resultat.metriques.nb_appels_llm == 25
    assert resultat.metriques.nb_objections_sans_reponse == 12