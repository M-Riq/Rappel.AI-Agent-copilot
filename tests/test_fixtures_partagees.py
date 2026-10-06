from datetime import datetime, timedelta
import json
from pathlib import Path

from scripts import generer_fixtures_partagees as generateur
from src.rappel.contrats import CorrectifPropose, EvenementEnquete


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_fixtures_existantes_valides_et_etapes():
    correctif = CorrectifPropose.model_validate_json(
        (FIXTURES / "correctif_p1.json").read_text(encoding="utf-8")
    )
    donnees = json.loads((FIXTURES / "evenements_p1.json").read_text(encoding="utf-8"))
    evenements = [EvenementEnquete.model_validate(element) for element in donnees]
    assert [element.etape for element in evenements] == list(range(1, len(evenements) + 1))
    assert correctif.niveau_risque in {"auto", "validation", "refus"}
    assert correctif.justification.strip()
    preuves = [element.texte for element in evenements if element.type == "preuve" and element.acteur == "systeme"]
    assert preuves and "schema:" in correctif.justification
    assert "schema: colonne manquante amount:float" in correctif.justification
    assert any("schema: colonne manquante amount:float" in preuve for preuve in preuves)


def test_types_evenements_sans_debat():
    donnees = json.loads((FIXTURES / "evenements_p1.json").read_text(encoding="utf-8"))
    types = {element["type"] for element in donnees}
    assert {"decision", "preuve", "hypothese", "rejet"} <= types
    assert "debat" not in types


def test_verifier_identiques_et_perime(tmp_path):
    assert generateur.main([], destination=tmp_path) == 0
    avant = {chemin.name: chemin.read_bytes() for chemin in tmp_path.iterdir()}
    assert generateur.main(["--verifier"], destination=tmp_path) == 0
    assert {chemin.name: chemin.read_bytes() for chemin in tmp_path.iterdir()} == avant
    chemin = tmp_path / "correctif_p1.json"
    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    contenu["justification"] = "Ancienne proposition"
    chemin.write_text(json.dumps(contenu), encoding="utf-8")
    modifie = chemin.read_bytes()
    assert generateur.main(["--verifier"], destination=tmp_path) == 1
    assert chemin.read_bytes() == modifie


def test_verifier_absence_ne_cree_rien(tmp_path):
    destination = tmp_path / "inexistant"
    assert generateur.main(["--verifier"], destination=destination) == 1
    assert not destination.exists()


def test_generations_identiques_octet_pour_octet(tmp_path):
    assert generateur.main([], destination=tmp_path) == 0
    avant = {chemin.name: chemin.read_bytes() for chemin in tmp_path.iterdir()}
    assert generateur.main([], destination=tmp_path) == 0
    assert {chemin.name: chemin.read_bytes() for chemin in tmp_path.iterdir()} == avant
    for contenu in avant.values():
        assert contenu.endswith(b"\n")
        assert b"\r\n" not in contenu
        assert b"C:\\" not in contenu and b"/Users/" not in contenu


def test_horodatages_synthetiques_reguliers():
    documents = generateur.generer_documents()
    contexte = json.loads((FIXTURES / "contexte_p1.json").read_text(encoding="utf-8"))
    origine = datetime.fromisoformat(contexte["horodatage"])
    for indice, evenement in enumerate(documents["evenements_p1.json"]):
        assert datetime.fromisoformat(evenement["horodatage"]) == origine + timedelta(seconds=indice)


def test_sorties_suivies_identiques_generation():
    for nom, contenu in generateur.generer_octets().items():
        assert (FIXTURES / nom).read_bytes() == contenu