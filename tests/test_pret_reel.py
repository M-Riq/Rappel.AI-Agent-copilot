"""Go / no-go du branchement réel contre les faux services locaux (NON une preuve sponsor)."""

import json

import pytest

from scripts import pret_reel
from tests.faux_services import FauxNightshift, FauxPsycopg, FauxZetaris, environnement_faux_services, reseau_local

MACHINE_VERTE = lambda: [pret_reel.point("machine : RAM", pret_reel.VERT, cause="16 Go")]
SCAN_VERT = lambda: pret_reel.point("scan des secrets", pret_reel.VERT, cause="aucun secret")


@pytest.fixture
def faux(monkeypatch):
    with reseau_local(monkeypatch), FauxNightshift() as nightshift, FauxZetaris() as zetaris:
        environnement_faux_services(monkeypatch, nightshift, zetaris, FauxPsycopg())
        yield nightshift, zetaris


def statuts(rapport):
    return {element["point"]: element["statut"] for element in rapport["points"]}


def test_go_avec_confirmer(faux, tmp_path, capsys):
    destination = tmp_path / "pret.json"
    code = pret_reel.main(["--confirmer", "--json", str(destination)], machine=MACHINE_VERTE, scan=SCAN_VERT, tests=None)
    rapport = json.loads(destination.read_text(encoding="utf-8"))
    assert code == 0 and rapport["go"] is True, rapport["bloquants"]
    assert statuts(rapport)["voies et modèle Nemotron"] == "VERT"
    assert statuts(rapport)["clé NVIDIA côté Nightshift"] == "ORANGE"  # étape humaine, non requise
    sortie = capsys.readouterr().out
    assert "FAUX SERVICE LOCAL" in sortie and "Décision : GO" in sortie
    nightshift, zetaris = faux
    texte = destination.read_text(encoding="utf-8")
    assert nightshift.jeton not in texte and zetaris.cle not in texte and "factice-local" not in texte


def test_sans_confirmer_no_go_lecture_seule(faux, tmp_path):
    nightshift, _ = faux
    destination = tmp_path / "pret.json"
    assert pret_reel.main(["--sans-tests", "--json", str(destination)], machine=MACHINE_VERTE, scan=SCAN_VERT) == 1
    rapport = json.loads(destination.read_text(encoding="utf-8"))
    assert rapport["bloquants"] == ["voies et modèle Nemotron"]
    assert not any(requete["chemin"] == "/v1/chat/completions" for requete in nightshift.requetes)  # aucun appel facturé
    voies = next(element for element in rapport["points"] if element["point"] == "voies et modèle Nemotron")
    assert voies["commande"] == "python scripts/pret_reel.py --confirmer"


def test_modele_hors_nemotron_rouge(faux, tmp_path):
    faux[0].modeles_repondus = {"*": "meta/llama-faux-local"}
    rapport = pret_reel.evaluer(True, machine=MACHINE_VERTE, scan=SCAN_VERT, tests=None)
    voies = next(element for element in rapport["points"] if element["point"] == "voies et modèle Nemotron")
    assert voies["statut"] == "ROUGE" and "le routage a choisi meta/llama-faux-local : fixer la voie" in voies["cause"]


def test_rien_de_branche_causes_et_commandes(monkeypatch, tmp_path):
    with reseau_local(monkeypatch):
        monkeypatch.setenv("METERLESS_BASE_URL", "http://127.0.0.1:9/v1")
        for nom in ("METERLESS_RUNTIME_TOKEN", "ZETARIS_API_KEY", "DATABASE_URL"):
            monkeypatch.delenv(nom, raising=False)
        rapport = pret_reel.evaluer(False, machine=MACHINE_VERTE, scan=SCAN_VERT, tests=None)
    rouges = {element["point"]: element for element in rapport["points"] if element["statut"] == "ROUGE"}
    assert {"Nightshift", "voies et modèle Nemotron", "Zetaris", "Postgres"} <= set(rouges)
    assert all(element["commande"] for element in rouges.values())
    assert "detecter_nightshift.py" in rouges["Nightshift"]["commande"]
    assert rapport["go"] is False


def test_env_suivi_par_git_rouge(monkeypatch):
    monkeypatch.setattr(pret_reel.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    resultat = pret_reel.verifier_env_git()
    assert resultat["statut"] == "ROUGE" and "révoquer" in resultat["commande"]
