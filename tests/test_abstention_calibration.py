from pathlib import Path

import pytest

from rappel.enquete.modeles import ContexteIncident, Hypothese
from rappel.enquete import enqueter
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.enquete.adaptateurs import LLMFaux
from rappel.enquete.adaptateurs import MemoireEnquete


def charger(nom):
    chemin = Path(__file__).resolve().parents[1] / "fixtures" / nom
    return ContexteIncident.model_validate_json(chemin.read_text(encoding="utf-8"))


def executer(contexte, memoire):
    return enqueter(contexte, memoire, LLMFaux(), AbstentionSeuil(), mode_preuves="outils")


def verifier_abstention(resultat):
    assert resultat.decision_abstention.abstient
    assert resultat.correctif is None
    assert resultat.enregistrement is None
    assert resultat.decision_abstention.manque
    assert any(evenement.type == "abstention" and evenement.acteur == "systeme"
               for evenement in resultat.evenements)


def test_a9_incident_ambigu(tmp_path):
    resultat = executer(charger("contexte_p3.json"), MemoireEnquete.json(tmp_path / "memoire.json"))
    verifier_abstention(resultat)


def test_a10_decisions_dans_un_meme_parcours(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "partagee.json")
    premier = executer(charger("contexte_p1.json"), memoire)
    second = executer(charger("contexte_p2.json"), memoire)
    ambigu = executer(charger("contexte_p3.json"), MemoireEnquete.json(tmp_path / "vide.json"))
    for resultat in (premier, second):
        assert not resultat.decision_abstention.abstient
        assert resultat.correctif is not None
    verifier_abstention(ambigu)


@pytest.mark.parametrize("nb_logs", [2, 3])
def test_correctif_resiste_au_bruit(tmp_path, nb_logs):
    donnees = charger("contexte_p1.json").model_dump()
    bruit = [
        {"template": "WARN memory monitor: temporary sampling delay", "compte": 2},
        {"template": "WARN metrics: timeout then retry succeeded", "compte": 3},
        {"template": "WARN disk monitor: delayed statistics", "compte": 1},
    ]
    donnees["logs"].extend(bruit[:nb_logs])
    resultat = executer(donnees, MemoireEnquete.json(tmp_path / "memoire.json"))
    assert not resultat.decision_abstention.abstient
    assert resultat.correctif is not None


def test_abstention_resiste_au_log_sans_rapport(tmp_path):
    donnees = charger("contexte_p3.json").model_dump()
    donnees["logs"].append({"template": "INFO audit: retention report generated", "compte": 100})
    verifier_abstention(executer(donnees, MemoireEnquete.json(tmp_path / "memoire.json")))


def test_abstention_avec_un_indice_faible(tmp_path):
    donnees = charger("contexte_p3.json").model_dump()
    # Un template eligible de plus, sans commit ni ecart de schema pour le corroborer.
    donnees["logs"].append({"template": "WARN connection timeout during optional polling", "compte": 6})
    resultat = executer(donnees, MemoireEnquete.json(tmp_path / "memoire.json"))
    reseau = next(hypothese for hypothese in resultat.hypotheses
                  if hypothese.texte == "Probleme reseau ou timeout")
    assert len(reseau.preuves) == 1
    retenue = next(hypothese for hypothese in resultat.hypotheses if hypothese.statut == "retenue")
    assert len(retenue.preuves) == 1
    assert {preuve.partition(":")[0] for preuve in retenue.preuves} == {"logs"}
    assert resultat.decision_abstention.confiance == pytest.approx(0.3)
    assert "deuxième source indépendante" in resultat.decision_abstention.manque
    verifier_abstention(resultat)


def test_une_preuve_une_source_ne_suffit_pas():
    hypothese = Hypothese(
        id="hyp-faible", incident_id="inc-faible", texte="Indice isole",
        statut="retenue", preuves=["logs: indice isole"], confiance=0.3, raison_rejet=None,
    )
    decision = AbstentionSeuil().decider(hypothese, nb_sources=1, incident_similaire=False)
    assert decision.abstient
    assert "deuxième source indépendante" in decision.manque


def test_confiance_exactement_au_seuil():
    politique = AbstentionSeuil()
    hypothese = Hypothese(
        id="hyp-limite", incident_id="inc-limite", texte="Cause avec preuves",
        statut="retenue", preuves=["logs: preuve", "schema: preuve"],
        confiance=politique.seuil, raison_rejet=None,
    )
    # La comparaison est stricte : egalite autorisee si une preuve existe.
    decision = politique.decider(hypothese, nb_sources=1, incident_similaire=False)
    assert decision.confiance == politique.seuil
    assert not decision.abstient
    assert "confiance sous le seuil" not in decision.manque