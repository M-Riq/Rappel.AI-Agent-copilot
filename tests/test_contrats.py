import copy
import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
FORMATS = FormatChecker()
NOMS = (
    "contexte_incident",
    "hypothese",
    "enregistrement_memoire",
    "correctif_propose",
    "evenement_enquete",
)


@FORMATS.checks("date-time", raises=ValueError)
def date_time_rfc3339(valeur):
    if not isinstance(valeur, str):
        return True
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})",
        valeur,
    ):
        return False
    return datetime.fromisoformat(valeur.upper().replace("Z", "+00:00")).utcoffset() is not None


def charger(chemin):
    return json.loads((ROOT / chemin).read_text(encoding="utf-8"))


def validateur(nom):
    schema = charger(f"contrats/{nom}.schema.json")
    return Draft202012Validator(schema, format_checker=FORMATS)


def exemples():
    return {
        "contexte_incident": charger("fixtures/contexte_p1.json"),
        "hypothese": {
            "id": "h1",
            "incident_id": "p1",
            "texte": "La colonne amount a été renommée total.",
            "statut": "retenue",
            "preuves": ["commit a1b2c3", "total:decimal observé"],
            "confiance": 0.98,
            "raison_rejet": None,
        },
        "enregistrement_memoire": {
            "signature": "schema_mismatch:orders_etl",
            "cause_confirmee": "Renommage de amount en total.",
            "hypotheses_refutees": ["Changement de type"],
            "correctif_essaye": "Lire total au lieu de amount.",
            "resultat": "reussi",
            "date": "2026-10-22",
        },
        "correctif_propose": charger("fixtures/correctif_p1.json"),
        "evenement_enquete": charger("fixtures/evenements_p1.json")[0],
    }


@pytest.mark.parametrize("nom", NOMS)
def test_schemas_draft_2020_12_et_exemples(nom):
    schema = charger(f"contrats/{nom}.schema.json")
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)
    validateur(nom).validate(exemples()[nom])


@pytest.mark.parametrize(
    ("fichier", "nom"),
    [
        ("contexte_p1.json", "contexte_incident"),
        ("contexte_p2.json", "contexte_incident"),
        ("contexte_p3.json", "contexte_incident"),
        ("correctif_p1.json", "correctif_propose"),
        ("evenements_p1.json", "evenement_enquete"),
    ],
)
def test_toutes_les_fixtures(fichier, nom):
    contenu = charger(f"fixtures/{fichier}")
    objets = contenu if isinstance(contenu, list) else [contenu]
    assert objets
    for objet in objets:
        validateur(nom).validate(objet)


@pytest.mark.parametrize("nom", NOMS)
def test_champs_requis_et_champs_supplementaires(nom):
    objet = exemples()[nom]
    schema = validateur(nom)
    for champ in schema.schema["required"]:
        incomplet = copy.deepcopy(objet)
        del incomplet[champ]
        assert not schema.is_valid(incomplet), champ
    assert not schema.is_valid({**objet, "inattendu": True})


@pytest.mark.parametrize(
    ("chemin", "champs"),
    [
        (("logs", 0), ["template", "compte"]),
        (("commits_recents", 0), ["hash", "message", "date"]),
        (("schema",), ["attendu", "observe"]),
        (("metriques",), ["lignes_rejetees", "latence_ms"]),
    ],
)
def test_champs_imbriques_requis(chemin, champs):
    for champ in champs:
        objet = charger("fixtures/contexte_p1.json")
        cible = objet
        for cle in chemin:
            cible = cible[cle]
        del cible[champ]
        assert not validateur("contexte_incident").is_valid(objet), champ


@pytest.mark.parametrize(
    ("chemin", "valeur"),
    [
        (("logs", 0, "inattendu"), True),
        (("commits_recents", 0, "inattendu"), True),
        (("schema", "inattendu"), True),
        (("metriques", "inattendu"), True),
        (("logs", 0, "compte"), -1),
        (("logs", 0, "compte"), 1.5),
        (("commits_recents", 0, "date"), "hier"),
        (("schema", "attendu"), [{"amount": "decimal"}]),
        (("schema", "observe"), [12]),
        (("metriques", "lignes_rejetees"), -1),
        (("metriques", "latence_ms"), -1),
        (("horodatage",), "2026-10-22"),
    ],
)
def test_contexte_invalide(chemin, valeur):
    objet = charger("fixtures/contexte_p1.json")
    cible = objet
    for cle in chemin[:-1]:
        cible = cible[cle]
    cible[chemin[-1]] = valeur
    assert not validateur("contexte_incident").is_valid(objet)


@pytest.mark.parametrize(
    ("nom", "champ", "valeur"),
    [
        ("hypothese", "statut", "inconnu"),
        ("hypothese", "preuves", [1]),
        ("hypothese", "raison_rejet", False),
        ("enregistrement_memoire", "resultat", "inconnu"),
        ("enregistrement_memoire", "hypotheses_refutees", [None]),
        ("enregistrement_memoire", "date", "hier"),
        ("enregistrement_memoire", "date", "2026-10-22T10:05:00Z"),
        ("correctif_propose", "niveau_risque", "inconnu"),
        ("correctif_propose", "elements_touches", [1]),
        ("correctif_propose", "reversible", "true"),
        ("evenement_enquete", "type", "inconnu"),
        ("evenement_enquete", "acteur", "inconnu"),
        ("evenement_enquete", "etape", 1.5),
        ("evenement_enquete", "etape", True),
        ("evenement_enquete", "horodatage", "hier"),
        ("evenement_enquete", "modele", None),
        ("evenement_enquete", "requete_sql", 1),
    ],
)
def test_autres_champs_invalides(nom, champ, valeur):
    objet = exemples()[nom]
    objet[champ] = valeur
    assert not validateur(nom).is_valid(objet)


@pytest.mark.parametrize("nom", ["hypothese", "correctif_propose"])
@pytest.mark.parametrize("confiance", [-0.01, 1.01, True, "0.5", None])
def test_confiance_invalide(nom, confiance):
    objet = exemples()[nom]
    objet["confiance"] = confiance
    assert not validateur(nom).is_valid(objet)


@pytest.mark.parametrize("nom", ["hypothese", "correctif_propose"])
@pytest.mark.parametrize("confiance", [0, 1])
def test_bornes_confiance_valides(nom, confiance):
    objet = exemples()[nom]
    objet["confiance"] = confiance
    validateur(nom).validate(objet)


@pytest.mark.parametrize(
    ("nom", "champ", "valeurs"),
    [
        ("hypothese", "statut", ["ouverte", "retenue", "refutee"]),
        ("hypothese", "raison_rejet", [None, "Type inchangé."]),
        ("enregistrement_memoire", "resultat", ["reussi", "echoue"]),
        ("correctif_propose", "niveau_risque", ["auto", "validation", "refus"]),
        (
            "evenement_enquete",
            "type",
            ["hypothese", "preuve", "rejet", "debat", "decision", "abstention"],
        ),
        (
            "evenement_enquete",
            "acteur",
            ["detective", "sceptique", "historien", "systeme"],
        ),
    ],
)
def test_valeurs_autorisees(nom, champ, valeurs):
    for valeur in valeurs:
        objet = exemples()[nom]
        objet[champ] = valeur
        validateur(nom).validate(objet)


def test_scenarios_distincts():
    p1, p2, p3 = [
        charger(f"fixtures/contexte_p{numero}.json") for numero in (1, 2, 3)
    ]
    assert [p["incident_id"] for p in (p1, p2, p3)] == ["p1", "p2", "p3"]
    assert p1["signature"] == p2["signature"] == "schema_mismatch:orders_etl"
    assert p1["logs"] == [{"template": "KeyError: 'amount'", "compte": 214}]
    assert p1["metriques"]["lignes_rejetees"] == 214
    assert p1["commits_recents"][0]["hash"] == "a1b2c3"
    assert p1["commits_recents"][0]["message"] == "rename amount->total"
    assert "amount:decimal" in p1["schema"]["attendu"]
    assert "total:decimal" in p1["schema"]["observe"]
    assert "amount:decimal" not in p1["schema"]["observe"]
    assert "amount:decimal" in p2["schema"]["attendu"]
    assert "amount:text" in p2["schema"]["observe"]
    assert p3["commits_recents"] == []
    assert p3["schema"]["attendu"] == p3["schema"]["observe"]
    assert "timeout" in p3["signature"]


def test_evenements_p1_couvrent_enquete():
    evenements = charger("fixtures/evenements_p1.json")
    assert len(evenements) == 8
    assert {e["incident_id"] for e in evenements} == {"p1"}
    assert [e["etape"] for e in evenements] == list(range(1, 9))
    assert [e["horodatage"] for e in evenements] == sorted(
        e["horodatage"] for e in evenements
    )
    assert {e["type"] for e in evenements} == {
        "hypothese", "preuve", "rejet", "debat", "decision"
    }
    assert {e["acteur"] for e in evenements} == {
        "detective", "sceptique", "historien", "systeme"
    }
    assert any(e.get("modele") == "fake" for e in evenements)
    assert any(e["type"] == "preuve" and e.get("requete_sql") for e in evenements)
