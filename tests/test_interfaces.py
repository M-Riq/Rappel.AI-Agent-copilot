import json

import pytest

from src.rappel.contrats import EnregistrementMemoire, Hypothese
from src.rappel.interfaces.abstention import AbstentionSeuil
from src.rappel.interfaces.llm import LLMClaude, LLMFaux, LLMNvidia, creer_llm
from src.rappel.interfaces.memoire import MemoireJSON


@pytest.fixture
def enregistrement():
    return EnregistrementMemoire(
        signature="schema_mismatch:orders_etl",
        cause_confirmee="Renommage de amount en total",
        hypotheses_refutees=["Panne reseau", "Manque de memoire"],
        correctif_essaye="Restaurer amount",
        resultat="reussi",
        date="2026-10-15",
    )


def test_memoire_ecrire_puis_chercher(tmp_path, enregistrement):
    chemin = tmp_path / "data" / "memoire.json"
    memoire = MemoireJSON(chemin)
    assert json.loads(chemin.read_text(encoding="utf-8")) == []
    memoire.ecrire(enregistrement)
    assert MemoireJSON(chemin).chercher(enregistrement.signature) == [enregistrement]


def test_memoire_signature_inconnue(tmp_path, enregistrement):
    memoire = MemoireJSON(tmp_path / "memoire.json")
    memoire.ecrire(enregistrement)
    assert memoire.chercher("signature_inconnue") == []


def test_memoire_hypotheses_refutees_sans_doublon(tmp_path, enregistrement):
    memoire = MemoireJSON(tmp_path / "memoire.json")
    memoire.ecrire(enregistrement)
    autre = enregistrement.model_copy(
        update={"hypotheses_refutees": ["Panne reseau", "Timeout"]}
    )
    memoire.ecrire(autre)
    memoire.ecrire(
        enregistrement.model_copy(
            update={"signature": "autre", "hypotheses_refutees": ["Autre cause"]}
        )
    )
    assert memoire.chercher(enregistrement.signature) == [enregistrement, autre]
    assert memoire.hypotheses_refutees(enregistrement.signature) == [
        "Panne reseau", "Manque de memoire", "Timeout"
    ]
    assert memoire.hypotheses_refutees("signature_inconnue") == []


def test_memoire_chemin_par_defaut(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    memoire = MemoireJSON()
    assert memoire.chemin.is_file()
    assert memoire.chercher("signature_inconnue") == []


def test_memoire_mettre_a_jour_dernier_resultat(tmp_path, enregistrement):
    memoire = MemoireJSON(tmp_path / "memoire.json")
    memoire.ecrire(enregistrement)
    memoire.ecrire(enregistrement.model_copy(update={"date": "2026-10-16"}))
    assert memoire.mettre_a_jour_resultat(enregistrement.signature, "echoue")
    assert [element.resultat for element in memoire.chercher(enregistrement.signature)] == [
        "reussi", "echoue"
    ]
    assert not memoire.mettre_a_jour_resultat("inconnue", "echoue")
    with pytest.raises(ValueError):
        memoire.mettre_a_jour_resultat(enregistrement.signature, "invalide")


@pytest.mark.parametrize("role", ["detective", "sceptique", "historien"])
def test_llm_faux_reponse_json(role):
    llm = LLMFaux()
    reponse = llm.completer("Examiner cet incident", role)
    if role == "detective":
        candidats = json.loads(reponse)
        assert isinstance(candidats, list) and len(candidats) >= 3
        assert {candidat["categorie"] for candidat in candidats} == {
            "schema", "ressources", "reseau", "configuration"
        }
        assert all(set(candidat) == {"texte", "categorie"} for candidat in candidats)
    else:
        assert isinstance(json.loads(reponse), dict)
        assert Hypothese.model_validate_json(reponse).id == f"hyp-faux-{role}"
    assert llm.completer("Autre prompt", role) == reponse


def test_llm_faux_role_par_defaut():
    assert LLMFaux().completer("Incident") == LLMFaux().completer("Incident", "detective")


def test_creer_llm_par_defaut(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(creer_llm(), LLMFaux)


@pytest.mark.parametrize(
    ("fournisseur", "cle_env", "modele_env", "classe"),
    [("nvidia", "NVIDIA_API_KEY", "NVIDIA_MODEL", LLMNvidia),
     ("claude", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", LLMClaude)],
)
def test_creer_llm_fournisseur(tmp_path, monkeypatch, fournisseur, cle_env, modele_env, classe):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", fournisseur)
    monkeypatch.setenv(cle_env, "cle-factice")
    monkeypatch.setenv(modele_env, "modele-factice")
    assert isinstance(creer_llm(), classe)


def test_creer_llm_lit_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "faux")
    monkeypatch.delenv("LLM_PROVIDER")
    (tmp_path / ".env").write_text("LLM_PROVIDER=nvidia\n", encoding="utf-8")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    with pytest.raises(ValueError, match="NVIDIA_API_KEY manquante"):
        creer_llm()


def test_creer_llm_preserve_environnement(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "faux")
    (tmp_path / ".env").write_text("LLM_PROVIDER=nvidia\n", encoding="utf-8")
    assert isinstance(creer_llm(), LLMFaux)


@pytest.fixture
def hypothese():
    return Hypothese(
        id="hyp-0001",
        incident_id="inc-0001",
        texte="Le renommage de colonne explique les erreurs",
        statut="ouverte",
        preuves=["Commit a1b2c3d"],
        confiance=0.8,
        raison_rejet=None,
    )


def test_abstention_hypothese_sure(hypothese):
    decision = AbstentionSeuil().decider(hypothese, nb_sources=2, incident_similaire=True)
    assert not decision.abstient
    assert decision.confiance == pytest.approx(1.0)
    assert decision.manque == []


def test_abstention_confiance_faible(hypothese):
    hypothese.confiance = 0.3
    decision = AbstentionSeuil().decider(hypothese, nb_sources=2, incident_similaire=True)
    assert decision.abstient
    assert decision.confiance == pytest.approx(0.5)
    assert "confiance sous le seuil" in decision.manque


def test_abstention_sans_preuve(hypothese):
    hypothese.preuves = []
    decision = AbstentionSeuil().decider(hypothese, nb_sources=2, incident_similaire=True)
    assert decision.abstient
    assert decision.confiance == pytest.approx(1.0)
    assert decision.manque == ["preuve"]


@pytest.mark.parametrize(
    ("nb_sources", "incident_similaire", "confiance"),
    [(1, False, 0.8), (2, False, 0.9), (1, True, 0.9), (3, True, 1.0)],
)
def test_abstention_bonus(hypothese, nb_sources, incident_similaire, confiance):
    decision = AbstentionSeuil().decider(hypothese, nb_sources, incident_similaire)
    assert decision.confiance == pytest.approx(confiance)
    assert not decision.abstient


def test_abstention_plafond_confiance(hypothese):
    hypothese.confiance = 0.95
    decision = AbstentionSeuil().decider(hypothese, nb_sources=2, incident_similaire=True)
    assert decision.confiance == 1.0


def test_abstention_egalite_seuil(hypothese):
    hypothese.confiance = 0.6
    decision = AbstentionSeuil().decider(hypothese, nb_sources=1, incident_similaire=False)
    assert not decision.abstient
    assert decision.manque == [
        "deuxième source indépendante", "incident similaire en mémoire"
    ]


def test_abstention_seuil_personnalise(hypothese):
    decision = AbstentionSeuil(seuil=0.9).decider(
        hypothese, nb_sources=1, incident_similaire=False
    )
    assert decision.abstient