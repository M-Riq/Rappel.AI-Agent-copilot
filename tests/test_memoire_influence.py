import json
from pathlib import Path

import pytest

from rappel.enquete.modeles import ContexteIncident, EnregistrementMemoire
from rappel.enquete import enqueter, outils
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.enquete.adaptateurs import LLMFaux
from rappel.enquete.adaptateurs import MemoireEnquete


def charger(nom):
    chemin = Path(__file__).resolve().parents[1] / "fixtures" / nom
    return ContexteIncident.model_validate_json(chemin.read_text(encoding="utf-8"))


def executer(contexte, memoire):
    return enqueter(contexte, memoire, LLMFaux(), AbstentionSeuil(), mode_preuves="outils")


@pytest.fixture
def parcours(tmp_path):
    memoire = MemoireEnquete.json(tmp_path / "memoire.json")
    premier = executer(charger("contexte_p1.json"), memoire)
    second = executer(charger("contexte_p2.json"), memoire)
    return premier, second


def test_a6_refutees_non_testees(tmp_path, monkeypatch):
    memoire = MemoireEnquete.json(tmp_path / "memoire.json")
    premier = executer(charger("contexte_p1.json"), memoire)
    refutees = premier.enregistrement.hypotheses_refutees
    appels = []
    for nom in ("tester_schema", "tester_ressources", "tester_reseau", "tester_configuration"):
        original = getattr(outils, nom)

        def espion(contexte, original=original, nom=nom):
            appels.append(nom)
            return original(contexte)

        monkeypatch.setattr(outils, nom, espion)
    second = executer(charger("contexte_p2.json"), memoire)
    assert appels == ["tester_schema"]
    assert second.metriques.nb_hypotheses_testees == len(appels)
    assert second.metriques.nb_ecartees_par_memoire == len(refutees) > 0
    assert not any(hypothese.texte in refutees for hypothese in second.hypotheses)
    tests = [evenement for evenement in second.evenements if evenement.acteur == "systeme"
             and evenement.type in {"preuve", "rejet"}]
    assert not any(texte in evenement.texte for texte in refutees for evenement in tests)
    assert any(evenement.acteur == "historien"
               and premier.enregistrement.correctif_essaye in evenement.texte
               and premier.enregistrement.date in evenement.texte
               for evenement in second.evenements)


def test_a7_moins_hypotheses_testees(parcours):
    premier, second = parcours
    assert second.metriques.nb_hypotheses_testees < premier.metriques.nb_hypotheses_testees


def test_a7_objectif_soixante_pourcent_hypotheses(parcours):
    premier, second = parcours
    # L'economie de la memoire porte sur les hypotheses testees, pas sur les noeuds.
    assert second.metriques.nb_hypotheses_testees <= 0.6 * premier.metriques.nb_hypotheses_testees


def test_ratio_evenements_informatif(parcours):
    premier, second = parcours
    # Les 8 noeuds du graphe emettent un cout fixe d'evenements.
    ratio = second.metriques.nb_evenements / premier.metriques.nb_evenements
    print(
        f"Ratio evenements P2/P1 : {second.metriques.nb_evenements}/"
        f"{premier.metriques.nb_evenements} = {ratio:.2%} (informatif, sans seuil)"
    )


def test_a8_memoire_contraire(tmp_path):
    contexte = charger("contexte_p1.json")
    vide = executer(contexte, MemoireEnquete.json(tmp_path / "vide.json"))
    assert vide.correctif is not None and "renomm" in vide.correctif.justification
    candidat_schema = next(candidat for candidat in json.loads(LLMFaux().completer(""))
                           if candidat["categorie"] == "schema")
    memoire = MemoireEnquete.json(tmp_path / "contraire.json")
    memoire.ecrire(EnregistrementMemoire(
        signature=contexte.signature, cause_confirmee="Diagnostic errone",
        hypotheses_refutees=[candidat_schema["texte"]],
        correctif_essaye="Autre correction", resultat="echoue", date="2026-10-14",
    ))
    contraire = executer(contexte, memoire)
    assert contraire.correctif is None
    assert contraire.decision_abstention.abstient
    assert contraire.metriques.nb_ecartees_par_memoire == 1
    assert candidat_schema["texte"] not in [hypothese.texte for hypothese in contraire.hypotheses]


def test_reutilisation_succes(parcours):
    premier, second = parcours
    justification = second.correctif.justification
    assert "incident similaire résolu par :" in justification
    assert premier.enregistrement.correctif_essaye in justification
    assert premier.enregistrement.date in justification
    assert second.correctif.niveau_risque == "validation"


def test_reutilisation_echec(tmp_path):
    contexte = charger("contexte_p2.json")
    memoire = MemoireEnquete.json(tmp_path / "memoire.json")
    souvenir = EnregistrementMemoire(
        signature=contexte.signature, cause_confirmee="Cause anterieure",
        hypotheses_refutees=[], correctif_essaye="Redemarrer le traitement",
        resultat="echoue", date="2026-10-14",
    )
    memoire.ecrire(souvenir)
    resultat = executer(contexte, memoire)
    assert "échec" in resultat.correctif.justification
    assert souvenir.correctif_essaye in resultat.correctif.justification
    assert souvenir.date in resultat.correctif.justification
    assert any(evenement.acteur == "historien" and "échec" in evenement.texte
               and souvenir.correctif_essaye in evenement.texte for evenement in resultat.evenements)
    assert resultat.correctif.niveau_risque == "validation"


def test_metriques_reelles(parcours):
    premier, second = parcours
    for resultat in (premier, second):
        assert resultat.metriques.nb_evenements == len(resultat.evenements)
        assert resultat.metriques.nb_appels_llm == 1
        assert resultat.metriques.duree_ms >= 0
    assert premier.metriques.nb_hypotheses_testees == 4
    assert premier.metriques.nb_ecartees_par_memoire == 0
    assert second.metriques.nb_hypotheses_testees == 1
    assert second.metriques.nb_ecartees_par_memoire == 3


def test_non_regression_memoire_vide(tmp_path):
    resultat = executer(charger("contexte_p1.json"), MemoireEnquete.json(tmp_path / "memoire.json"))
    # Valeurs de la fixture P1 de l'equipe (fixtures/contexte_p1.json).
    preuves_schema = [
        "schema: colonne manquante amount:decimal",
        "schema: nouvelle colonne total:decimal",
        "commits: a1b2c3: rename amount->total (2026-10-22T09:50:00Z)",
        "logs: KeyError: 'amount' (compte=214)",
    ]
    candidats = json.loads(LLMFaux().completer(""))
    attendues = []
    for indice, candidat in enumerate(candidats, start=1):
        preuves = preuves_schema if indice == 1 else []
        attendues.append({
            "id": f"p1:hyp-{indice}", "incident_id": "p1",
            "texte": candidat["texte"], "statut": "retenue" if indice == 1 else "refutee",
            "preuves": preuves, "confiance": 0.95 if indice == 1 else 0.0,
            "raison_rejet": None if indice == 1 else "Aucune preuve fournie par l'outil",
        })
    assert [hypothese.model_dump() for hypothese in resultat.hypotheses] == attendues
    description = "Restaurer le schema attendu de orders_etl: order_id:integer, amount:decimal"
    justification = "colonne renommee probable: schema: colonne manquante amount:decimal; schema: nouvelle colonne total:decimal"
    assert resultat.correctif.model_dump() == {
        "description": description,
        "patch": json.dumps({"composant": "orders_etl", "avant": ["order_id:integer", "total:decimal"],
                             "apres": ["order_id:integer", "amount:decimal"]}, indent=2),
        "niveau_risque": "validation", "elements_touches": ["orders_etl"],
        "reversible": True, "justification": justification, "confiance": 1.0,
    }
    assert vars(resultat.decision_abstention) == {
        "abstient": False, "confiance": 1.0, "manque": ["incident similaire en mémoire"]
    }
    assert resultat.metriques.nb_evenements == 13
    assert [(evenement.type, evenement.acteur) for evenement in resultat.evenements] == [
        ("decision", "systeme"), ("preuve", "historien"),
        *[("hypothese", "detective")] * 4,
        ("preuve", "systeme"), *[("rejet", "systeme")] * 3,
        ("decision", "systeme"), ("decision", "systeme"), ("decision", "historien"),
    ]
    assert resultat.enregistrement.cause_confirmee == justification
    assert resultat.enregistrement.correctif_essaye == description
    assert resultat.enregistrement.resultat == "reussi"
    assert resultat.enregistrement.hypotheses_refutees == [hypothese["texte"] for hypothese in attendues[1:]]