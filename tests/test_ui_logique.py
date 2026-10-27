"""Logique pure de l'interface et client API (sans Streamlit)."""

import importlib
import importlib.util
from pathlib import Path
import sys
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
import requests

from rappel import api
from rappel.enquete.adaptateurs import MemoireEnquete
from rappel.pipeline_demo import injecter

UI = Path(__file__).resolve().parents[1] / "ui"


def _charger(nom):
    spec = importlib.util.spec_from_file_location(f"rappel_ui_{nom}", UI / f"{nom}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


logique = _charger("logique")
client_api = _charger("client_api")


def test_catalogue_bilingue_et_parametres_preserves():
    from string import Formatter
    from ui.traductions import ANGLAIS, traduire

    def champs(texte):
        return {champ for _, champ, _, _ in Formatter().parse(texte) if champ is not None}

    for francais, anglais in ANGLAIS.items():
        assert champs(francais) == champs(anglais), francais
        assert traduire(francais, "fr") == francais
        assert traduire(francais, "en") == anglais
    assert traduire("Texte API inconnu {brut}", "en") == "Texte API inconnu {brut}"
    assert logique.resume_incident({"statut": "en_cours"}, "en") == "Investigation in progress."
    assert logique.libelle_incident("id", ["id"], {"id": "p1"}, "en") == "P1 · Investigation 1"
    assert logique.texte_evenement({"texte": "Preuve originale {brute}"}, "en") == "Preuve originale {brute}"


def test_aucun_import_streamlit_dans_la_logique():
    source = (UI / "logique.py").read_text(encoding="utf-8") + (UI / "client_api.py").read_text(encoding="utf-8")
    assert "streamlit" not in source


def test_libelle_incident_unique_sans_modifier_identifiants():
    incidents = ["identifiant-long-1", "identifiant-long-2", "identifiant-long-3"]
    scenarios = {incidents[0]: "p1", incidents[1]: "p1"}
    assert logique.libelle_incident(incidents[0], incidents, scenarios) == "P1 · Enquête 1"
    assert logique.libelle_incident(incidents[1], incidents, scenarios) == "P1 · Enquête 2"
    assert logique.libelle_incident(incidents[2], incidents, scenarios) == "Incident 3"
    assert logique.libelle_incident("externe", incidents, scenarios) == "externe"
    assert incidents == ["identifiant-long-1", "identifiant-long-2", "identifiant-long-3"]


def test_tri_numerique_stable_et_sans_mutation():
    lignes = [{"valeur": 10}, {"valeur": 2}, {"valeur": "inconnue"}, {"valeur": 0}]
    assert logique.trier_lignes(lignes, "valeur") == [lignes[3], lignes[1], lignes[0], lignes[2]]
    assert logique.trier_lignes(lignes, "valeur", True) == [lignes[2], lignes[0], lignes[1], lignes[3]]
    assert logique.trier_lignes(lignes, None) == lignes
    assert lignes == [{"valeur": 10}, {"valeur": 2}, {"valeur": "inconnue"}, {"valeur": 0}]


@pytest.mark.parametrize("type_evenement, gravite", [
    ("hypothese", "info"), ("preuve", "success"), ("rejet", "error"),
    ("debat", "warning"), ("decision", "success"), ("abstention", "warning"), ("inconnu", "info"),
])
def test_style_par_type(type_evenement, gravite):
    assert logique.style_evenement({"type": type_evenement})[0] == gravite


def test_badge_honnete():
    assert logique.badge_modele({"modele": "faux-rapide"}, True) == "simulé · faux-rapide"
    assert logique.badge_modele({"modele": "nvidia/Nemotron-super"}, True).startswith("simulé")
    assert logique.badge_modele({"modele": "nvidia/nemotron-nano"}, False) == "Nemotron via Meterless · nvidia/nemotron-nano"
    assert "fixer la voie" in logique.badge_modele({"modele": "gpt-x"}, False)
    assert logique.badge_modele({"acteur": "systeme"}, False) is None
    assert logique.badge_modele({"acteur": "detective"}, True) == "simulé"


def test_sql_sous_la_preuve():
    assert logique.sql_a_afficher({"type": "preuve", "requete_sql": " SELECT 1 LIMIT 50 "}) == "SELECT 1 LIMIT 50"
    assert logique.sql_a_afficher({"type": "preuve", "requete_sql": "  "}) is None
    assert logique.sql_a_afficher({"type": "hypothese"}) is None


def test_fusion_sans_doublon_et_apres():
    existants = [{"etape": 1, "texte": "a"}, {"etape": 2, "texte": "b"}]
    nouveaux = [{"etape": 2, "texte": "doublon"}, {"etape": 3, "texte": "c"}, "pas un dict"]
    fusion = logique.fusionner(existants, nouveaux)
    assert [e["texte"] for e in fusion] == ["a", "b", "c"]
    assert logique.derniere_etape(fusion) == 3
    assert logique.derniere_etape([]) == 0


def test_resume_et_simulation():
    assert logique.resume_incident(None) == "Aucun incident."
    assert logique.resume_incident({"statut": "en_cours"}) == "Enquête en cours."
    abstention = {"statut": "termine", "decision_abstention": {"abstient": True, "manque": ["deuxième source"]}}
    assert "deuxième source" in logique.resume_incident(abstention)
    assert "auto" in logique.resume_incident({"statut": "termine", "correctif": {"niveau_risque": "auto"},
                                              "decision_abstention": {"abstient": False}})
    mention = logique.mention_simulation({"enquete_simulee": True, "preuves_simulees": True, "source_preuves": "simule-sqlite"})
    assert "simulé" in mention and "simule-sqlite" in mention
    assert logique.mention_simulation({"enquete_simulee": False, "preuves_simulees": False}) is None
    assert logique.enquete_terminee({"statut": "erreur"}) and not logique.enquete_terminee({"statut": "en_cours"})


@pytest.mark.parametrize("incident, libelle, couleur", [
    (None, "État non confirmé", "gray"),
    ({"statut": "inconnu"}, "État non confirmé", "gray"),
    ({"statut": "en_cours"}, "Enquête en cours", "blue"),
    ({"statut": "erreur"}, "Enquête interrompue", "red"),
    ({"statut": "termine"}, "Enquête terminée", "green"),
    ({"statut": "termine", "decision_abstention": {"abstient": True}}, "Abstention", "orange"),
])
def test_statut_incident_avec_libelle_et_icone(incident, libelle, couleur):
    statut = logique.style_incident(incident)
    assert statut[:2] == (libelle, couleur)
    assert statut[2].startswith(":material/")


@pytest.mark.parametrize("incident, decision_locale, attendu", [
    ({"statut": "en_cours"}, None, "Indisponible"),
    ({"statut": "erreur"}, None, "Indisponible"),
    ({"statut": "termine"}, None, "Sans objet"),
    ({"statut": "termine", "correctif": {"description": "Correction"}}, None, "En attente"),
    ({"statut": "termine", "decision_abstention": {"abstient": True}}, None, "Sans objet"),
    ({"decision_humaine": "appliquer"}, None, "Application demandée"),
    ({"decision_humaine": "refuser"}, None, "Correctif refusé"),
    ({"decision_humaine": "autre"}, None, "Non reconnue"),
    ({}, {"decision": "appliquer", "confirmee": True}, "Application demandée"),
    ({"decision_humaine": "appliquer"}, {"decision": "appliquer", "confirmee": False}, "Non confirmée"),
])
def test_decision_humaine_sans_deduire_execution(incident, decision_locale, attendu):
    assert logique.libelle_decision(incident, decision_locale) == attendu


def test_compteur_et_duree_indicative():
    comparaison = {
        "metriques": {
            "a": {"nb_evenements": 21, "nb_hypotheses_testees": 4, "nb_appels_llm": 5,
                  "nb_tokens_entree": None, "nb_tokens_sortie": None, "duree_ms": 30},
            "b": {"nb_evenements": 12, "nb_hypotheses_testees": 1, "nb_appels_llm": 3,
                  "nb_tokens_entree": None, "nb_tokens_sortie": None, "duree_ms": 20},
        },
        "reductions_pct": {"evenements": 42.86, "hypotheses_testees": 75.0, "appels_llm": 40.0, "tokens": None, "duree": 33.33},
        "regle_60pc": {"evenements_b_sur_a": 0.5714, "appels_b_sur_a": 0.6, "respecte": True},
        "simulation": True,
    }
    lignes = logique.lignes_compteur(comparaison)
    assert lignes[0] == {"mesure": "Événements", "a": 21, "b": 12, "reduction": "42.86 %"}
    assert lignes[3]["a"] == "inconnu" and lignes[3]["reduction"] == "inconnue"
    assert "indicative" in lignes[4]["mesure"]
    verdict = logique.verdict_regle_60pc(comparaison)
    assert "respectée" in verdict and "57%" in verdict and verdict.endswith("(simulé)")
    assert "non mesurable" in logique.verdict_regle_60pc({})


def test_plafond_de_rejeu():
    assert logique.REJEUX_MAX == 3
    assert [logique.peut_rejouer(n) for n in range(5)] == [True, True, True, False, False]
    assert logique.identifiant_incident("p2", 2) == "ui-p2-2"


def test_budget_trois_parcours_et_accord_reel():
    assert logique.plafond_enquetes(True) == 9
    assert logique.plafond_enquetes(False) == 3
    assert logique.plafond_enquetes(False, True) == 9
    assert logique.peut_rejouer(8, 9)
    assert not logique.peut_rejouer(9, 9)
    assert logique.identifiant_incident("p1", 1, "session-a") != logique.identifiant_incident("p1", 1, "session-b")


def test_filtres_combines_sans_modifier_les_donnees():
    lignes = [
        {"etape": 1, "type": "preuve", "acteur": "analyste", "requete_sql": "SELECT montant FROM ventes"},
        {"etape": 2, "type": "rejet", "acteur": "historien", "texte": "ventes"},
        {"etape": 3, "type": "preuve", "acteur": "historien", "texte": "ventes confirmees"},
    ]
    assert logique.filtrer_lignes(lignes, " VENTES select ", {"type": ["preuve"]}) == [lignes[0]]
    assert logique.filtrer_lignes(lignes, selections={"type": ["preuve"], "acteur": ["historien"]}) == [lignes[2]]
    assert logique.filtrer_lignes(lignes, selections={"type": []}) == lignes
    assert logique.filtrer_lignes(lignes, "absent") == []
    assert [ligne["etape"] for ligne in lignes] == [1, 2, 3]


def test_pagination_bornee_apres_filtrage():
    lignes = [{"numero": numero} for numero in range(23)]
    assert logique.paginer(lignes, 2, 10) == (lignes[10:20], 2, 3)
    assert logique.paginer(lignes, 99, 10) == (lignes[20:], 3, 3)
    assert logique.paginer([], 3, 10) == ([], 1, 1)


def test_compteur_incident_distingue_etapes_tours_et_appels():
    incident = {"metriques": {
        "nb_evenements": 21, "nb_tours": 5, "nb_appels_llm": 6,
        "nb_appels_rapide": 4, "nb_appels_profonde": 2,
        "nb_tokens_entree": 100, "nb_tokens_sortie": 20, "duree_ms": 12.5,
        "nb_requetes_sql": 7, "nb_ecartees_par_memoire": 3,
    }}
    mesures = {ligne["mesure"]: ligne["valeur"] for ligne in logique.lignes_metriques(incident, [{"etape": 22}])}
    assert mesures == {
        "Dernière étape reçue": 22, "Événements d'enquête": 21, "Tours d'enquête": 5,
        "Appels LLM": 6, "Appels voie rapide": 4, "Appels voie profonde": 2,
        "Tokens": 120, "Durée (ms, indicative)": 12.5, "Requêtes SQL": 7,
        "Hypothèses écartées grâce à la mémoire": 3,
    }


@pytest.mark.parametrize("metriques", [{}, {"nb_tokens_entree": 10}, {"nb_tokens_sortie": 20}])
def test_compteur_mesures_absentes_non_inventees(metriques):
    mesures = {ligne["mesure"]: ligne["valeur"] for ligne in logique.lignes_metriques({"metriques": metriques}, [])}
    assert mesures["Dernière étape reçue"] == 0
    assert mesures["Tokens"] == "inconnue"
    assert mesures["Tours d'enquête"] == "inconnue"
    assert logique.total_tokens({"nb_tokens_entree": 0, "nb_tokens_sortie": 0}) == 0


def test_comparaison_tours_sans_reduction_inventee():
    lignes = logique.lignes_compteur({"metriques": {"a": {"nb_tours": 5}, "b": {"nb_tours": 2}}})
    tours = next(ligne for ligne in lignes if ligne["mesure"] == "Tours d'enquête")
    assert tours == {"mesure": "Tours d'enquête", "a": 5, "b": 2, "reduction": "inconnue"}


def test_client_api_coupee_sans_detail():
    session = Mock()
    session.get.side_effect = requests.ConnectionError("http://jeton-secret@example.invalid")
    client = client_api.ClientApi("http://127.0.0.1:8000/", session=session)
    with pytest.raises(client_api.ErreurApi) as erreur:
        client.sante()
    assert erreur.value.statut is None
    assert "secret" not in str(erreur.value) and "example.invalid" not in str(erreur.value)
    session.get.assert_called_once_with("http://127.0.0.1:8000/healthz", timeout=5)


def test_client_api_erreur_http_assainie():
    reponse = Mock(status_code=500)
    reponse.json.return_value = {"detail": "x" * 500}
    client = client_api.ClientApi("http://h", session=Mock(get=Mock(return_value=reponse)))
    with pytest.raises(client_api.ErreurApi) as erreur:
        client.lire_incident("p1")
    assert erreur.value.statut == 500 and "x" * 50 not in str(erreur.value)


def test_client_contre_l_api_p1_p2_compteur(tmp_path):
    application = api.creer_app(MemoireEnquete.json(tmp_path / "m.json"), executeur_enquete=lambda tache: tache())
    with TestClient(application) as session:
        client = client_api.ClientApi("http://testserver", session=session)
        assert client.sante()["statut"] == "ok"
        for scenario, numero in (("p1", 1), ("p2", 2)):
            injecter(scenario)
            client.creer_depuis_demo(logique.identifiant_incident(scenario, numero))
        a, b = "ui-p1-1", "ui-p2-2"
        evenements = logique.fusionner([], client.fil(a))
        assert evenements and logique.fusionner(evenements, client.fil(a, logique.derniere_etape(evenements))) == evenements
        assert any(logique.sql_a_afficher(e) for e in evenements if e["type"] == "preuve")
        incident = client.lire_incident(a)
        assert logique.enquete_terminee(incident) and logique.mention_simulation(incident)
        assert all(logique.badge_modele(e, True) in (None, "simulé") or logique.badge_modele(e, True).startswith("simulé")
                   for e in evenements)
        comparaison = client.comparaison(a, b)
        assert comparaison["regle_60pc"]["respecte"]
        assert logique.verdict_regle_60pc(comparaison).endswith("(simulé)")
        with pytest.raises(client_api.ErreurApi) as erreur:
            client.comparaison(a, "inconnu")
        assert erreur.value.statut == 404


class _FauxStreamlit:
    """Remplaçant minimal de Streamlit : enregistre les appels d'affichage."""

    def __init__(self, clics=()):
        self.session_state = {}
        self.appels = []
        self.clics = set(clics)

    def fragment(self, run_every=None):
        return lambda fonction: fonction

    def __getattr__(self, nom):
        def appel(*args, **kwargs):
            self.appels.append((nom, args, kwargs))
            if nom == "button":
                return kwargs.get("key", args[0] if args else None) in self.clics or (
                    args and str(args[0]).startswith("Injecter") and "injecter" in self.clics)
            if nom == "selectbox":
                options = list(args[1])
                return options[kwargs.get("index", 0)]
            if nom == "columns":
                return [self] * args[0]
            if nom == "expander":
                return self
            return None
        return appel

    def textes(self, nom):
        return [str(args[0]) for appel, args, _ in self.appels if appel == nom and args]


def test_zones_avec_streamlit_simule(monkeypatch, tmp_path):
    faux = _FauxStreamlit(clics={"injecter"})
    faux.session_state["rappel_session"] = "test"
    monkeypatch.setitem(sys.modules, "streamlit", faux)
    monkeypatch.syspath_prepend(str(UI))
    for nom in ("zones", "logique", "client_api"):
        monkeypatch.delitem(sys.modules, nom, raising=False)
    zones = importlib.import_module("zones")
    application = api.creer_app(MemoireEnquete.json(tmp_path / "m.json"), executeur_enquete=lambda tache: tache())
    with TestClient(application) as session:
        client = zones.ClientApi("http://testserver", session=session)
        zones.zone_pipeline(client)
        zones.zone_fil(client)
        zones.zone_resultat(client)
    assert faux.session_state["rappel_incidents"] == ["ui-p1-1-test"]
    assert any(code.startswith("SELECT") for code in faux.textes("code"))
    assert any("Mode simulé" in texte for texte in faux.textes("caption"))
    assert any(texte.startswith("simulé") for texte in faux.textes("caption"))
    assert not any("Nemotron via Meterless" in texte for texte in faux.textes("caption"))
    assert any("Correctif proposé" in texte for texte in faux.textes("write"))
    faux.session_state["rappel_rejeux"] = 9
    with TestClient(application) as session:
        zones.zone_pipeline(zones.ClientApi("http://testserver", session=session))
    assert any("Plafond de 9" in texte for texte in faux.textes("info"))


def test_zone_fil_api_coupee(monkeypatch):
    faux = _FauxStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", faux)
    monkeypatch.syspath_prepend(str(UI))
    for nom in ("zones", "logique", "client_api"):
        monkeypatch.delitem(sys.modules, nom, raising=False)
    zones = importlib.import_module("zones")
    faux.session_state["rappel_courant"] = "ui-p1-1"
    session = Mock(get=Mock(side_effect=requests.ConnectionError("http://jeton@example.invalid")))
    zones.zone_fil(zones.ClientApi("http://127.0.0.1:8000", session=session))
    avertissements = faux.textes("warning")
    assert avertissements == [zones.logique.MESSAGE_API_COUPEE]
    assert "example.invalid" not in str(faux.appels)


def test_signature_incident_sans_confondre_les_voisins():
    graphe = {
        "noeuds": [{"id": "signature:panne", "type": "signature"}],
        "aretes": [{"source": "incident:p1", "cible": "signature:panne", "type": "a_pour_signature"}],
    }
    assert logique.signature_incident(graphe, "p1") == "panne"
    assert logique.signature_incident(graphe, "p2") is None
    assert logique.signature_incident({}, "p1") is None


def test_memoire_statuts_confirmes_et_filtrage():
    memoire = {
        "souvenirs": [
            {"signature": "autre", "cause_confirmee": "étrangère"},
            {"signature": "panne", "cause_confirmee": "colonne", "resultat": "reussi"},
            {"signature": "panne", "cause_confirmee": "format", "resultat": "reussi"},
        ],
        "statuts_resultat": ["reussi", "non_confirme", "echoue"],
    }
    lignes = logique.lignes_souvenirs(memoire, "panne")
    assert [ligne["cause"] for ligne in lignes] == ["colonne", "format"]
    assert [ligne["résultat"] for ligne in lignes] == ["non confirmé", "échoué"]
    memoire["statuts_resultat"] = []
    assert all(ligne["résultat"] == "non confirmé" for ligne in logique.lignes_souvenirs(memoire, "panne"))


def test_evenements_memoire_sans_inventer_une_consultation():
    evenements = [
        {"acteur": "historien", "type": "preuve", "texte": "aucun incident similaire"},
        {"acteur": "systeme", "type": "decision", "texte": "incident similaire, essayé : patch (résultat non confirmé)"},
        {"acteur": "systeme", "type": "rejet", "texte": "Déjà réfutée en mémoire : disque"},
        {"acteur": "detective", "type": "hypothese", "texte": "Mémoire insuffisante"},
    ]
    assert logique.evenements_memoire(evenements) == evenements[:3]


def test_client_memoire_filtre_signature():
    donnees = {"souvenirs": [], "statuts_resultat": [], "refutations_par_signature": {}}
    reponse = Mock(status_code=200)
    reponse.json.return_value = donnees
    session = Mock(get=Mock(return_value=reponse))
    client = client_api.ClientApi("http://testserver", session=session)
    assert client.memoire("panne & colonne") == donnees
    session.get.assert_called_once_with("http://testserver/memoire", timeout=5, params={"signature": "panne & colonne"})


@pytest.mark.parametrize("donnees", [None, [], {}, {"souvenirs": [None], "statuts_resultat": [], "refutations_par_signature": {}}])
def test_client_memoire_reponse_invalide(donnees):
    reponse = Mock(status_code=200)
    reponse.json.return_value = donnees
    client = client_api.ClientApi("http://testserver", session=Mock(get=Mock(return_value=reponse)))
    with pytest.raises(client_api.ErreurApi, match="Format de mémoire invalide"):
        client.memoire("panne")
