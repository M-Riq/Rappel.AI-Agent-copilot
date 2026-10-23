from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from scripts import demo_enquete, stabilite_p1
from src.rappel import api
from src.rappel.contrats import ContexteIncident, Hypothese
from src.rappel.enquete import boucle_sql, enqueter
from src.rappel.interfaces.abstention import AbstentionSeuil
from src.rappel.interfaces.llm import LLMFaux
from src.rappel.interfaces.memoire import MemoireJSON
from src.rappel.interfaces.run_sql import RunSqlNonBranche


RACINE = Path(__file__).resolve().parents[1]


def charger(nom):
    return ContexteIncident.model_validate_json((RACINE / "fixtures" / f"contexte_{nom}.json").read_text(encoding="utf-8"))


class AbstentionEspion(AbstentionSeuil):
    def __init__(self):
        super().__init__()
        self.appels = []

    def decider(self, hypothese, nb_sources, incident_similaire):
        self.appels.append((hypothese.confiance, nb_sources, incident_similaire))
        return super().decider(hypothese, nb_sources, incident_similaire)


def executer(contexte, memoire, abstention=None):
    return enqueter(contexte, memoire, LLMFaux(), abstention or AbstentionSeuil(), mode_preuves="sql")


def verifier_abstention(resultat):
    assert resultat.decision_abstention.abstient and resultat.decision_abstention.manque
    assert resultat.correctif is None and resultat.enregistrement is None
    assert any(evenement.type == "abstention" for evenement in resultat.evenements)


def test_seuil_et_formule_inchanges():
    assert AbstentionSeuil().seuil == 0.6
    hypothese = Hypothese(id="h", incident_id="i", texte="t", statut="retenue", preuves=["q1"],
                          confiance=boucle_sql.CONFIANCE_PAR_REQUETE_NON_VIDE, raison_rejet=None)
    # Une requete non vide seule, sans memoire ni seconde table, reste sous le seuil.
    assert AbstentionSeuil().decider(hypothese, nb_sources=1, incident_similaire=False).abstient
    assert not AbstentionSeuil().decider(hypothese, nb_sources=2, incident_similaire=True).abstient


def test_calibration_p1_p2_p3(tmp_path):
    abstention = AbstentionEspion()
    partagee = MemoireJSON(tmp_path / "partagee.json")
    premier = executer(charger("p1"), partagee, abstention)
    second = executer(charger("p2"), partagee, abstention)
    ambigu = executer(charger("p3"), MemoireJSON(tmp_path / "vide.json"), abstention)
    for resultat in (premier, second):
        assert not resultat.decision_abstention.abstient and resultat.correctif is not None
    verifier_abstention(ambigu)
    # nb_sources = tables distinctes a preuve non vide citees par l'hypothese retenue.
    assert abstention.appels == [(0.9, 3, False), (0.45, 2, True)]


@pytest.mark.parametrize("nb_logs", [1, 2, 3])
def test_correctif_resiste_au_bruit(tmp_path, nb_logs):
    donnees = charger("p1").model_dump()
    bruit = [
        {"template": "WARN memory monitor: temporary sampling delay", "compte": 2},
        {"template": "WARN metrics: timeout then retry succeeded", "compte": 3},
        {"template": "WARN disk monitor: delayed statistics", "compte": 1},
    ]
    donnees["logs"].extend(bruit[:nb_logs])
    resultat = executer(donnees, MemoireJSON(tmp_path / "memoire.json"))
    assert not resultat.decision_abstention.abstient and resultat.correctif is not None
    assert "colonne renommée" in resultat.correctif.justification


def test_abstention_resiste_au_log_sans_rapport(tmp_path):
    donnees = charger("p3").model_dump()
    donnees["logs"].append({"template": "INFO audit: retention report generated", "compte": 100})
    verifier_abstention(executer(donnees, MemoireJSON(tmp_path / "memoire.json")))


def test_abstention_avec_un_indice_faible_unique(tmp_path):
    donnees = charger("p3").model_dump()
    donnees["logs"].append({"template": "WARN connection timeout during optional polling", "compte": 6})
    abstention = AbstentionEspion()
    resultat = executer(donnees, MemoireJSON(tmp_path / "memoire.json"), abstention)
    retenue = next(hypothese for hypothese in resultat.hypotheses if hypothese.statut == "retenue")
    assert len(retenue.preuves) == 1 and "[logs]" in retenue.preuves[0]
    assert abstention.appels == [(boucle_sql.CONFIANCE_PAR_REQUETE_NON_VIDE, 1, False)]
    assert "deuxième source indépendante" in resultat.decision_abstention.manque
    verifier_abstention(resultat)


def test_stabilite_mode_sql_triplet_inchange():
    rapport = stabilite_p1.mesurer_stabilite(charger("p1"), LLMFaux())
    assert rapport["verdict"] == "OK" and rapport["identiques"] == 5
    contexte = charger("p1")
    assert rapport["conclusion_majoritaire"] == ["schema", False, [contexte.signature.partition(":")[2]]]
    assert all(mesure["appels_llm"] == 5 for mesure in rapport["mesures"])


def test_demo_colonnes_sql_et_source_simulee(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    resultats = demo_enquete.jouer_demo(LLMFaux())
    demo_enquete.afficher_resultats(resultats)
    sortie = capsys.readouterr().out
    for colonne in ("Tours", "Requetes SQL", "Appels rapide/profonde", "Source preuves"):
        assert colonne in sortie
    assert sortie.count("simule-sqlite (SIMULE)") == 3
    assert "P1 | 21 | 4 | 0 | 5 | 5 | 5 | 3/2 |" in sortie
    assert "Appels LLM : 40.00% de reduction" in sortie


@pytest.fixture
def client(tmp_path):
    memoire = MemoireJSON(tmp_path / "memoire.json")
    application = api.creer_app(memoire, LLMFaux(), AbstentionSeuil(), executeur_enquete=lambda tache: tache())
    with TestClient(application) as connexion:
        yield connexion


def test_api_expose_la_source_des_preuves(client):
    contexte = charger("p1").model_dump()
    client.post("/incidents", json=contexte)
    reponse = client.get(f"/incidents/{contexte['incident_id']}").json()
    assert reponse["mode_preuves"] == "sql"
    assert reponse["source_preuves"] == "simule-sqlite" and reponse["preuves_simulees"] is True
    assert reponse["metriques"]["nb_tours"] == 5 and reponse["metriques"]["nb_requetes_sql"] == 5
    evenements = client.get(f"/incidents/{contexte['incident_id']}/evenements").json()
    preuves = [evenement for evenement in evenements if evenement["type"] == "preuve"]
    assert preuves and all(evenement["requete_sql"] and "[simule-sqlite]" in evenement["texte"] for evenement in preuves)
    assert all(evenement["modele"] == "faux-rapide" for evenement in preuves)


def test_api_mode_outils_toujours_disponible(tmp_path):
    application = api.creer_app(MemoireJSON(tmp_path / "m.json"), LLMFaux(), AbstentionSeuil(),
                                executeur_enquete=lambda tache: tache(), mode_preuves="outils")
    with TestClient(application) as connexion:
        contexte = charger("p1").model_dump()
        connexion.post("/incidents", json=contexte)
        reponse = connexion.get(f"/incidents/{contexte['incident_id']}").json()
    assert reponse["mode_preuves"] == "outils" and reponse["source_preuves"] is None
    assert reponse["preuves_simulees"] is False


def test_api_backend_zetaris_non_branche(tmp_path, monkeypatch):
    monkeypatch.setenv("RUN_SQL_BACKEND", "zetaris")
    with pytest.raises(RunSqlNonBranche, match="non branche"):
        api.creer_app(MemoireJSON(tmp_path / "m.json"), LLMFaux(), AbstentionSeuil())


def test_memoire_hypotheses_fichier_separe_et_vider(tmp_path):
    memoire = MemoireJSON(tmp_path / "memoire.json")
    hypothese = Hypothese(id="inc:hyp-1", incident_id="inc", texte="Cause", statut="refutee", preuves=[],
                          confiance=0.0, raison_rejet="aucune preuve SQL")
    memoire.ecrire_hypotheses("inc", "sig:comp", [hypothese])
    lignes = memoire.lister_hypotheses("sig:comp")
    assert len(lignes) == 1
    assert set(lignes[0]) == {"id", "hypothese_id", "incident_id", "signature", "texte", "statut", "preuves",
                              "confiance", "raison_rejet", "cree_le"}
    assert lignes[0]["raison_rejet"] == "aucune preuve SQL" and memoire.lister() == []
    assert (tmp_path / "memoire_hypotheses.json").is_file()
    memoire.vider()
    assert memoire.lister_hypotheses() == []
