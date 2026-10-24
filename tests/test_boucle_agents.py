"""Boucle des 4 agents : Ingestion -> Mémoire -> Enquête -> Action -> retour en mémoire."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from rappel import api
from rappel.contrats import valider
from rappel.enquete.adaptateurs import LLMFaux, MemoireEnquete
from rappel.orchestration import boucle
from rappel.pipeline_demo import empreinte_dossier, injecter, lire_etat, reset
from scripts import boucle_agents

JETON = "jeton-" + "factice-boucle-42"


@pytest.fixture
def memoire(tmp_path):
    return MemoireEnquete.json(tmp_path / "memoire_boucle.json")


@pytest.fixture
def action_reelle(monkeypatch):
    monkeypatch.setenv("ACTION_REELLE_DEMO", "1")


def test_p1_correctif_applique_vert_et_memoire_ecrite(memoire, action_reelle):
    cycle = boucle.executer_boucle("p1", memoire=memoire)
    resume = cycle.resume
    assert resume["statut"] == "termine" and resume["simulation"] is True
    assert "colonne renommée" in resume["enquete"]["conclusion"]
    assert resume["action"]["statut"] == "execute" and resume["action"]["pipeline"] == "vert"
    assert lire_etat()["statut"] == "vert"
    souvenirs = memoire.chercher(resume["signature"])
    assert souvenirs and souvenirs[-1].resultat == "reussi" and souvenirs[-1].hypotheses_refutees
    assert resume["memoire"] == {"souvenirs_avant": 0, "refutees_connues": 0, "ecrit": True, "resultat": "reussi"}
    assert [ligne["agent"] for ligne in resume["agents"]] == ["ingestion", "memoire", "enquete", "memoire", "action", "retour"]
    for evenement in cycle.resultat.evenements:
        valider("evenement_enquete", evenement.model_dump(mode="json", exclude_none=True))


def test_sequence_p2_plus_courte_et_p3_abstention(memoire, action_reelle):
    p1, p2, p3 = boucle.executer_sequence(["p1", "p2", "p3"], memoire=memoire)
    e1, e2 = p1.resume["enquete"], p2.resume["enquete"]
    assert e2["nb_evenements"] <= 0.6 * e1["nb_evenements"] and e2["nb_appels_llm"] < e1["nb_appels_llm"]
    assert p2.resume["memoire"]["souvenirs_avant"] >= 1 and p2.resume["memoire"]["refutees_connues"] >= 1
    assert p3.resume["enquete"]["abstention"] and p3.resume["enquete"]["conclusion"] is None
    assert p3.resume["enquete"]["manque"]
    assert p3.resume["action"]["statut"] == "aucune" and p3.resume["action"]["pipeline_inchange"]
    assert p3.resume["memoire"]["ecrit"] is False


def test_p3_empreinte_inchangee_meme_avec_action_reelle(memoire, action_reelle):
    injecter("p3")
    avant = empreinte_dossier()
    cycle = boucle.executer_boucle(None, memoire=memoire)
    assert cycle.resume["enquete"]["abstention"]
    assert empreinte_dossier() == avant


def test_sans_action_reelle_proposition_seule(memoire):
    cycle = boucle.executer_boucle("p1", memoire=memoire)
    assert cycle.resume["action"]["statut"] == "proposition"
    assert cycle.resume["action"]["pipeline_inchange"] and lire_etat()["statut"] == "rouge"
    assert cycle.resume["memoire"]["resultat"] == "non_confirme"


def test_pipeline_vert_aucune_enquete(memoire):
    reset()
    cycle = boucle.executer_boucle(None, memoire=memoire)
    assert cycle.resume["statut"] == "aucune_anomalie" and cycle.resultat is None
    assert boucle.lire_dernier_cycle()["statut"] == "termine"


def test_journal_sans_secret(memoire, monkeypatch, action_reelle):
    monkeypatch.setenv("ZETARIS_API_KEY", JETON)
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", JETON + "-2")
    boucle.executer_sequence(["p1", "p3"], memoire=memoire)
    contenu = boucle.chemin_journal().read_text(encoding="utf-8")
    assert JETON not in contenu
    lignes = [json.loads(ligne) for ligne in contenu.splitlines()]
    assert lignes and all(set(ligne) == {"horodatage", "cycle", "agent", "etape", "statut", "duree_ms", "mode", "detail"}
                          for ligne in lignes)
    assert {ligne["mode"] for ligne in lignes} <= {"simulé", "réel"}
    assert all(ligne["agent"] in boucle.AGENTS for ligne in lignes)
    assert boucle.assainir(f"x {JETON} y") == "x [MASQUE] y"


def test_surveillance_plafond_de_cycles():
    lancements, lectures = [], []
    etats = iter([{"statut": "vert", "horodatage": "t0"}, {"statut": "rouge", "horodatage": "t1"},
                  {"statut": "rouge", "horodatage": "t1"}, {"statut": "rouge", "horodatage": "t2"},
                  {"statut": "rouge", "horodatage": "t3"}, {"statut": "rouge", "horodatage": "t4"}])

    def lire(dossier=None):
        lectures.append(1)
        return next(etats)

    import rappel.pipeline_demo as module
    original = module.lire_etat
    module.lire_etat = lire
    try:
        cycles = boucle.surveiller(intervalle_s=0, max_cycles=2, duree_max_s=60, dormir=lambda s: None,
                                   lancer=lambda scenario, **options: lancements.append(scenario) or boucle.CycleBoucle({}))
    finally:
        module.lire_etat = original
    assert len(cycles) == 2 and lancements == [None, None]
    assert len(lectures) == 4  # le même état rouge (t1) n'est traité qu'une fois


def test_surveillance_arret_propre_par_duree():
    temps = iter(range(100))
    cycles = boucle.surveiller(intervalle_s=1, max_cycles=5, duree_max_s=3, dormir=lambda s: None,
                               horloge=lambda: next(temps), lancer=lambda *a, **o: pytest.fail("aucun rouge"))
    assert cycles == []
    with pytest.raises(ValueError):
        boucle.surveiller(max_cycles=0)


def test_route_boucle_etat(memoire, action_reelle):
    application = api.creer_app(memoire, executeur_enquete=lambda tache: tache())
    with TestClient(application) as client:
        assert client.get("/boucle/etat").json() == {"statut": "aucun_cycle", "cycle": None, "agents": []}
        boucle.executer_boucle("p1", memoire=memoire)
        etat = client.get("/boucle/etat").json()
        assert etat["statut"] == "termine" and etat["simulation"] is True
        assert [agent["agent"] for agent in etat["agents"]][-2:] == ["action", "retour"]
        assert etat["agents"][-1]["statut"] == "reussi"
        assert "detail" not in etat["agents"][0]


def test_script_sequence(memoire, action_reelle, tmp_path, capsys):
    sortie = tmp_path / "boucle.json"
    assert boucle_agents.main(["--scenario", "p1,p2,p3", "--json", str(sortie)], memoire=memoire) == 0
    texte = capsys.readouterr().out
    assert "SIMULATION" in texte and texte.count("| termine |") == 3
    resumes = json.loads(sortie.read_text(encoding="utf-8"))["cycles"]
    assert [resume["scenario"] for resume in resumes] == ["p1", "p2", "p3"]


def test_script_refuse_reel_sans_confirmer(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "meterless")
    assert boucle_agents.main(["--scenario", "p1", "--json", str(tmp_path / "b.json")]) == 2
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("SOURCE_PROVIDER", "zetaris")
    assert boucle_agents.main(["--scenario", "p1", "--json", str(tmp_path / "b.json")]) == 2
    assert not (tmp_path / "b.json").exists()


def test_script_scenario_invalide():
    with pytest.raises(SystemExit):
        boucle_agents.main(["--scenario", "p9"])


def test_war_room_dans_la_boucle(memoire, monkeypatch):
    monkeypatch.setenv("WAR_ROOM_ACTIVE", "1")
    cycle = boucle.executer_boucle("p1", memoire=memoire, fabrique_llm=LLMFaux)
    assert cycle.resume["enquete"]["war_room"]["tours"] >= 1
    assert any(evenement.type == "debat" for evenement in cycle.resultat.evenements)
