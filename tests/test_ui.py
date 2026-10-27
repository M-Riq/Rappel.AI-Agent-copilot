import importlib.util
import importlib
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from streamlit.testing.v1 import AppTest

from rappel.config import Configuration


APP = Path(__file__).resolve().parents[1] / "ui" / "app.py"
spec = importlib.util.spec_from_file_location("rappel_ui", APP)
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


@pytest.fixture(autouse=True)
def isoler_modules_ui(monkeypatch):
    import sys

    for nom in ("zones", "logique", "client_api", "app"):
        monkeypatch.delitem(sys.modules, nom, raising=False)


def test_evenements_api_et_timeout(monkeypatch):
    evenements = [{"etape": 1, "horodatage": "2026-01-01", "message": "OK"}]
    reponse = Mock()
    reponse.json.return_value = {"evenements": evenements}
    obtenir = Mock(return_value=reponse)
    monkeypatch.setattr(ui.requests, "get", obtenir)
    resultat, hors_ligne = ui.charger_evenements(Configuration())
    assert resultat == evenements
    assert not hors_ligne
    obtenir.assert_called_once_with(
        "http://127.0.0.1:8000/incidents/p1/evenements", timeout=5
    )


@pytest.mark.parametrize(
    "probleme",
    [
        requests.ConnectionError("******example.invalid"),
        ValueError("JSON invalide avec secret"),
    ],
)
def test_repli_fixture(monkeypatch, probleme):
    monkeypatch.setattr(ui.requests, "get", Mock(side_effect=probleme))
    evenements, hors_ligne = ui.charger_evenements(Configuration())
    assert hors_ligne
    assert evenements


def test_tri_etape_puis_horodatage():
    evenements = [
        {"etape": 10, "horodatage": "2026-01-01"},
        {"etape": 2, "horodatage": "2026-01-02"},
        {"etape": 2, "horodatage": "2026-01-01"},
    ]
    assert ui.trier_evenements(evenements) == [
        evenements[2], evenements[1], evenements[0]
    ]


def test_streamlit_hors_ligne_sans_erreur_brute(monkeypatch):
    monkeypatch.setattr(
        requests, "get",
        Mock(side_effect=requests.ConnectionError("******example.invalid")),
    )
    app = AppTest.from_file(str(APP)).run(timeout=15)
    app.switch_page("pages/6_Exemple_P1.py").run(timeout=15)
    assert not app.exception
    assert any("API coupée" in message.value for message in app.warning)
    assert any("214 KeyError" in message.value for message in app.info)
    assert "secret" not in str(app)
    assert "example.invalid" not in str(app)


def test_streamlit_mode_et_champs_optionnels(monkeypatch):
    def obtenir(url, **kwargs):
        assert kwargs["timeout"] == 5
        assert url.endswith(("/healthz", "/incidents/p1/evenements"))
        reponse = Mock(status_code=200)
        reponse.json.return_value = (
            {
                "statut": "ok",
                "llm": "fake",
                "source": "fichier",
                "memoire": "json",
            } if url.endswith("/healthz") else [
                {
                    "etape": 1,
                    "type": "erreur",
                    "texte": "<script>alert('x')</script>",
                    "modele": "fake/nemotron",
                    "requete_sql": "SELECT 1 AS one",
                }
            ]
        )
        return reponse

    monkeypatch.setattr(requests, "get", obtenir)
    app = AppTest.from_file(str(APP)).run(timeout=15)
    app.switch_page("pages/6_Exemple_P1.py").run(timeout=15)
    assert not app.exception
    assert any(
        "Mode API — llm : fake · source : fichier · memoire : json" in texte.value
        for texte in app.text
    )
    assert any("fake/nemotron" in texte.value for texte in app.text)
    assert app.code[0].value == "SELECT 1 AS one"
    assert app.error[0].value == "<script>alert('x')</script>"


@pytest.fixture
def page_directe(monkeypatch, tmp_path):
    import sys
    from fastapi.testclient import TestClient
    from rappel import api
    from rappel.enquete.adaptateurs import MemoireEnquete

    monkeypatch.setenv("ACTION_REELLE_DEMO", "0")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    monkeypatch.syspath_prepend(str(APP.parent))
    for nom in ("zones", "logique", "client_api"):
        monkeypatch.delitem(sys.modules, nom, raising=False)
    zones = importlib.import_module("zones")
    application = api.creer_app(
        MemoireEnquete.json(tmp_path / "memoire-ui.json"), executeur_enquete=lambda tache: tache(),
    )
    with TestClient(application) as session:
        client = zones.ClientApi("http://testserver", session=session)
        monkeypatch.setattr(zones, "ClientApi", lambda url: client)
        page = AppTest.from_file(str(APP))
        yield page, client, zones


def test_streamlit_document_en_francais(page_directe):
    page, _, _ = page_directe
    page.run(timeout=15)
    assert not page.exception
    scripts = page.get("html")
    assert len(scripts) == 1
    assert scripts[0].proto.body == "<script>document.documentElement.lang = 'fr'; document.body.lang = 'fr';</script>"
    assert scripts[0].proto.unsafe_allow_javascript


def test_streamlit_langue_persistante_sans_changer_de_page(page_directe):
    page, _, _ = page_directe
    page.run(timeout=15)
    assert page.sidebar.selectbox(key="rappel_langue").value == "fr"
    page.sidebar.selectbox(key="rappel_langue").set_value("en").run(timeout=15)
    assert not page.exception
    assert "document.documentElement.lang = 'en'" in page.get("html")[0].proto.body
    page.switch_page("pages/2_Actions.py").run(timeout=15)
    assert page.sidebar.selectbox(key="rappel_langue").value == "en"
    page.sidebar.selectbox(key="rappel_langue").set_value("fr").run(timeout=15)
    assert not page.exception
    assert "document.documentElement.lang = 'fr'" in page.get("html")[0].proto.body


def test_streamlit_langue_conserve_consentement_et_incertitude(page_directe):
    page, client, zones = page_directe
    client.sante = Mock(return_value={"llm": "meterless", "source": "zetaris", "memoire": "postgres"})
    page.run(timeout=15)
    page.sidebar.selectbox(key="rappel_langue").set_value("en").run(timeout=15)
    assert not page.checkbox(key="rappel_accord_reel").value
    assert "3 attempt(s)" in page.button(key="rappel_injecter").label
    assert any("billable LLM calls" in element.value for element in page.warning)
    page.checkbox(key="rappel_accord_reel").check().run()
    assert "9 attempt(s)" in page.button(key="rappel_injecter").label
    page.sidebar.selectbox(key="rappel_langue").set_value("fr").run(timeout=15)
    assert page.checkbox(key="rappel_accord_reel").value
    page.sidebar.selectbox(key="rappel_langue").set_value("en").run(timeout=15)
    assert page.checkbox(key="rappel_accord_reel").value
    page.session_state["rappel_courant"] = "incident-incertain"
    client.lire_incident = Mock(side_effect=zones.ErreurApi("API coupée"))
    page.run(timeout=15)
    assert not page.exception
    assert page.button(key="rappel_injecter").disabled
    assert any("Incident status unconfirmed" in element.value for element in page.warning)
    page.sidebar.selectbox(key="rappel_langue").set_value("fr").run(timeout=15)
    assert page.button(key="rappel_injecter").disabled
    assert page.session_state["rappel_rejeux"] == 0


@pytest.mark.parametrize("statut,detail", [(None, None), (403, "Message original {API}"), (500, None)])
def test_streamlit_erreurs_bilingues_sans_traduire_detail(page_directe, statut, detail):
    page, client, zones = page_directe
    client.sante = Mock(side_effect=zones.ErreurApi("API coupée ou indisponible.", statut, detail))
    page.run(timeout=15)
    page.sidebar.selectbox(key="rappel_langue").set_value("en").run(timeout=15)
    assert not page.exception
    attendu = (f"API error {statut}: {detail or 'request rejected'}" if statut
               else "API offline or unavailable.")
    assert attendu in [element.value for element in page.warning]
    assert not any(element.key == "rappel_injecter" for element in page.button)


def test_streamlit_sept_vues_en_anglais_et_sessions_independantes(page_directe, monkeypatch):
    page, client, _ = page_directe
    monkeypatch.setattr(requests, "get", Mock(side_effect=requests.ConnectionError("hors ligne")))
    page.run(timeout=15)
    page.button(key="rappel_injecter").click().run(timeout=15)
    identifiant = page.session_state["rappel_courant"]
    incident = client.lire_incident(identifiant)
    page.sidebar.selectbox(key="rappel_langue").set_value("en").run(timeout=15)
    assert not page.exception
    assert page.sidebar.selectbox(key="rappel_langue").value == "en"
    assert "document.documentElement.lang = 'en'" in page.get("html")[0].proto.body
    assert "New investigation" in [element.value for element in page.subheader]
    assert page.button(key="rappel_injecter").label.startswith("Inject failure and investigate")
    autre_session = AppTest.from_file(str(APP)).run(timeout=15)
    assert autre_session.sidebar.selectbox(key="rappel_langue").value == "fr"
    assert "Nouvelle enquête" in [element.value for element in autre_session.subheader]
    for chemin, titre in [
        ("0_Incidents.py", "Incidents"),
        ("1_Enquete_en_direct.py", "Investigation"),
        ("2_Actions.py", "Actions"),
        ("3_Memoire.py", "Memory"),
        ("4_Compteurs.py", "Metrics"),
        ("5_Relations.py", "Relationships"),
        ("6_Exemple_P1.py", "P1 example"),
    ]:
        page.switch_page(f"pages/{chemin}").run(timeout=15)
        assert not page.exception
        assert titre in [element.value for element in page.title]
        assert page.sidebar.selectbox(key="rappel_langue").value == "en"
        assert page.sidebar.selectbox(key="rappel_choix_incident").value == identifiant
        assert page.sidebar.selectbox(key="rappel_choix_incident").options == ["P1 · Investigation 1"]
        if titre == "Actions":
            assert page.button(key=f"rappel_appliquer_{identifiant}").label == "Apply"
            assert page.button(key=f"rappel_refuser_{identifiant}").label == "Reject"
            assert "Risk level" in [element.label for element in page.metric]
            assert incident["correctif"]["patch"] in [element.value for element in page.code]
            assert incident["correctif"]["description"] in [element.value for element in page.markdown]
        if titre == "Memory":
            assert "attempted fix" in page.table[0].value.columns
            assert "unconfirmed" in page.table[0].value["outcome"].values
        if titre == "Metrics":
            assert "LLM calls" in page.table[0].value["metric"].values
        if titre == "Relationships":
            assert "has signature" in page.table[0].value["relation"].values
            assert "target" in page.table[0].value.columns
        if titre == "P1 example":
            assert any("showing local fixture events" in element.value for element in page.warning)
            assert page.multiselect(key="rappel_exemple_type").label == "Event types"
            assert any("214 KeyError" in element.value for element in page.info)
    assert page.session_state["rappel_rejeux"] == 1
    page.sidebar.selectbox(key="rappel_langue").set_value("fr").run(timeout=15)
    assert page.title[0].value == "Exemple P1"


def test_streamlit_langue_preserve_selection_filtres_et_tableaux(page_directe):
    page, _, _ = page_directe
    page.run(timeout=15)
    page.button(key="rappel_injecter").click().run(timeout=15)
    premier = page.session_state["rappel_courant"]
    page.selectbox(key="rappel_scenario").set_value("p2").run()
    page.button(key="rappel_injecter").click().run(timeout=15)
    dernier = page.session_state["rappel_courant"]
    page.sidebar.selectbox(key="rappel_choix_incident").set_value(premier).run(timeout=15)
    page.multiselect(key="rappel_historique_statut").set_value(["Enquête terminée"]).run()
    page.selectbox(key="rappel_historique_tri").set_value("scénario").run()
    page.checkbox(key="rappel_historique_decroissant").check().run()
    colonnes = ["incident", "identifiant complet", "statut"]
    page.multiselect(key="rappel_historique_colonnes").set_value(colonnes).run()
    page.selectbox(key="rappel_historique_taille").set_value(25).run()
    page.sidebar.selectbox(key="rappel_langue").set_value("en").run(timeout=15)
    assert not page.exception
    assert page.sidebar.selectbox(key="rappel_choix_incident").value == premier
    assert page.multiselect(key="rappel_historique_statut").value == ["Enquête terminée"]
    assert page.multiselect(key="rappel_historique_statut").options == ["Investigation completed"]
    assert page.selectbox(key="rappel_historique_tri").value == "scénario"
    assert page.checkbox(key="rappel_historique_decroissant").value
    assert page.multiselect(key="rappel_historique_colonnes").value == colonnes
    assert page.selectbox(key="rappel_historique_taille").value == 25
    assert list(page.table[0].value.columns) == ["incident", "full identifier", "status"]
    assert list(page.table[0].value["full identifier"]) == [dernier, premier]
    page.text_input(key="rappel_historique_recherche").set_value("completed").run()
    assert len(page.table[0].value) == 2
    page.text_input(key="rappel_historique_recherche").set_value(premier).run()
    page.sidebar.selectbox(key="rappel_langue").set_value("fr").run(timeout=15)
    assert not page.exception
    assert page.text_input(key="rappel_historique_recherche").value == premier
    assert list(page.table[0].value["identifiant complet"]) == [premier]
    assert page.session_state["rappel_courant"] == dernier
    assert page.session_state["rappel_rejeux"] == 2
    page.switch_page("pages/1_Enquete_en_direct.py").run(timeout=15)
    evenements = page.session_state["rappel_evenements"][premier].copy()
    page.multiselect(key="rappel_fil_type").set_value(["preuve"]).run()
    etape = page.slider(key=f"rappel_etape_{premier}").value
    page.sidebar.selectbox(key="rappel_langue").set_value("en").run(timeout=15)
    assert page.multiselect(key="rappel_fil_type").value == ["preuve"]
    assert "evidence" in page.multiselect(key="rappel_fil_type").options
    assert page.slider(key=f"rappel_etape_{premier}").value == etape
    assert page.session_state["rappel_evenements"][premier] == evenements
    assert all("SQL query" in element.label for element in page.expander if "SQL" in element.label)
    for evenement in evenements:
        if evenement.get("type") == "preuve":
            assert evenement["texte"] in [element.value for element in page.success]
            if evenement.get("requete_sql"):
                assert evenement["requete_sql"].strip() in [element.value for element in page.code]


@pytest.mark.parametrize("confirmee", [True, False])
def test_streamlit_decision_bilingue_ne_renvoie_jamais(page_directe, confirmee):
    page, client, zones = page_directe
    envoyer = Mock(wraps=client.decision) if confirmee else Mock(side_effect=zones.ErreurApi("API coupée"))
    client.decision = envoyer
    page.run(timeout=15)
    page.button(key="rappel_injecter").click().run(timeout=15)
    identifiant = page.session_state["rappel_courant"]
    page.switch_page("pages/2_Actions.py").run(timeout=15)
    page.sidebar.selectbox(key="rappel_langue").set_value("en").run(timeout=15)
    page.button(key=f"rappel_refuser_{identifiant}").click().run(timeout=15)
    assert not page.exception
    if confirmee:
        assert "Decision recorded: reject." in [element.value for element in page.info]
    else:
        assert any("Decision unconfirmed" in element.value for element in page.warning)
    for langue in ("fr", "en"):
        page.sidebar.selectbox(key="rappel_langue").set_value(langue).run(timeout=15)
        assert not page.exception
        assert page.button(key=f"rappel_appliquer_{identifiant}").disabled
        assert page.button(key=f"rappel_refuser_{identifiant}").disabled
    envoyer.assert_called_once_with(identifiant, "refuser")


def test_streamlit_navigation_vues_isolees(page_directe):
    page, _, _ = page_directe
    page.run(timeout=15)
    assert not page.exception
    assert [element.value for element in page.title if element.value != "Rappel"] == ["Incidents"]
    page.button(key="rappel_injecter").click().run(timeout=15)
    identifiant = page.session_state["rappel_courant"]
    for chemin, titre in [
        ("1_Enquete_en_direct.py", "Enquête"),
        ("2_Actions.py", "Actions"),
        ("3_Memoire.py", "Mémoire"),
        ("4_Compteurs.py", "Compteurs"),
        ("5_Relations.py", "Relations"),
    ]:
        page.switch_page(f"pages/{chemin}").run(timeout=15)
        assert not page.exception
        assert titre in [element.value for element in page.title]
        assert page.sidebar.selectbox(key="rappel_choix_incident").value == identifiant
        assert "P1 · Enquête 1" in [element.value for element in page.subheader]
        assert identifiant in [element.value for element in page.text]
        assert "Décision humaine : En attente" in [element.value for element in page.caption]
        assert not any(bouton.key == "rappel_injecter" for bouton in page.button)
        if titre != "Actions":
            assert not any(bouton.key.startswith(("rappel_appliquer_", "rappel_refuser_")) for bouton in page.button)


def test_streamlit_filtres_du_fil_et_reinitialisation(page_directe):
    page, _, _ = page_directe
    page.run(timeout=15)
    page.button(key="rappel_injecter").click().run(timeout=15)
    page.switch_page("pages/1_Enquete_en_direct.py").run(timeout=15)
    evenements = page.session_state["rappel_evenements"][page.session_state["rappel_courant"]].copy()
    page.multiselect(key="rappel_fil_type").set_value(["preuve"]).run(timeout=15)
    assert not page.exception
    assert page.code
    page.switch_page("pages/2_Actions.py").run(timeout=15)
    page.switch_page("pages/1_Enquete_en_direct.py").run(timeout=15)
    assert page.multiselect(key="rappel_fil_type").value == ["preuve"]
    page.text_input(key="rappel_fil_recherche").set_value("texte introuvable xyz").run(timeout=15)
    assert any("Aucun résultat" in message.value for message in page.info)
    assert not page.code
    page.button(key="rappel_fil_effacer").click().run(timeout=15)
    assert not page.exception
    assert page.multiselect(key="rappel_fil_type").value == []
    assert page.text_input(key="rappel_fil_recherche").value == ""
    assert page.session_state["rappel_evenements"][page.session_state["rappel_courant"]] == evenements


@pytest.mark.parametrize("chemin,cle,champ,valeur,colonne", [
    ("0_Incidents.py", "rappel_historique", "scénario", "P1", "scénario"),
    ("3_Memoire.py", "rappel_memoire", "résultat", "non confirmé", "résultat"),
    ("4_Compteurs.py", "rappel_mesures", "mesure", "Tokens", "mesure"),
    ("5_Relations.py", "rappel_relations", "relation", "a pour signature", "relation"),
])
def test_streamlit_filtres_des_vues(page_directe, chemin, cle, champ, valeur, colonne):
    page, _, _ = page_directe
    page.run(timeout=15)
    page.button(key="rappel_injecter").click().run(timeout=15)
    page.switch_page(f"pages/{chemin}").run(timeout=15)
    page.multiselect(key=f"{cle}_{champ}").set_value([valeur]).run(timeout=15)
    assert not page.exception
    assert list(page.table[0].value[colonne].unique()) == [valeur]
    page.text_input(key=f"{cle}_recherche").set_value("aucune-correspondance-xyz").run(timeout=15)
    assert not page.exception
    assert not page.table
    assert any("Aucun résultat" in message.value for message in page.info)


def test_streamlit_selection_incident_independante_du_dernier_lancement(page_directe):
    page, _, _ = page_directe
    page.run(timeout=15)
    page.button(key="rappel_injecter").click().run(timeout=15)
    premier = page.session_state["rappel_courant"]
    page.selectbox(key="rappel_scenario").set_value("p2").run()
    page.button(key="rappel_injecter").click().run(timeout=15)
    dernier = page.session_state["rappel_courant"]
    page.switch_page("pages/2_Actions.py").run(timeout=15)
    assert page.sidebar.selectbox(key="rappel_choix_incident").options == ["P1 · Enquête 1", "P2 · Enquête 2"]
    page.sidebar.selectbox(key="rappel_choix_incident").set_value(premier).run(timeout=15)
    assert not page.exception
    assert page.button(key=f"rappel_refuser_{premier}")
    assert page.session_state["rappel_courant"] == dernier
    assert page.session_state["rappel_rejeux"] == 2
    page.switch_page("pages/3_Memoire.py").run(timeout=15)
    assert page.sidebar.selectbox(key="rappel_choix_incident").value == premier


def test_streamlit_tri_colonnes_et_recherche_identifiant(page_directe):
    page, _, _ = page_directe
    page.run(timeout=15)
    page.button(key="rappel_injecter").click().run(timeout=15)
    premier = page.session_state["rappel_courant"]
    page.selectbox(key="rappel_scenario").set_value("p2").run()
    page.button(key="rappel_injecter").click().run(timeout=15)
    assert list(page.table[0].value["incident"]) == ["P2 · Enquête 2", "P1 · Enquête 1"]
    page.selectbox(key="rappel_historique_tri").set_value("incident").run()
    assert list(page.table[0].value["incident"]) == ["P1 · Enquête 1", "P2 · Enquête 2"]
    page.multiselect(key="rappel_historique_colonnes").set_value(["incident", "identifiant complet"]).run()
    page.text_input(key="rappel_historique_recherche").set_value(premier).run()
    assert list(page.table[0].value.columns) == ["incident", "identifiant complet"]
    assert list(page.table[0].value["identifiant complet"]) == [premier]
    page.switch_page("pages/2_Actions.py").run(timeout=15)
    page.switch_page("pages/0_Incidents.py").run(timeout=15)
    assert page.multiselect(key="rappel_historique_colonnes").value == ["incident", "identifiant complet"]
    page.multiselect(key="rappel_historique_colonnes").set_value([]).run()
    assert not page.exception
    assert not page.table
    assert "Aucune colonne sélectionnée." in [message.value for message in page.info]


def test_streamlit_panneaux_action_memoire_p1_p2_p3(page_directe):
    page, client, zones = page_directe
    envoyer = Mock(wraps=client.decision)
    client.decision = envoyer
    page.run(timeout=15)
    page.button(key="rappel_injecter").click().run(timeout=15)
    page.switch_page("pages/2_Actions.py").run(timeout=15)
    assert not page.exception
    assert any(metrique.label == "Confiance" for metrique in page.metric)
    identifiant_p1 = page.session_state["rappel_courant"]
    incident = client.lire_incident(identifiant_p1)
    assert any(code.value == incident["correctif"]["patch"] for code in page.code)
    page.button(key=f"rappel_refuser_{identifiant_p1}").click().run(timeout=15)
    assert not page.exception
    envoyer.assert_called_once_with(identifiant_p1, "refuser")
    assert page.button(key=f"rappel_appliquer_{identifiant_p1}").disabled
    assert page.button(key=f"rappel_refuser_{identifiant_p1}").disabled
    assert any("Aucun patch exécuté" in message.value for message in page.info)
    assert "Décision humaine : Correctif refusé" in [element.value for element in page.caption]
    page.button(key="rappel_rafraichir").click().run(timeout=15)
    envoyer.assert_called_once()
    page.switch_page("pages/3_Memoire.py").run(timeout=15)
    assert any("non confirmé" in str(table.value) for table in page.table)
    page.switch_page("pages/2_Actions.py").run(timeout=15)
    assert page.button(key=f"rappel_refuser_{identifiant_p1}").disabled

    page.switch_page("pages/0_Incidents.py").run(timeout=15)
    page.selectbox(key="rappel_scenario").set_value("p2").run()
    page.button(key="rappel_injecter").click().run(timeout=15)
    page.switch_page("pages/3_Memoire.py").run(timeout=15)
    assert not page.exception
    assert any("incident similaire" in message.value for message in page.markdown)
    identifiant_p2 = page.session_state["rappel_courant"]
    signature = zones.logique.signature_incident(client.graphe(identifiant_p2), identifiant_p2)
    memoire = client.memoire(signature)
    refutations = memoire["refutations_par_signature"][signature]
    assert refutations
    assert all(any(texte == message.value for message in page.markdown) for texte in refutations)
    assert any("Hypothèses réfutées enregistrées" in message.value for message in page.markdown)
    assert len(page.table) == 1

    page.switch_page("pages/0_Incidents.py").run(timeout=15)
    page.selectbox(key="rappel_scenario").set_value("p3").run()
    page.button(key="rappel_injecter").click().run(timeout=15)
    page.switch_page("pages/2_Actions.py").run(timeout=15)
    assert not page.exception
    assert any("Abstention" in message.value for message in page.markdown)
    assert not any(bouton.key.startswith(("rappel_appliquer_", "rappel_refuser_")) for bouton in page.button)
    envoyer.assert_called_once()


@pytest.mark.parametrize("probleme", ["timeout", "reponse_invalide"])
def test_streamlit_decision_incertitude_sans_renvoi(page_directe, probleme):
    page, client, zones = page_directe
    envoyer = Mock(side_effect=zones.ErreurApi("API coupée")) if probleme == "timeout" else Mock(return_value={})
    client.decision = envoyer
    page.run(timeout=15)
    page.button(key="rappel_injecter").click().run(timeout=15)
    identifiant = page.session_state["rappel_courant"]
    page.switch_page("pages/2_Actions.py").run(timeout=15)
    page.button(key=f"rappel_appliquer_{identifiant}").click().run(timeout=15)
    assert not page.exception
    assert page.button(key=f"rappel_appliquer_{identifiant}").disabled
    assert page.button(key=f"rappel_refuser_{identifiant}").disabled
    assert any("non confirmée" in message.value for message in page.warning)
    assert "Décision humaine : Non confirmée" in [element.value for element in page.caption]
    page.switch_page("pages/1_Enquete_en_direct.py").run(timeout=15)
    page.switch_page("pages/2_Actions.py").run(timeout=15)
    assert page.button(key=f"rappel_appliquer_{identifiant}").disabled
    envoyer.assert_called_once_with(identifiant, "appliquer")


def test_streamlit_trois_parcours_complets_et_compteurs(page_directe):
    page, client, zones = page_directe
    page.run(timeout=15)
    incidents = {scenario: [] for scenario in ("p1", "p2", "p3")}
    for _ in range(3):
        for scenario in incidents:
            page.switch_page("pages/0_Incidents.py").run(timeout=15)
            page.selectbox(key="rappel_scenario").set_value(scenario).run()
            assert not page.button(key="rappel_injecter").disabled
            page.button(key="rappel_injecter").click().run(timeout=15)
            assert not page.exception
            identifiant = page.session_state["rappel_courant"]
            incidents[scenario].append(identifiant)
            incident = client.lire_incident(identifiant)
            assert incident["statut"] == "termine"
            page.switch_page("pages/4_Compteurs.py").run(timeout=15)
            mesures = next(table.value for table in page.table if "mesure" in table.value.columns)
            valeurs = dict(zip(mesures["mesure"], mesures["valeur"]))
            assert valeurs["Tours d'enquête"] == incident["metriques"]["nb_tours"]
            assert valeurs["Appels LLM"] == incident["metriques"]["nb_appels_llm"]
            assert "Tokens" in valeurs
            assert "Durée (ms, indicative)" in valeurs
            if scenario == "p3":
                assert incident["decision_abstention"]["abstient"]
                assert not any(bouton.key.startswith(("rappel_appliquer_", "rappel_refuser_")) for bouton in page.button)
            else:
                assert incident["correctif"]
            if incidents["p2"]:
                for cle, scenario in (("rappel_cmp_a", "p1"), ("rappel_cmp_b", "p2")):
                    libelles = [zones.logique.libelle_incident(identifiant,
                        page.session_state["rappel_incidents"], page.session_state["rappel_scenarios"])
                        for identifiant in incidents[scenario]]
                    assert page.selectbox(key=cle).options == libelles
                    assert page.selectbox(key=cle).value in incidents[scenario]
    page.switch_page("pages/0_Incidents.py").run(timeout=15)
    assert len(set(page.session_state["rappel_incidents"])) == 9
    assert page.session_state["rappel_rejeux"] == 9
    assert page.button(key="rappel_injecter").disabled
    assert any("Plafond de 9" in message.value for message in page.info)
    page.button(key="rappel_actualiser_incidents").click().run(timeout=15)
    assert not page.exception
    assert page.session_state["rappel_rejeux"] == 9


def test_streamlit_resume_indisponible_sans_ancien_succes(page_directe):
    page, client, zones = page_directe
    page.run(timeout=15)
    page.button(key="rappel_injecter").click().run(timeout=15)
    envoyer = Mock()
    client.decision = envoyer
    client.lire_incident = Mock(side_effect=zones.ErreurApi("API coupée"))
    page.switch_page("pages/3_Memoire.py").run(timeout=15)
    assert not page.exception
    assert "Résumé indisponible : état de l'incident non confirmé." in [message.value for message in page.warning]
    assert any("État non confirmé" in element.value for element in page.markdown)
    assert not any("Enquête terminée" in element.value for element in page.markdown)
    assert not any(element.value.startswith("Décision humaine :") for element in page.caption)
    envoyer.assert_not_called()
