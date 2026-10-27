"""Tests de contrat des VRAIS adaptateurs contre des faux services locaux.

FAUX SERVICE LOCAL, NON UNE PREUVE SPONSOR : Nightshift, Zetaris et Postgres sont imités sur
127.0.0.1 ; ces tests vérifient en-têtes, URL, délais, relances, coupe-circuit, classification
des erreurs et absence de secret, pas l'usage réel des sponsors.
"""

from dataclasses import replace
import json

import pytest

from rappel.config import Configuration
from rappel.enquete.adaptateurs import LLMEnquete, LLMIndisponible, MemoireEnquete
from rappel.enquete.modeles import ContexteIncident
from rappel.interfaces import memoire as memoire_squelette
from rappel.interfaces import tools_zetaris
from rappel.interfaces.client_http import ClientService, ErreurService, PolitiqueReseau
from rappel.interfaces.llm import CONSEILS_METERLESS, MeterlessLLM
from rappel.interfaces.run_sql import RunSqlZetaris
from scripts import check_postgres, recette_module_a, recette_reelle
from tests.faux_services import ETIQUETTE, FauxNightshift, FauxPsycopg, FauxZetaris, environnement_faux_services, reseau_local
from tests.faux_services.zetaris import contexte_fixture


@pytest.fixture
def local(monkeypatch):
    with reseau_local(monkeypatch):
        yield


@pytest.fixture
def nightshift(local):
    with FauxNightshift() as serveur:
        yield serveur


@pytest.fixture
def zetaris(local):
    with FauxZetaris(chemin="/requetes/sql", cle_sql="statement") as serveur:
        yield serveur


def configuration_meterless(serveur, base=None, **options):
    return Configuration(llm_provider="meterless", meterless_base_url=base or serveur.url + "/v1",
                         meterless_runtime_token=serveur.jeton, meterless_model_rapide="meterless/fast",
                         meterless_model_profond="meterless/deep", reseau_attente_base_s=0, **options)


def configuration_zetaris(serveur, **options):
    return Configuration(source_provider="zetaris", zetaris_base_url=serveur.url + "/api/v1.0",
                         zetaris_api_key=serveur.cle, zetaris_org_id=serveur.org_id,
                         zetaris_chemin_sql=serveur.chemin, zetaris_cle_sql=serveur.cle_sql,
                         reseau_attente_base_s=0, **options)


def textes_sans_secret(textes, *secrets):
    return all(secret not in str(texte) for texte in textes for secret in secrets)


# --- Meterless Nightshift


@pytest.mark.parametrize("base", ["{url}/v1", "{url}", "{url}/v1/", "{url}/v1/chat/completions", "{url}/v1/models",
                                  "{url}/healthz", "127.0.0.1:{port}", "127.0.0.1:{port}/v1/v1"])
def test_meterless_url_normalisee_et_entetes_exacts(nightshift, base):
    configuration = configuration_meterless(nightshift, base.format(url=nightshift.url, port=nightshift.port))
    adaptateur = LLMEnquete(MeterlessLLM(configuration), configuration)
    adaptateur.completer("Role: hypotheses.\nDONNEES:\n" + json.dumps({"resume": {}}), role="hypotheses")
    requete = nightshift.requetes[-1]
    assert requete["chemin"] == "/v1/chat/completions"
    assert requete["entetes"]["Authorization"] == "Be" + "arer " + nightshift.jeton
    assert requete["entetes"]["meterless-app"] == "rappel" and requete["entetes"]["meterless-scope"] == "user"
    assert requete["corps"]["model"] == "meterless/fast"
    assert adaptateur.derniere_reponse_info["modele_repondu"] == "nvidia/nemotron-faux-local-rapide"
    assert adaptateur.usage.tokens_entree > 0


@pytest.mark.parametrize("scenario", ["texte", "markdown", "tool_calls", "tronque", "vide"])
def test_meterless_formats_de_reponse(nightshift, scenario):
    configuration = configuration_meterless(nightshift)
    adaptateur = LLMEnquete(MeterlessLLM(configuration), configuration)
    nightshift.scenarios.append(scenario)
    texte = adaptateur.completer("Role: hypotheses.\nDONNEES:\n{}", role="hypotheses")
    if scenario == "tool_calls":
        assert adaptateur.derniers_appels_outils[0]["function"]["name"] == "run_sql"
        assert "tool_calls" in texte
    elif scenario == "markdown":
        assert texte.startswith("```json")
    elif scenario == "vide":
        assert texte == ""
    else:
        assert isinstance(texte, str) and texte


def test_meterless_html_invalide(nightshift):
    nightshift.scenarios.append("html")
    with pytest.raises(LLMIndisponible, match="invalide"):
        configuration = configuration_meterless(nightshift)
        LLMEnquete(MeterlessLLM(configuration), configuration).completer("p", role="verdict")


def test_meterless_relances_attente_croissante(nightshift):
    attentes = []
    configuration = configuration_meterless(nightshift)
    client = ClientService("Meterless Nightshift", PolitiqueReseau(relances=2, attente_base_s=0.5), CONSEILS_METERLESS,
                           dormir=attentes.append, secrets=(nightshift.jeton,))
    nightshift.scenarios.extend(["429", "500", "json"])
    assert MeterlessLLM(configuration, client=client).completer("p", "hypotheses")["texte"]
    assert attentes == [0.5, 1.0] and client.nb_tentatives == 3
    nightshift.scenarios.extend(["500", "500", "500"])
    with pytest.raises(ErreurService) as erreur:
        MeterlessLLM(configuration, client=client).completer("p", "hypotheses")
    assert erreur.value.categorie == "serveur" and "5xx" in str(erreur.value)


def test_meterless_coupe_circuit(nightshift):
    configuration = configuration_meterless(nightshift)
    client = ClientService("Meterless Nightshift", PolitiqueReseau(relances=0, seuil_coupe_circuit=2), CONSEILS_METERLESS,
                           dormir=lambda s: None)
    llm = MeterlessLLM(configuration, client=client)
    nightshift.scenarios.extend(["500", "500"])
    for _ in range(2):
        with pytest.raises(ErreurService):
            llm.completer("p", "hypotheses")
    avant = len(nightshift.requetes)
    with pytest.raises(ErreurService) as erreur:
        llm.completer("p", "hypotheses")
    assert erreur.value.categorie == "coupe_circuit" and len(nightshift.requetes) == avant


def test_meterless_401_403_hors_ligne_delai_distincts(nightshift):
    erreurs = {}
    # rt-local est désormais refusé localement (règle de main, sans requête) : le 403 serveur est
    # couvert par un autre jeton reconnu mais interdit.
    nightshift.jetons_interdits.add("jeton-" + "interdit-local")
    with pytest.raises(ValueError, match="METERLESS_RUNTIME_TOKEN"):
        MeterlessLLM(replace(configuration_meterless(nightshift), meterless_runtime_token="rt-local")).completer("p", "hypotheses")
    for nom, jeton in (("non_autorise", "jeton-" + "inconnu-local"), ("interdit", "jeton-" + "interdit-local")):
        configuration = replace(configuration_meterless(nightshift), meterless_runtime_token=jeton)
        with pytest.raises(ErreurService) as erreur:
            MeterlessLLM(configuration).completer("p", "hypotheses")
        erreurs[nom] = erreur.value
    assert erreurs["non_autorise"].categorie == "non_autorise" and "jeton Runtime Nightshift incorrect" in str(erreurs["non_autorise"])
    assert erreurs["interdit"].categorie == "interdit" and "403" in str(erreurs["interdit"])
    nightshift.scenarios.append("lent")
    nightshift.delai_lent_s = 1.5
    lent = configuration_meterless(nightshift, meterless_delai_s=0.3, reseau_relances=0)
    with pytest.raises(ErreurService) as erreur:
        MeterlessLLM(lent).completer("p", "hypotheses")
    assert erreur.value.categorie == "delai" and "METERLESS_DELAI_S" in str(erreur.value)
    hors_ligne = configuration_meterless(nightshift, base="http://127.0.0.1:9/v1", reseau_relances=0)
    with pytest.raises(ErreurService) as erreur:
        MeterlessLLM(hors_ligne).completer("p", "hypotheses")
    assert erreur.value.categorie == "hors_ligne" and "indisponible" in str(erreur.value)
    textes = [*erreurs.values(), erreur.value, repr(MeterlessLLM(lent)), repr(MeterlessLLM(lent).client)]
    assert textes_sans_secret(textes, nightshift.jeton)


def test_meterless_delai_par_defaut_180_s():
    configuration = Configuration()
    assert configuration.meterless_delai_s == 180.0
    assert MeterlessLLM(configuration).client.politique.delai_lecture_s == 180.0


# --- Zetaris REST


def test_zetaris_entetes_identifiant_unique_et_cle_configurable(zetaris):
    configuration = configuration_zetaris(zetaris)
    for _ in range(3):
        assert tools_zetaris.run_sql("SELECT 1 AS one", configuration=configuration)["lignes"] == [{"one": 1}]
    identifiants = [requete["entetes"]["X-Request-ID"] for requete in zetaris.requetes]
    assert len(set(identifiants)) == 3
    for requete in zetaris.requetes:
        assert requete["chemin"] == "/api/v1.0/requetes/sql" and set(requete["corps"]) == {"statement"}
        assert requete["entetes"]["X-Org-ID"] == "7"


def test_zetaris_erreurs_classees_sans_secret(zetaris):
    configuration = configuration_zetaris(zetaris)
    erreurs = {}
    for sql, categorie in (("SELECT * FROM inconnue", "table_inconnue"), ("SELECT FROM WHERE", "syntaxe_sql")):
        with pytest.raises(ErreurService) as erreur:
            tools_zetaris.run_sql(sql, configuration=configuration)
        erreurs[categorie] = erreur.value
        assert erreur.value.categorie == categorie
    zetaris.scenarios.append("401")
    with pytest.raises(ErreurService) as erreur:
        tools_zetaris.run_sql("SELECT 1 AS one", configuration=configuration)
    assert erreur.value.categorie == "non_autorise" and "ZETARIS_API_KEY" in str(erreur.value)
    assert textes_sans_secret([*erreurs.values(), erreur.value], zetaris.cle)


def test_run_sql_zetaris_resultat_volumineux_tronque(zetaris):
    zetaris.scenarios.append("volumineux")
    resultat = RunSqlZetaris(contexte_fixture("p1"), configuration_zetaris(zetaris)).executer("SELECT template FROM logs")
    assert resultat.erreur is None and resultat.nb_lignes == 50 and resultat.tronque


def test_run_sql_zetaris_erreur_lisible(zetaris):
    resultat = RunSqlZetaris(contexte_fixture("p1"), configuration_zetaris(zetaris)).executer(
        "SELECT template FROM logs WHERE compte = 'x' GROUP BY")
    # Une reformulation guidée (LIMIT cité par le moteur) est tentée une fois, puis abandon propre.
    assert resultat.erreur.startswith("erreur SQL : apres une reformulation : Zetaris : dialecte ou syntaxe SQL refusé")
    assert zetaris.cle not in resultat.erreur


# --- Postgres (faux pilote)


def test_memoire_postgres_coupure_et_reprise(monkeypatch, tmp_path):
    faux = FauxPsycopg()
    monkeypatch.setitem(__import__("sys").modules, "psycopg", faux)
    secours = memoire_squelette.MemoireJSON(tmp_path / "secours.json")
    memoire = MemoireEnquete(memoire_squelette.MemoireAvecSecours(memoire_squelette.MemoirePostgres("postgresql://x"), secours))
    contexte = ContexteIncident.model_validate(contexte_fixture("p1"))
    from rappel.enquete import enqueter
    from rappel.enquete.adaptateurs import LLMFaux
    from rappel.interfaces.abstention import AbstentionSeuil

    enqueter(contexte, memoire, LLMFaux(), AbstentionSeuil())
    assert memoire.backend.mode == "postgres" and len(memoire.chercher(contexte.signature)) == 1
    assert memoire.lister_hypotheses(contexte.signature)
    faux.disponible = False
    assert memoire.chercher(contexte.signature) == [] and memoire.backend.mode == "fallback-json"
    faux.disponible = True
    assert len(memoire.chercher(contexte.signature)) == 1 and memoire.backend.mode == "postgres"
    assert memoire.mettre_a_jour_resultat(contexte.signature, "echoue")
    assert memoire.chercher(contexte.signature)[-1].resultat == "echoue"


def test_check_postgres_avec_faux_pilote(capsys):
    assert check_postgres.main(pilote=FauxPsycopg()) == 0
    sortie = capsys.readouterr().out
    assert "Aller-retour ecriture/lecture/suppression : OK" in sortie
    assert check_postgres.main(pilote=FauxPsycopg(avec_tables=False)) == 1


# --- Recettes contre les faux services (aucune preuve sponsor)


@pytest.fixture
def faux_complets(local, monkeypatch):
    with FauxNightshift() as faux_nightshift, FauxZetaris() as faux_zetaris:
        pilote = FauxPsycopg()
        environnement_faux_services(monkeypatch, faux_nightshift, faux_zetaris, pilote)
        from rappel.enquete import boucle_sql
        from rappel.ingestion import contexte_demo
        from rappel.pipeline_demo.pipeline import SCENARIOS

        original = boucle_sql.creer_run_sql

        def servir_le_contexte(contexte, configuration=None):
            # Un vrai Zetaris expose des vues limitées à l'incident ; ici le test choisit le contexte servi.
            faux_zetaris.contexte = contexte.model_dump() if hasattr(contexte, "model_dump") else contexte
            return original(contexte, configuration)

        def contrat_du_faux_zetaris(configuration=None, ordre=()):
            # Le faux Zetaris n'expose pas rappel.schema_contract : on sert le contrat de référence
            # (identique à la configuration du pipeline). La lecture réelle est testée dans test_contrat_zetaris.py.
            return [f"{nom}:{type_colonne}" for nom, type_colonne in SCENARIOS["schema_attendu"].items()]

        monkeypatch.setattr(boucle_sql, "creer_run_sql", servir_le_contexte)
        monkeypatch.setattr(contexte_demo, "schema_attendu_zetaris", contrat_du_faux_zetaris)
        yield faux_nightshift, faux_zetaris, pilote


def test_recette_reelle_contre_faux_services(faux_complets, tmp_path, capsys):
    destination = tmp_path / "recette_reelle.json"
    assert recette_reelle.main(["--confirmer", "--json", str(destination)]) == 0
    rapport = json.loads(destination.read_text(encoding="utf-8"))
    assert {etape["etape"]: etape["statut"] for etape in rapport["etapes"]} == dict.fromkeys(recette_reelle.ORDRE, "REUSSI")
    assert rapport["avertissement"] == "FAUX SERVICE LOCAL, NON une preuve sponsor"
    assert "FAUX SERVICE LOCAL" in capsys.readouterr().out
    faux_nightshift, faux_zetaris, _ = faux_complets
    texte = destination.read_text(encoding="utf-8")
    assert faux_nightshift.jeton not in texte and faux_zetaris.cle not in texte and "factice-local" not in texte


def test_recette_reelle_modele_hors_nemotron(faux_complets, tmp_path):
    faux_nightshift = faux_complets[0]
    faux_nightshift.modeles_repondus = {"*": "meta/llama-faux-local"}
    destination = tmp_path / "recette_reelle.json"
    assert recette_reelle.main(["--confirmer", "--json", str(destination)]) == 1
    rapport = json.loads(destination.read_text(encoding="utf-8"))
    statuts = {etape["etape"]: etape for etape in rapport["etapes"]}
    assert "le routage a choisi meta/llama-faux-local : fixer la voie" in statuts["A14"]["detail"]
    # Seules les étapes qui exigent un vrai Nemotron échouent.
    assert set(rapport["echecs"]) <= {"A14", "sponsors"} and "A14" in rapport["echecs"]


@pytest.mark.parametrize("modele, echecs", [(None, []), ("meta/llama-faux-local", ["A14"])])
def test_recette_module_a_contre_faux_services(faux_complets, monkeypatch, tmp_path, modele, echecs):
    if modele:
        faux_complets[0].modeles_repondus = {"*": modele}
    for nom, valeur in (("LLM_PROVIDER", "meterless"), ("SOURCE_PROVIDER", "zetaris"), ("MEMOIRE_PROVIDER", "postgres")):
        monkeypatch.setenv(nom, valeur)
    destination = tmp_path / "recette.json"
    code = recette_module_a.main(["--confirmer", "--json", str(destination)])
    rapport = json.loads(destination.read_text(encoding="utf-8"))
    assert rapport["echecs"] == echecs and code == (1 if echecs else 0)
    assert rapport["avertissement"].lower() == ETIQUETTE.lower()
    assert rapport["modes"]["simulation"] is False  # fournisseurs « réels » au sens du code, mais faux services
    if modele:
        a14 = next(case for case in rapport["cases"] if case["case"] == "A14")
        assert a14["detail"] == f"le routage a choisi {modele} : fixer la voie"
