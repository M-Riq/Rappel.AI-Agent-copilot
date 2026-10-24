import json
from pathlib import Path
import sqlite3

from pydantic import ValidationError
import pytest

from rappel.enquete.modeles import ContexteIncident, EvenementEnquete
from rappel.interfaces import run_sql as module_run_sql
from rappel.interfaces.run_sql import (
    LIMITE_LIGNES, TABLES, ResultatSql, RunSqlSQLite, RunSqlZetaris, construire_base,
    creer_run_sql, lire_colonnes, valider_sql,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def charger(nom):
    return ContexteIncident.model_validate_json((FIXTURES / f"contexte_{nom}.json").read_text(encoding="utf-8"))


@pytest.fixture(params=["p1", "p2", "p3"])
def contexte(request):
    return charger(request.param)


class ConnexionEspion:
    def __init__(self, connexion):
        self.connexion = connexion
        self.requetes = []

    def execute(self, sql, *arguments):
        self.requetes.append(sql)
        return self.connexion.execute(sql, *arguments)


def espionner(run):
    espion = ConnexionEspion(run._connexion)
    run._connexion = espion
    return espion


def test_tables_constante_unique_et_base_conforme(contexte):
    connexion = construire_base(contexte)
    for table, colonnes in TABLES.items():
        infos = connexion.execute(f"PRAGMA table_info({table})").fetchall()
        assert [(info[1], info[2]) for info in infos] == list(colonnes.items())
    assert connexion.execute("SELECT count(*) FROM logs").fetchone()[0] == len(contexte.logs)
    assert connexion.execute("SELECT count(*) FROM commits").fetchone()[0] == len(contexte.commits_recents)
    assert connexion.execute("SELECT count(*) FROM metriques").fetchone()[0] == len(contexte.metriques)
    assert connexion.execute("SELECT nom, type FROM colonnes_attendues").fetchall() == lire_colonnes(contexte.schema.attendu)
    assert connexion.execute("SELECT nom, type FROM colonnes_observees").fetchall() == lire_colonnes(contexte.schema.observe)


def test_type_inconnu_et_doublons():
    assert lire_colonnes(["id:int", "sans_type", "id:str", " "]) == [("id", "int"), ("sans_type", "?")]


def test_mode_et_interface(contexte):
    run = RunSqlSQLite(contexte)
    assert run.mode == "simule-sqlite"
    assert isinstance(run.executer("SELECT nom FROM metriques"), ResultatSql)


def test_colonnes_attendues_absentes_des_observees(contexte):
    resultat = RunSqlSQLite(contexte).executer(
        "SELECT a.nom, a.type FROM colonnes_attendues a "
        "WHERE a.nom NOT IN (SELECT nom FROM colonnes_observees) ORDER BY a.nom"
    )
    attendues = dict(lire_colonnes(contexte.schema.attendu))
    observees = dict(lire_colonnes(contexte.schema.observe))
    assert resultat.erreur is None
    assert resultat.colonnes == ["nom", "type"]
    assert resultat.lignes == sorted([nom, type_colonne] for nom, type_colonne in attendues.items() if nom not in observees)
    assert resultat.tables == ["colonnes_attendues", "colonnes_observees"]


def test_valeurs_p1_p2_p3():
    sql = ("SELECT a.nom FROM colonnes_attendues a JOIN colonnes_observees o ON o.nom = a.nom "
           "WHERE a.type <> o.type UNION ALL SELECT nom FROM colonnes_attendues "
           "WHERE nom NOT IN (SELECT nom FROM colonnes_observees)")
    # Fixtures de l'equipe : P1 renomme amount, P2 change son type, P3 sans ecart.
    assert RunSqlSQLite(charger("p1")).executer(sql).lignes == [["amount"]]
    assert RunSqlSQLite(charger("p2")).executer(sql).lignes == [["amount"]]
    assert RunSqlSQLite(charger("p3")).executer(sql).nb_lignes == 0


def test_logs_au_compte_eleve(contexte):
    resultat = RunSqlSQLite(contexte).executer("SELECT template, compte FROM logs WHERE compte > 100 ORDER BY compte DESC")
    attendus = sorted(([log.template, log.compte] for log in contexte.logs if log.compte > 100), key=lambda ligne: -ligne[1])
    assert resultat.lignes == attendus


def test_commits_contenant_un_mot():
    resultat = RunSqlSQLite(charger("p1")).executer("SELECT hash FROM commits WHERE lower(message) LIKE '%rename%'")
    assert resultat.lignes == [[charger("p1").commits_recents[0].hash]]
    assert RunSqlSQLite(charger("p3")).executer("SELECT hash FROM commits").nb_lignes == 0


def test_metriques(contexte):
    resultat = RunSqlSQLite(contexte).executer("SELECT nom, valeur FROM metriques ORDER BY nom")
    assert resultat.lignes == [[nom, float(valeur)] for nom, valeur in sorted(contexte.metriques.items())]


def test_with_select_autorise():
    contexte = charger("p1")
    resultat = RunSqlSQLite(contexte).executer(
        "WITH eleves AS (SELECT template, compte FROM logs WHERE compte > 20) SELECT count(*) AS n FROM eleves"
    )
    assert resultat.erreur is None
    assert resultat.lignes == [[sum(log.compte > 20 for log in contexte.logs)]]


@pytest.mark.parametrize(("sql", "motif"), [
    ("INSERT INTO logs VALUES ('x', 1)", "INSERT"),
    ("DROP TABLE logs", "DROP"),
    ("SELECT * FROM logs; DROP TABLE logs", "une seule instruction"),
    ("SELECT 1; SELECT 2", "une seule instruction"),
    ("PRAGMA table_info(logs)", "PRAGMA"),
    ("SELECT * FROM sqlite_master", "hors liste blanche"),
    ("SELECT * FROM pragma_table_info('logs')", "hors liste blanche"),
    ("SELECT * FROM main.logs", "qualifies"),
    ('SELECT * FROM "sqlite_master"', "guillemets"),
    ("SELECT * FROM logs /* masque */; DELETE FROM logs", "une seule instruction"),
    ("SELECT * FROM logs -- commentaire\n; UPDATE logs SET compte = 0", "une seule instruction"),
    ("SELECT * FROM logs -- commentaire\n DROP TABLE logs", "DROP"),
    ("SELECT * FROM logs WHERE compte > 0 /* ' */ UNION SELECT name, 1 FROM sqlite_master", "hors liste blanche"),
    ("SELECT * FROM logs /* commentaire non termine", "commentaire non termine"),
    ("SELECT 'x FROM logs", "chaine litterale non terminee"),
    ("WITH x AS (SELECT 1) DELETE FROM logs", "DELETE"),
    ("ATTACH DATABASE 'autre.db' AS autre", "ATTACH"),
    ("SELECT replace(template, 'a', 'b') FROM logs", "REPLACE"),
    ("SELECT * FROM (SELECT 1) t, sqlite_master", "hors liste blanche"),
    ("EXPLAIN SELECT 1", "seules les requetes SELECT"),
    ("", "requete vide"),
    ("   ;  ", "requete vide"),
])
def test_refus_sans_exception_ni_execution(sql, motif):
    run = RunSqlSQLite(charger("p1"))
    espion = espionner(run)
    resultat = run.executer(sql)
    assert resultat.erreur is not None and resultat.erreur.startswith("requete refusee")
    assert motif in resultat.erreur
    assert resultat.lignes == [] and resultat.nb_lignes == 0
    assert espion.requetes == []
    assert run.executer("SELECT count(*) FROM logs").lignes == [[len(charger("p1").logs)]]


def test_mots_interdits_dans_commentaire_ou_chaine_acceptes():
    run = RunSqlSQLite(charger("p1"))
    resultat = run.executer("SELECT template FROM logs WHERE template LIKE '%drop%' -- DROP TABLE logs")
    assert resultat.erreur is None
    assert "DROP" not in resultat.requete and "--" not in resultat.requete
    assert resultat.requete == "SELECT template FROM logs WHERE template LIKE '%drop%' LIMIT 50"


def test_limit_ajoute_si_absent_et_conserve_sinon():
    assert valider_sql("SELECT nom FROM metriques;", TABLES).requete == "SELECT nom FROM metriques LIMIT 50"
    validation = valider_sql("SELECT nom FROM metriques LIMIT 3", TABLES)
    assert validation.requete == "SELECT nom FROM metriques LIMIT 3" and not validation.limite_ajoutee
    # Un LIMIT de sous-requete ne compte pas : le LIMIT de premier niveau est ajoute.
    assert valider_sql("SELECT nom FROM (SELECT nom FROM metriques LIMIT 2)", TABLES).requete.endswith(") LIMIT 50")


def contexte_volumineux(nombre):
    donnees = charger("p3").model_dump()
    donnees["logs"] = [{"template": f"ligne {indice}", "compte": indice} for indice in range(nombre)]
    return donnees


@pytest.mark.parametrize(("nombre", "sql", "lignes", "tronque"), [
    (60, "SELECT template FROM logs", LIMITE_LIGNES, True),
    (60, "SELECT template FROM logs LIMIT 100", LIMITE_LIGNES, True),
    (60, "SELECT template FROM logs LIMIT 10", 10, False),
    (49, "SELECT template FROM logs", 49, False),
    (50, "SELECT template FROM logs LIMIT 50", 50, False),
])
def test_troncature_a_50_lignes(nombre, sql, lignes, tronque):
    resultat = RunSqlSQLite(contexte_volumineux(nombre)).executer(sql)
    assert resultat.erreur is None
    assert resultat.nb_lignes == len(resultat.lignes) == lignes
    assert resultat.tronque is tronque


def test_erreur_sql_renvoyee_proprement():
    resultat = RunSqlSQLite(charger("p1")).executer("SELECT colonne_inexistante FROM logs")
    assert resultat.erreur is not None and resultat.erreur.startswith("erreur SQL")
    assert resultat.requete == "SELECT colonne_inexistante FROM logs LIMIT 50"
    assert resultat.lignes == [] and resultat.colonnes == []


def test_sql_execute_est_celui_renvoye():
    run = RunSqlSQLite(charger("p2"))
    espion = espionner(run)
    resultat = run.executer("  select   nom,\n type from colonnes_observees /* note */ order by nom ;")
    assert espion.requetes == [resultat.requete]
    assert resultat.requete == "select nom, type from colonnes_observees order by nom LIMIT 50"
    assert resultat.lignes == sorted([nom, type_colonne] for nom, type_colonne in lire_colonnes(charger("p2").schema.observe))


def test_moteur_en_lecture_seule_meme_sans_garde_fou():
    run = RunSqlSQLite(charger("p1"))
    with pytest.raises(sqlite3.DatabaseError):
        run._connexion.execute("DELETE FROM logs")
    with pytest.raises(sqlite3.DatabaseError):
        run._connexion.execute("SELECT name FROM sqlite_master")


def test_requete_trop_longue_interrompue(monkeypatch):
    monkeypatch.setattr(module_run_sql, "DELAI_MAX_EXECUTION_S", 0.2)
    resultat = RunSqlSQLite(charger("p1")).executer(
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
    )
    assert resultat.erreur is not None and resultat.erreur.startswith("erreur SQL")


def test_backend_choisi_par_source_provider(monkeypatch):
    monkeypatch.setenv("SOURCE_PROVIDER", "fichier")
    assert isinstance(creer_run_sql(charger("p1")), RunSqlSQLite)
    monkeypatch.setenv("SOURCE_PROVIDER", "zetaris")
    backend = creer_run_sql(charger("p1"))
    assert isinstance(backend, RunSqlZetaris) and backend.mode == "zetaris"


def zetaris_simule(monkeypatch, lignes=None, erreur=None):
    from rappel.interfaces import tools_zetaris

    appels = []

    def run_sql(sql, *, configuration=None, scenario="p1"):
        appels.append(sql)
        if erreur is not None:
            raise erreur
        return {"requete": sql, "lignes": lignes or []}

    monkeypatch.setattr(tools_zetaris, "run_sql", run_sql)
    monkeypatch.setenv("SOURCE_PROVIDER", "zetaris")
    return appels


def test_zetaris_meme_garde_fous_et_sql_execute(monkeypatch):
    appels = zetaris_simule(monkeypatch, [{"nom": "amount", "type": "decimal"}, {"nom": "id", "autre": 1}])
    backend = creer_run_sql(charger("p1"))
    refus = backend.executer("DROP TABLE logs")
    assert refus.erreur.startswith("requete refusee") and appels == []
    resultat = backend.executer("SELECT * FROM colonnes_attendues -- note")
    assert appels == [resultat.requete] == ["SELECT * FROM colonnes_attendues LIMIT 50"]
    assert resultat.colonnes == ["nom", "type", "autre"]
    assert resultat.lignes == [["amount", "decimal", None], ["id", None, 1]]
    assert resultat.tables == ["colonnes_attendues"] and resultat.erreur is None


def test_zetaris_troncature_et_erreur_assainie(monkeypatch):
    zetaris_simule(monkeypatch, [{"n": indice} for indice in range(60)])
    resultat = creer_run_sql(charger("p1")).executer("SELECT nom FROM metriques LIMIT 100")
    assert resultat.nb_lignes == LIMITE_LIGNES and resultat.tronque
    zetaris_simule(monkeypatch, erreur=RuntimeError("Zetaris est indisponible ou a refuse la requete"))
    resultat = creer_run_sql(charger("p1")).executer("SELECT nom FROM metriques")
    assert resultat.erreur.startswith("erreur SQL : Zetaris est indisponible")
    assert "Bearer" not in resultat.erreur and "Authorization" not in resultat.erreur


def test_resultat_serialisable_json(contexte):
    resultat = RunSqlSQLite(contexte).executer("SELECT * FROM logs")
    assert json.loads(resultat.model_dump_json())["nb_lignes"] == len(contexte.logs)


EVENEMENT = {"incident_id": "inc", "etape": 1, "type": "preuve", "acteur": "detective",
             "texte": "q1", "horodatage": "2026-10-15T10:42:00Z"}


def test_contrat_preuve_requete_sql():
    assert EvenementEnquete.model_validate(EVENEMENT).requete_sql is None
    evenement = EvenementEnquete.model_validate({**EVENEMENT, "requete_sql": "SELECT 1", "modele": "m"})
    assert evenement.requete_sql == "SELECT 1" and evenement.modele == "m"
    for vide in ("", "   "):
        with pytest.raises(ValidationError, match="requete non vide"):
            EvenementEnquete.model_validate({**EVENEMENT, "requete_sql": vide})
    assert EvenementEnquete.model_validate({**EVENEMENT, "type": "decision", "requete_sql": ""}).requete_sql == ""


def test_zetaris_message_avec_entete_masque(monkeypatch):
    zetaris_simule(monkeypatch, erreur=RuntimeError("refus ; Authorization: Bearer cle-secrete"))
    resultat = creer_run_sql(charger("p1")).executer("SELECT nom FROM metriques")
    assert resultat.erreur == "erreur SQL : Zetaris indisponible"
