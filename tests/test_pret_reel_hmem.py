"""Go / no-go avec MEMOIRE_PROVIDER=hmem : contrôle du fichier SQLite, Postgres et Docker non requis."""

import os

import pytest

from scripts import pret_reel
from tests.faux_services import reseau_local

SCAN_VERT = lambda: pret_reel.point("scan des secrets", pret_reel.VERT, cause="aucun secret")
MACHINE_SANS_DOCKER = lambda: [
    pret_reel.point("machine : RAM", pret_reel.VERT, cause="16 Go"),
    pret_reel.point("machine : Docker", pret_reel.ROUGE, cause="docker introuvable dans le PATH",
                    commande="démarrer Docker Desktop"),
]


def par_nom(rapport):
    return {element["point"]: element for element in rapport["points"]}


@pytest.fixture
def rien_de_branche(monkeypatch):
    with reseau_local(monkeypatch):
        monkeypatch.setenv("METERLESS_BASE_URL", "http://127.0.0.1:9/v1")
        for nom in ("METERLESS_RUNTIME_TOKEN", "ZETARIS_API_KEY", "DATABASE_URL"):
            monkeypatch.delenv(nom, raising=False)
        yield


def test_hmem_remplace_postgres_et_docker_non_requis(rien_de_branche, monkeypatch, tmp_path):
    monkeypatch.setenv("MEMOIRE_PROVIDER", "hmem")
    monkeypatch.setenv("HMEM_PATH", str(tmp_path / "hmem.sqlite"))
    rapport = pret_reel.evaluer(False, machine=MACHINE_SANS_DOCKER, scan=SCAN_VERT, tests=None)
    points = par_nom(rapport)
    assert "Postgres" not in points
    assert points["Mémoire H-MEM"]["statut"] == "VERT"
    assert points["machine : Docker"]["statut"] == "ORANGE" and points["machine : Docker"]["requis"] is False
    assert "Postgres" not in rapport["bloquants"] and "machine : Docker" not in rapport["bloquants"]
    assert {"Nightshift", "Zetaris"} <= set(rapport["bloquants"])  # les vrais services restent requis


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="droits de fichiers POSIX non applicables")
def test_hmem_dossier_non_inscriptible_rouge(rien_de_branche, monkeypatch, tmp_path):
    dossier = tmp_path / "lecture_seule"
    dossier.mkdir()
    dossier.chmod(0o500)
    try:
        monkeypatch.setenv("MEMOIRE_PROVIDER", "hmem")
        monkeypatch.setenv("HMEM_PATH", str(dossier / "hmem.sqlite"))
        rapport = pret_reel.evaluer(False, machine=MACHINE_SANS_DOCKER, scan=SCAN_VERT, tests=None)
    finally:
        dossier.chmod(0o700)
    memoire = par_nom(rapport)["Mémoire H-MEM"]
    assert memoire["statut"] == "ROUGE" and memoire["requis"] is True and "HMEM_PATH" in memoire["commande"]
    assert "Mémoire H-MEM" in rapport["bloquants"]


def test_sans_hmem_postgres_et_docker_inchanges(rien_de_branche, monkeypatch):
    monkeypatch.setenv("MEMOIRE_PROVIDER", "json")
    rapport = pret_reel.evaluer(False, machine=MACHINE_SANS_DOCKER, scan=SCAN_VERT, tests=None)
    points = par_nom(rapport)
    assert points["Postgres"]["statut"] == "ROUGE" and points["Postgres"]["requis"] is True
    assert points["machine : Docker"]["statut"] == "ROUGE" and points["machine : Docker"]["requis"] is True
    assert "Mémoire H-MEM" not in points
