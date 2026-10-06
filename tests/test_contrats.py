from copy import deepcopy

from pydantic import ValidationError
import pytest

from src.rappel.contrats import (
    ContexteIncident,
    CorrectifPropose,
    EnregistrementMemoire,
    EvenementEnquete,
    Hypothese,
)


CONTEXTE = {
    "incident_id": "inc-0001",
    "horodatage": "2026-10-15T10:42:00Z",
    "signature": "schema_mismatch:orders_etl",
    "logs": [{"template": "KeyError: column <*> not found", "compte": 214}],
    "commits_recents": [
        {"hash": "a1b2c3", "message": "rename amount->total", "date": "2026-10-15"}
    ],
    "schema": {"attendu": ["id", "amount"], "observe": ["id", "total"]},
    "metriques": {"lignes_rejetees": 214, "latence_ms": 1800},
}

HYPOTHESE = {
    "id": "hyp-0001",
    "incident_id": "inc-0001",
    "texte": "Le renommage de amount provoque le rejet des lignes",
    "statut": "ouverte",
    "preuves": [],
    "confiance": 0.8,
    "raison_rejet": None,
}

MEMOIRE = {
    "signature": "schema_mismatch:orders_etl",
    "cause_confirmee": "Renommage de amount en total",
    "hypotheses_refutees": ["Panne reseau"],
    "correctif_essaye": "Restaurer la colonne amount",
    "resultat": "reussi",
    "date": "2026-10-15",
}

CORRECTIF = {
    "description": "Restaurer la colonne amount",
    "patch": "-total\n+amount",
    "niveau_risque": "validation",
    "elements_touches": ["orders_etl"],
    "reversible": True,
    "justification": "Le schema attendu utilise amount",
    "confiance": 0.9,
}

EVENEMENT = {
    "incident_id": "inc-0001",
    "etape": 1,
    "type": "hypothese",
    "acteur": "detective",
    "texte": "Examiner le dernier renommage de colonne",
    "horodatage": "2026-10-15T10:42:00Z",
}


def test_contexte_incident_valide():
    contexte = ContexteIncident.model_validate(CONTEXTE)
    assert contexte.model_dump() == CONTEXTE
    assert contexte.schema.attendu == ["id", "amount"]


@pytest.mark.parametrize("compte", [-1, 1.5])
def test_contexte_incident_compte_invalide(compte):
    donnees = deepcopy(CONTEXTE)
    donnees["logs"][0]["compte"] = compte
    with pytest.raises(ValidationError, match="compte"):
        ContexteIncident.model_validate(donnees)


@pytest.mark.parametrize("horodatage", ["invalide", "2026-10-15", "2026-13-15T10:42:00Z"])
def test_contexte_incident_horodatage_invalide(horodatage):
    with pytest.raises(ValidationError, match="date-heure ISO 8601"):
        ContexteIncident.model_validate({**CONTEXTE, "horodatage": horodatage})


@pytest.mark.parametrize(
    ("statut", "preuves", "raison_rejet"),
    [
        ("ouverte", [], None),
        ("retenue", ["Commit a1b2c3"], None),
        ("refutee", [], "Les logs contredisent cette hypothese"),
    ],
)
def test_hypothese_valide(statut, preuves, raison_rejet):
    donnees = {
        **HYPOTHESE,
        "statut": statut,
        "preuves": preuves,
        "raison_rejet": raison_rejet,
    }
    assert Hypothese.model_validate(donnees).model_dump() == donnees


@pytest.mark.parametrize("confiance", [-0.1, 1.5, float("nan")])
def test_hypothese_confiance_invalide(confiance):
    with pytest.raises(ValidationError, match="confiance"):
        Hypothese.model_validate({**HYPOTHESE, "confiance": confiance})


def test_hypothese_statut_inconnu():
    with pytest.raises(ValidationError, match="statut"):
        Hypothese.model_validate({**HYPOTHESE, "statut": "inconnu"})


def test_hypothese_retenue_sans_preuve():
    with pytest.raises(ValidationError, match="au moins une preuve"):
        Hypothese.model_validate({**HYPOTHESE, "statut": "retenue"})


@pytest.mark.parametrize("raison_rejet", [None, "", "   "])
def test_hypothese_refutee_sans_raison(raison_rejet):
    with pytest.raises(ValidationError, match="raison_rejet non vide"):
        Hypothese.model_validate(
            {**HYPOTHESE, "statut": "refutee", "raison_rejet": raison_rejet}
        )


def test_enregistrement_memoire_valide():
    assert EnregistrementMemoire.model_validate(MEMOIRE).model_dump() == MEMOIRE


def test_enregistrement_memoire_resultat_invalide():
    with pytest.raises(ValidationError, match="resultat"):
        EnregistrementMemoire.model_validate({**MEMOIRE, "resultat": "inconnu"})


def test_correctif_propose_valide():
    assert CorrectifPropose.model_validate(CORRECTIF).model_dump() == CORRECTIF


@pytest.mark.parametrize("confiance", [-0.1, 1.5])
def test_correctif_propose_confiance_invalide(confiance):
    with pytest.raises(ValidationError, match="confiance"):
        CorrectifPropose.model_validate({**CORRECTIF, "confiance": confiance})


def test_correctif_propose_risque_invalide():
    with pytest.raises(ValidationError, match="niveau_risque"):
        CorrectifPropose.model_validate({**CORRECTIF, "niveau_risque": "inconnu"})


def test_evenement_enquete_valide():
    assert EvenementEnquete.model_validate(EVENEMENT).model_dump() == EVENEMENT


def test_evenement_enquete_etape_zero():
    with pytest.raises(ValidationError, match="etape"):
        EvenementEnquete.model_validate({**EVENEMENT, "etape": 0})


@pytest.mark.parametrize("champ", ["type", "acteur"])
def test_evenement_enquete_literal_invalide(champ):
    with pytest.raises(ValidationError, match=champ):
        EvenementEnquete.model_validate({**EVENEMENT, champ: "inconnu"})


@pytest.mark.parametrize(
    ("modele", "donnees"),
    [
        (ContexteIncident, CONTEXTE),
        (Hypothese, HYPOTHESE),
        (EnregistrementMemoire, MEMOIRE),
        (CorrectifPropose, CORRECTIF),
        (EvenementEnquete, EVENEMENT),
    ],
)
def test_champs_partages_exacts(modele, donnees):
    assert set(modele.model_fields) == set(donnees)
    assert set(modele.model_json_schema()["properties"]) == set(donnees)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        modele.model_validate({**donnees, "champ_supplementaire": "interdit"})


@pytest.mark.parametrize("confiance", [0.0, 1.0])
@pytest.mark.parametrize(
    ("modele", "donnees"), [(Hypothese, HYPOTHESE), (CorrectifPropose, CORRECTIF)]
)
def test_bornes_confiance_valides(modele, donnees, confiance):
    assert modele.model_validate({**donnees, "confiance": confiance}).confiance == confiance