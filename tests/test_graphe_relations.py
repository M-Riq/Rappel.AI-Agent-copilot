"""Graphe de relations des incidents (déterministe, sans LLM)."""

import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from rappel import api
from rappel.enquete.adaptateurs import MemoireEnquete
from rappel.interfaces import memoire as memoire_squelette
from rappel.memoire.graphe_relations import construire_graphe, depuis_memoire, en_mermaid, voisinage
from rappel.orchestration import boucle

RACINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rappel_ui_logique_graphe", RACINE / "ui" / "logique.py")
logique = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logique)
JETON = "jeton-" + "factice-graphe-77"


def aretes(graphe, type_arete):
    return [(arete["source"], arete["cible"]) for arete in graphe["aretes"] if arete["type"] == type_arete]


@pytest.fixture
def parcours(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "m.json")
    cycles = boucle.executer_sequence(["p1", "p2", "p3"], memoire=memoire)
    identifiants = [cycle.resume["incident_id"] for cycle in cycles]
    return memoire, identifiants


def test_p1_p2_meme_signature_et_refutation_heritee(parcours):
    memoire, (p1, p2, p3) = parcours
    graphe = depuis_memoire(memoire)
    assert (f"incident:{p1}", f"incident:{p2}") in aretes(graphe, "meme_signature")
    refutees_p1 = {source for source, cible in aretes(graphe, "refutee_dans") if cible == f"incident:{p1}"}
    refutees_p2 = {source for source, cible in aretes(graphe, "refutee_pour") if cible == f"incident:{p2}"}
    assert refutees_p1 and refutees_p1 <= refutees_p2
    assert all(source.startswith("hypothese_refutee:") for source in refutees_p2)


def test_p3_sans_lien_de_cause(parcours):
    memoire, (p1, p2, p3) = parcours
    graphe = depuis_memoire(memoire)
    cibles_causes = {cible for _, cible in aretes(graphe, "cause_confirmee_de")}
    assert f"incident:{p3}" not in cibles_causes
    assert not any(f"incident:{p3}" in paire for paire in aretes(graphe, "meme_signature"))
    assert any(noeud["id"] == f"incident:{p3}" for noeud in graphe["noeuds"])


def test_resultats_et_determinisme(parcours):
    memoire, _ = parcours
    graphe = depuis_memoire(memoire)
    assert depuis_memoire(memoire) == graphe
    assert aretes(graphe, "resultat_non_confirme") and not aretes(graphe, "resultat_reussi")
    types = {noeud["type"] for noeud in graphe["noeuds"]}
    assert types == {"incident", "signature", "hypothese_retenue", "hypothese_refutee", "cause", "correctif"}


def test_graphe_vide_propre():
    graphe = construire_graphe([])
    assert graphe == {"noeuds": [], "aretes": []}
    assert "aucune relation" in en_mermaid(graphe)
    assert "aucune relation" in logique.dot_relations(graphe)
    assert logique.tableau_relations(graphe) == []


def test_lien_incident_et_resultat_confirme():
    souvenir = {"signature": "s:c", "cause_confirmee": "cause A", "hypotheses_refutees": ["h1"],
                "correctif_essaye": "corriger A", "resultat": "reussi", "date": "2026-10-24"}
    hypotheses = [{"incident_id": "i1", "signature": "s:c", "texte": "h1", "statut": "refutee"},
                  {"incident_id": "i1", "signature": "s:c", "texte": "h2", "statut": "retenue"}]
    graphe = construire_graphe([souvenir], hypotheses, [{"incident_id": "i1", "signature": "s:c", "enregistrement": souvenir}],
                               ["reussi"])
    assert [cible for _, cible in aretes(graphe, "cause_confirmee_de")] == ["incident:i1"]
    assert len(aretes(graphe, "resultat_reussi")) == 1
    sans_incident = construire_graphe([souvenir])
    assert [cible for _, cible in aretes(sans_incident, "cause_confirmee_de")] == ["signature:s:c"]
    sous = voisinage(graphe, "incident:i1", 1)
    assert {noeud["id"] for noeud in sous["noeuds"]} >= {"incident:i1", "signature:s:c"}


def test_mermaid_et_dot_echappes_sans_secret(monkeypatch):
    monkeypatch.setenv("ZETARIS_API_KEY", JETON)
    souvenir = {"signature": 's:"x"', "cause_confirmee": f'cause [a] {{b}} "c" {JETON}', "hypotheses_refutees": [],
                "correctif_essaye": "corriger | tout", "resultat": "echoue", "date": "2026-10-24"}
    graphe = construire_graphe([souvenir])
    texte = en_mermaid(graphe)
    assert JETON not in json.dumps(graphe) and JETON not in texte
    assert texte.startswith("graph LR") and "resultat_echoue" in texte
    corps = [ligne for ligne in texte.splitlines()[1:] if "-->" not in ligne]
    assert all(ligne.count('"') == 2 for ligne in corps)
    dot = logique.dot_relations(graphe)
    assert dot.startswith("digraph") and "résultat échoué" in dot
    assert {ligne["relation"] for ligne in logique.tableau_relations(graphe)} >= {"cause confirmée de", "résultat échoué"}


def test_api_graphe_et_voisinage(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "m.json")
    application = api.creer_app(memoire, executeur_enquete=lambda tache: tache())
    contexte = json.loads((RACINE / "fixtures" / "contexte_p1.json").read_text(encoding="utf-8"))
    with TestClient(application) as client:
        vide = client.get("/graphe").json()
        assert vide["noeuds"] == [] and "aucune relation" in vide["mermaid"]
        client.post("/incidents", json=contexte)
        client.post("/incidents", json={**contexte, "incident_id": "p1-bis"})
        graphe = client.get("/graphe").json()
        assert graphe["memoire_mode"] == "json"
        assert ("incident:p1", "incident:p1-bis") in aretes(graphe, "meme_signature")
        assert any(cible == "incident:p1" for _, cible in aretes(graphe, "cause_confirmee_de"))
        local = client.get("/graphe/p1").json()
        assert local["incident"] == "p1" and any(n["id"] == "incident:p1" for n in local["noeuds"])
        assert client.get("/graphe/inconnu").status_code == 404


class PostgresCoupe:
    def __getattr__(self, nom):
        def echouer(*arguments):
            raise RuntimeError(memoire_squelette.MESSAGE_CONNEXION_POSTGRES)
        return echouer


def test_graphe_avec_secours_automatique(tmp_path):
    secours = memoire_squelette.MemoireJSON(tmp_path / "secours.json")
    memoire = MemoireEnquete(memoire_squelette.MemoireAvecSecours(PostgresCoupe(), secours))
    boucle.executer_sequence(["p1", "p2"], memoire=memoire)
    graphe = depuis_memoire(memoire)
    assert aretes(graphe, "meme_signature") and aretes(graphe, "refutee_pour")
    assert memoire.backend.mode == "fallback-json"



class _FauxSt:
    """Remplaçant minimal de Streamlit ; sans graphviz_chart si avec_graphviz est faux."""

    def __init__(self, avec_graphviz):
        self.session_state = {}
        self.appels = []
        self.avec_graphviz = avec_graphviz

    def fragment(self, run_every=None):
        return lambda fonction: fonction

    def __getattr__(self, nom):
        if nom == "graphviz_chart" and not self.avec_graphviz:
            raise AttributeError(nom)
        return lambda *args, **kwargs: self.appels.append((nom, args, kwargs))


@pytest.mark.parametrize("avec_graphviz", [True, False])
def test_zone_relations(monkeypatch, tmp_path, avec_graphviz):
    import sys

    faux = _FauxSt(avec_graphviz)
    monkeypatch.setitem(sys.modules, "streamlit", faux)
    monkeypatch.syspath_prepend(str(RACINE / "ui"))
    for nom in ("zones", "logique", "client_api"):
        monkeypatch.delitem(sys.modules, nom, raising=False)
    import zones

    memoire = MemoireEnquete.json(tmp_path / "m.json")
    boucle.executer_sequence(["p1", "p2"], memoire=memoire)
    application = api.creer_app(memoire, executeur_enquete=lambda tache: tache())
    with TestClient(application) as session:
        zones.zone_relations(zones.ClientApi("http://testserver", session=session))
    noms = [nom for nom, _, _ in faux.appels]
    assert "table" in noms
    if avec_graphviz:
        assert "graphviz_chart" in noms
    else:
        assert any(nom == "code" and kwargs.get("language") == "mermaid" for nom, _, kwargs in faux.appels)
