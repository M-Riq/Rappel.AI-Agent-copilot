import sys
from types import SimpleNamespace

import pytest

from rappel.contrats import valider
from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import LLMFaux, MemoireEnquete
from rappel.ingestion.contexte_demo import construire_contexte
from rappel.ingestion.ecarts_schema import detecter_ecarts, moteur_detection
from rappel.ingestion.reducteur_logs import normaliser, reduire_logs
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.pipeline_demo import injecter, reset


def test_normalisation():
    ligne = "2026-10-23T10:00:00.040000Z ERROR orders_etl: KeyError: 'amount' while transforming row 17"
    assert normaliser(ligne) == "ERROR orders_etl: KeyError: 'amount' while transforming row <*>"
    assert normaliser("INFO job 3fa85f64-5717-4562-b3fc-2c963f66afa6 took 12.5 ms") == "INFO job <*> took <*> ms"


def test_p1_contexte_valide_et_reduit():
    injecter("p1")
    contexte, infos = construire_contexte()
    valider("contexte_incident", contexte)
    assert contexte["signature"] == "schema_mismatch:orders_etl"
    assert len(contexte["logs"]) < 10 and infos["nb_lignes_logs"] > 200
    assert contexte["logs"][0]["template"].startswith("ALERT")
    erreur = next(modele for modele in contexte["logs"] if "KeyError: 'amount'" in modele["template"])
    assert erreur["compte"] == 214 and contexte["logs"][1] == erreur
    assert contexte["metriques"] == {"lignes_rejetees": 214, "latence_ms": 120}
    assert contexte["commits_recents"][-1]["hash"] == "a1b2c3"
    assert {(ecart["ecart"], ecart["colonne"]) for ecart in infos["ecarts_schema"]} == {
        ("manquante", "amount"), ("nouvelle", "total")}
    assert infos["reducteur_logs"] == "maison" and infos["simulation"] is True
    assert infos["detecteur_schema"] == "python" and "great_expectations" in infos["message_detecteur"]


def test_p2_detecte_et_p3_sans_ecart():
    injecter("p2")
    contexte, infos = construire_contexte()
    assert infos["ecarts_schema"] == [{"ecart": "type", "colonne": "quantity", "attendu": "integer", "observe": "text"}]
    assert contexte["signature"] == "schema_mismatch:orders_etl"
    injecter("p3")
    contexte, infos = construire_contexte()
    assert infos["ecarts_schema"] == [] and contexte["schema"]["attendu"] == contexte["schema"]["observe"]
    assert contexte["signature"] == "timeout:orders_etl"
    assert contexte["metriques"]["latence_ms"] == 30000
    valider("contexte_incident", contexte)


def test_etat_sain_aucune_anomalie():
    reset()
    contexte, infos = construire_contexte(incident_id="sain")
    assert infos["ecarts_schema"] == [] and contexte["signature"].startswith("aucune_anomalie:")
    assert contexte["incident_id"] == "sain"


def test_unicite_seulement_dans_chaque_liste():
    # Une meme colonne dans attendu et observe n'est pas un doublon.
    assert detecter_ecarts(["id:int", "a:int"], ["id:int", "a:int"]) == []
    assert detecter_ecarts(["id:int", "id:text"], ["id:int"]) == []


def test_drain3_utilise_s_il_est_present(monkeypatch):
    class Grappe:
        def __init__(self, modele):
            self.modele, self.size = modele, 0

        def get_template(self):
            return self.modele

    class Mineur:
        def __init__(self):
            self.drain = SimpleNamespace(clusters=[])

        def add_log_message(self, ligne):
            modele = normaliser(ligne)
            grappe = next((element for element in self.drain.clusters if element.modele == modele), None)
            if grappe is None:
                grappe = Grappe(modele)
                self.drain.clusters.append(grappe)
            grappe.size += 1
            return {"template_mined": modele}

    monkeypatch.setitem(sys.modules, "drain3", SimpleNamespace(TemplateMiner=Mineur))
    modeles, reducteur = reduire_logs(["ERROR a 1", "ERROR a 2", "INFO b"])
    assert reducteur == "drain3" and modeles[0] == {"template": "ERROR a <*>", "compte": 2}


def test_drain3_defaillant_repli_maison(monkeypatch):
    monkeypatch.setitem(sys.modules, "drain3", SimpleNamespace())
    assert reduire_logs(["INFO x"])[1] == "maison"


def test_great_expectations_signale_sans_branchement(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda nom: object() if nom == "great_expectations" else None)
    assert moteur_detection() == ("python", "great_expectations détecté mais non branché : détecteur Python utilisé")


@pytest.mark.parametrize(("scenario", "abstient"), [("p1", False), ("p3", True)])
def test_moteur_sur_le_contexte_de_demo(tmp_path, scenario, abstient):
    injecter(scenario)
    contexte, _ = construire_contexte()
    resultat = enqueter(contexte, MemoireEnquete.json(tmp_path / "m.json"), LLMFaux(), AbstentionSeuil())
    assert resultat.decision_abstention.abstient is abstient
    if not abstient:
        assert "colonne renommée" in resultat.correctif.justification
