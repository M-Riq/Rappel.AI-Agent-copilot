import logging
import sys

from fastapi.testclient import TestClient
import pytest

from rappel import api
from rappel.enquete.adaptateurs import MemoireEnquete
from rappel.enquete.modeles import EnregistrementMemoire
from rappel.interfaces import memoire
from scripts import check_postgres


SECRET = "motdepasse-secret-factice"
URL = f"postgresql://rappel:{SECRET}@localhost:5432/rappel"


class Curseur:
    def __init__(self, pilote):
        self.pilote = pilote
        self.resultat = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *erreur):
        return False

    def execute(self, sql, parametres=()):
        self.pilote.requetes.append((sql, parametres))
        if sql == "SELECT 1":
            self.resultat = [(1,)]
        elif sql.startswith("SELECT table_name"):
            self.resultat = [(table,) for table in self.pilote.tables]
        elif sql.startswith("INSERT INTO memoire_incidents") and "RETURNING id" in sql:
            self.pilote.ligne = parametres[0]
            self.resultat = [(42,)]
        elif sql.startswith("SELECT signature FROM memoire_incidents WHERE id"):
            self.resultat = [(self.pilote.ligne,)]
        elif sql.startswith("INSERT INTO hypotheses") and "ON CONFLICT" not in sql:
            self.pilote.hypothese = parametres[0]
            self.resultat = []
        elif sql.startswith("SELECT id FROM hypotheses"):
            self.resultat = [(self.pilote.hypothese,)]
        else:
            self.resultat = []
            self.rowcount = 1

    def fetchone(self):
        return self.resultat[0] if self.resultat else None

    def fetchall(self):
        return list(self.resultat)


class Connexion:
    def __init__(self, pilote):
        self.pilote = pilote

    def __enter__(self):
        return self

    def __exit__(self, *erreur):
        return False

    def cursor(self):
        return Curseur(self.pilote)

    def rollback(self):
        self.pilote.annulations += 1

    def close(self):
        self.pilote.fermetures += 1


class Pilote:
    class Error(Exception):
        pass

    def __init__(self, disponible=True, tables=("hypotheses", "memoire_incidents")):
        self.disponible = disponible
        self.tables = tables
        self.requetes = []
        self.annulations = self.fermetures = 0
        self.ligne = self.hypothese = None

    def connect(self, url, connect_timeout=5):
        if not self.disponible:
            # Un vrai pilote peut citer l'URL : le message ne doit jamais remonter.
            raise self.Error(f"connexion refusee pour {url}")
        return Connexion(self)


def enregistrement():
    return {"signature": "sig:comp", "cause_confirmee": "Cause", "hypotheses_refutees": ["A"],
            "correctif_essaye": "Correctif", "resultat": "reussi", "date": "2026-10-22"}


@pytest.fixture
def postgres(monkeypatch, tmp_path):
    pilote = Pilote(disponible=False)
    monkeypatch.setitem(sys.modules, "psycopg", pilote)
    monkeypatch.setenv("MEMOIRE_PROVIDER", "postgres")
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("MEMOIRE_JSON_PATH", str(tmp_path / "secours.json"))
    monkeypatch.setattr(memoire, "_ETAT_SECOURS", {"mode": None, "alerte_emise": False})
    return pilote


def test_bascule_json_alerte_unique_sans_secret(postgres, tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    assert memoire.mode_memoire_effectif() == "non_verifie"
    memoire.ecrire(enregistrement())
    assert memoire.chercher("sig:comp") == [enregistrement()]
    assert (tmp_path / "secours.json").is_file()
    assert memoire.mode_memoire_effectif() == "fallback-json"
    alertes = [enregistre for enregistre in caplog.records if "fallback-json" in enregistre.getMessage()]
    assert len(alertes) == 1
    assert SECRET not in caplog.text and "rappel:" not in caplog.text


def test_retour_au_principal(postgres):
    backend = memoire.backend()
    assert isinstance(backend, memoire.MemoireAvecSecours)
    backend.chercher("sig:comp")
    assert backend.mode == "fallback-json"
    postgres.disponible = True
    assert backend.chercher("sig:comp") == []
    assert backend.mode == "postgres" and memoire.mode_memoire_effectif() == "postgres"
    assert postgres.requetes[-1][1] == ("sig:comp",)


def test_autres_erreurs_non_masquees(postgres, tmp_path):
    backend = memoire.backend()
    with pytest.raises(ValueError):
        backend.mettre_a_jour_resultat("sig:comp", "autre")

    class Principal:
        def chercher(self, signature):
            raise RuntimeError("erreur inattendue")

    with pytest.raises(RuntimeError, match="inattendue"):
        memoire.MemoireAvecSecours(Principal(), memoire.MemoireJSON(tmp_path / "x.json")).chercher("s")


def test_memoire_enquete_expose_le_mode(postgres):
    adaptateur = MemoireEnquete()
    adaptateur.ecrire(EnregistrementMemoire(**enregistrement()))
    assert adaptateur.backend.mode == "fallback-json"
    assert api.mode_memoire(adaptateur) == "fallback-json"


def test_healthz_et_incident_exposent_le_mode(postgres):
    application = api.creer_app(executeur_enquete=lambda tache: tache())
    with TestClient(application) as client:
        import json
        from rappel.config import RACINE

        contexte = json.loads((RACINE / "fixtures" / "contexte_p1.json").read_text(encoding="utf-8"))
        client.post("/incidents", json=contexte)
        assert client.get("/incidents/p1").json()["memoire_mode"] == "fallback-json"
        sante = client.get("/healthz").json()
        assert sante["memoire"] == "postgres" and sante["memoire_mode_effectif"] == "fallback-json"
        assert SECRET not in str(sante)


def test_healthz_json_inchange(monkeypatch):
    monkeypatch.setenv("MEMOIRE_PROVIDER", "json")
    with TestClient(api.creer_app()) as client:
        assert "memoire_mode_effectif" not in client.get("/healthz").json()


# --- scripts/check_postgres.py ----------------------------------------------------------

def test_masquer_url():
    assert check_postgres.masquer_url(URL) == "postgresql://rappel:****@localhost:5432/rappel"
    assert check_postgres.masquer_url("postgresql://localhost/rappel") == "postgresql://localhost/rappel"


def test_check_postgres_ok(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", URL)
    pilote = Pilote()
    assert check_postgres.main(pilote=pilote) == 0
    sortie = capsys.readouterr().out
    assert "SELECT 1 : OK" in sortie and "transaction annulee" in sortie
    assert SECRET not in sortie and "****" in sortie
    assert pilote.annulations == 1 and pilote.fermetures == 1
    assert all("%s" in sql or not parametres for sql, parametres in pilote.requetes)


def test_check_postgres_tables_absentes(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", URL)
    assert check_postgres.main(pilote=Pilote(tables=("hypotheses",))) == 1
    assert "Tables absentes : memoire_incidents" in capsys.readouterr().out


def test_check_postgres_injoignable_sans_secret(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", URL)
    assert check_postgres.main(pilote=Pilote(disponible=False)) == 1
    sortie = capsys.readouterr().out
    assert "Connexion Postgres impossible" in sortie and SECRET not in sortie


def test_check_postgres_pilote_absent(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    assert check_postgres.main() == 2
    assert "requirements-demo.txt" in capsys.readouterr().out
