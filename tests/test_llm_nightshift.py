import json
import logging

import httpx
import pytest

from scripts import check_meterless
from src.rappel.interfaces.llm import (
    LLMAvecSecours, LLMBudgetEpuise, LLMFaux, LLMIndisponible, LLMNightshift, MARQUEUR_DONNEES,
    UsageLLM, creer_llm, voie_pour_role,
)


CLE_FACTICE = "jeton-factice-nightshift-ne-pas-divulguer"
URL = "https://nightshift.test/v1"
VARIABLES = ("NIGHTSHIFT_URL", "NIGHTSHIFT_TOKEN", "NIGHTSHIFT_VOIE_RAPIDE", "NIGHTSHIFT_VOIE_PROFONDE")


@pytest.fixture(autouse=True)
def configurer(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIGHTSHIFT_URL", URL + "/")
    monkeypatch.setenv("NIGHTSHIFT_TOKEN", CLE_FACTICE)
    monkeypatch.setenv("NIGHTSHIFT_VOIE_RAPIDE", "voie-rapide-configuree")
    monkeypatch.setenv("NIGHTSHIFT_VOIE_PROFONDE", "voie-profonde-configuree")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.25")
    monkeypatch.setenv("LLM_MAX_TOKENS", "77")
    monkeypatch.setenv("LLM_TIMEOUT_S", "5")
    monkeypatch.setenv("LLM_MAX_APPELS", "20")
    monkeypatch.delenv("LLM_FALLBACK", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)


def serveur(requetes, modeles=None, usage=None, statut=200, contenu="reponse"):
    """Faux Nightshift : renvoie un champ "model" choisi par le service, pas celui demande."""
    def handler(request):
        requetes.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": "nvidia/nemotron-lightning"}, {"id": "nvidia/Nemotron-Super"}, {"id": "autre-modele"},
            ]}, request=request)
        if statut >= 400:
            return httpx.Response(statut, json={"error": CLE_FACTICE}, request=request)
        corps = json.loads(request.content)
        reponse = {"choices": [{"message": {"content": contenu}}],
                   "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}}
        if modeles is not None:
            reponse["model"] = modeles.get(corps["model"], corps["model"])
        return httpx.Response(200, json=reponse, request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(("role", "voie", "modele"), [
    ("hypotheses", "rapide", "voie-rapide-configuree"),
    ("verdict", "rapide", "voie-rapide-configuree"),
    ("conclusion", "profonde", "voie-profonde-configuree"),
    ("correctif", "profonde", "voie-profonde-configuree"),
])
def test_requete_bien_formee_sur_chaque_voie(role, voie, modele):
    requetes = []
    with serveur(requetes, modeles={}) as client:
        llm = LLMNightshift(client)
        assert llm.completer("prompt de test", role) == "reponse"
    requete = requetes[0]
    assert str(requete.url) == f"{URL}/chat/completions"
    assert requete.headers["Authorization"] == "Bearer " + CLE_FACTICE
    assert json.loads(requete.content) == {
        "model": modele,
        "messages": [{"role": "system", "content": f"Role: {role}"},
                     {"role": "user", "content": "prompt de test"}],
        "temperature": 0.25, "max_tokens": 77,
    }
    assert llm.derniere_reponse_info["voie"] == voie
    assert llm.derniere_reponse_info["modele_demande"] == modele


@pytest.mark.parametrize(("role", "voie"), [
    ("detective", "rapide"), ("hypotheses", "rapide"), ("verdict", "rapide"),
    ("conclusion", "profonde"), ("correctif", "profonde"), ("sceptique", "rapide"), ("inconnu", "rapide"),
])
def test_aiguillage_role_voie(role, voie):
    assert voie_pour_role(role) == voie


def test_tools_transmis_seulement_si_fournis():
    requetes = []
    outils = [{"type": "function", "function": {"name": "run_sql", "parameters": {"type": "object"}}}]
    with serveur(requetes) as client:
        llm = LLMNightshift(client)
        llm.completer("sans outils", "hypotheses")
        llm.completer("avec outils", "hypotheses", tools=outils)
    assert "tools" not in json.loads(requetes[0].content)
    assert json.loads(requetes[1].content)["tools"] == outils


def test_appel_outil_sans_contenu_renvoye_en_json():
    appel = [{"id": "c1", "type": "function", "function": {"name": "run_sql", "arguments": "{}"}}]

    def handler(request):
        return httpx.Response(200, json={"model": "m", "choices": [
            {"message": {"content": None, "tool_calls": appel}}]}, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        texte = LLMNightshift(client).completer("p", "hypotheses", tools=[{"type": "function"}])
    assert json.loads(texte) == {"tool_calls": appel}


def test_modele_repondu_lu_dans_la_reponse():
    requetes = []
    choix = {"voie-rapide-configuree": "nvidia/nemotron-lightning-v2",
             "voie-profonde-configuree": "auto-routeur-autre"}
    with serveur(requetes, modeles=choix) as client:
        llm = LLMNightshift(client)
        llm.completer("p", "hypotheses")
        assert llm.derniere_reponse_info == {
            "voie": "rapide", "modele_demande": "voie-rapide-configuree",
            "modele_repondu": "nvidia/nemotron-lightning-v2", "tokens_entree": 10, "tokens_sortie": 5,
        }
        llm.completer("p", "correctif")
        assert llm.derniere_reponse_info["modele_repondu"] == "auto-routeur-autre"
    with serveur([], modeles=None) as client:
        llm = LLMNightshift(client)
        llm.completer("p", "verdict")
        assert llm.derniere_reponse_info["modele_repondu"] is None


def test_compteurs_par_voie():
    with serveur([], modeles={}) as client:
        llm = LLMNightshift(client)
        for role in ("hypotheses", "verdict", "conclusion"):
            llm.completer("p", role)
    assert llm.usage.par_voie["rapide"] == UsageLLM(20, 10, 2)
    assert llm.usage.par_voie["profonde"] == UsageLLM(10, 5, 1)
    assert (llm.usage.tokens_entree, llm.usage.tokens_sortie, llm.usage.nb_appels) == (30, 15, 3)


def test_plafond_appels(monkeypatch):
    monkeypatch.setenv("LLM_MAX_APPELS", "2")
    requetes = []
    with serveur(requetes) as client:
        llm = LLMNightshift(client)
        llm.completer("p", "hypotheses")
        llm.completer("p", "conclusion")
        with pytest.raises(LLMBudgetEpuise, match="LLM_MAX_APPELS"):
            llm.completer("p", "correctif")
    assert len(requetes) == 2
    assert llm.usage.nb_appels == 2 and llm.usage.par_voie["profonde"].nb_appels == 1


def test_secours_vers_faux(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nightshift")
    monkeypatch.setenv("LLM_FALLBACK", "faux")
    with serveur([], statut=500) as client:
        llm = creer_llm(client)
        assert isinstance(llm, LLMAvecSecours) and isinstance(llm.principal, LLMNightshift)
        assert llm.completer("prompt", "detective") == LLMFaux().completer("prompt")
    assert llm.basculements == 1
    assert llm.derniere_reponse_info["modele_repondu"] == "faux-rapide"
    assert llm.usage.par_voie["rapide"].nb_appels == 2
    assert llm.usage.par_voie["rapide"].tokens_entree is None


def test_factory_nightshift(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nightshift")
    assert isinstance(creer_llm(), LLMNightshift)


@pytest.mark.parametrize("variable", VARIABLES)
def test_erreur_claire_sans_variable(monkeypatch, variable):
    monkeypatch.setenv("LLM_PROVIDER", "nightshift")
    monkeypatch.delenv(variable)
    with pytest.raises(ValueError, match=variable) as erreur:
        creer_llm()
    assert CLE_FACTICE not in str(erreur.value)


def test_erreur_toutes_variables_absentes(monkeypatch):
    for variable in VARIABLES:
        monkeypatch.delenv(variable)
    with pytest.raises(ValueError) as erreur:
        LLMNightshift()
    assert all(variable in str(erreur.value) for variable in VARIABLES)


def test_url_invalide(monkeypatch):
    monkeypatch.setenv("NIGHTSHIFT_URL", "nightshift.test/v1")
    with pytest.raises(ValueError, match="NIGHTSHIFT_URL"):
        LLMNightshift()


def test_aucune_fuite_du_jeton(caplog):
    caplog.set_level(logging.DEBUG)
    with serveur([], statut=500) as client:
        llm = LLMNightshift(client)
        with pytest.raises(LLMIndisponible) as erreur:
            llm.completer("prompt", "conclusion")
    def reseau(request):
        raise httpx.ConnectError(f"echec {CLE_FACTICE}", request=request)
    with httpx.Client(transport=httpx.MockTransport(reseau)) as client:
        autre = LLMNightshift(client)
        with pytest.raises(LLMIndisponible) as erreur_reseau:
            autre.completer("prompt", "hypotheses")
        with pytest.raises(LLMIndisponible) as erreur_modeles:
            autre.lister_modeles()
    textes = [str(erreur.value), repr(erreur.value), repr(llm), repr(llm.usage), str(llm.derniere_reponse_info),
              str(erreur_reseau.value), str(erreur_modeles.value), repr(autre), caplog.text]
    assert all(CLE_FACTICE not in texte for texte in textes)
    assert str(erreur_reseau.value) == "nightshift: erreur réseau"


def test_lister_modeles():
    requetes = []
    with serveur(requetes) as client:
        modeles = LLMNightshift(client).lister_modeles()
    assert str(requetes[0].url) == f"{URL}/models"
    assert requetes[0].headers["Authorization"] == "Bearer " + CLE_FACTICE
    assert modeles == ["nvidia/nemotron-lightning", "nvidia/Nemotron-Super", "autre-modele"]


def donnees_prompt(role, donnees):
    return f"Role: {role}.{MARQUEUR_DONNEES}{json.dumps(donnees)}"


def test_faux_roles_sql_json_valide_et_modele():
    llm = LLMFaux()
    hypotheses = json.loads(llm.completer(donnees_prompt("hypotheses", {}), "hypotheses"))
    assert len(hypotheses) >= 2 and all({"texte", "sql"} <= set(element) for element in hypotheses)
    assert llm.derniere_reponse_info["modele_repondu"] == "faux-rapide"
    hypothese = {"id": "h1", "texte": "t", "categorie": "schema", "requetes": [
        {"id": "q1", "nb_lignes": 1, "erreur": None, "colonnes": ["ecart", "nom", "type_attendu", "type_observe"],
         "lignes": [["absente", "a", "int", None], ["nouvelle", "b", None, "int"]]}]}
    verdict = json.loads(llm.completer(donnees_prompt("verdict", {"hypotheses": [hypothese], "incident_similaire": True}), "verdict"))
    assert verdict["verdicts"] == [{"id": "h1", "statut": "retenue", "requetes_citees": ["q1"], "raison": "resultats non vides"}]
    assert llm.derniere_reponse_info["voie"] == "rapide"
    conclusion = json.loads(llm.completer(donnees_prompt("conclusion", {"hypothese": hypothese}), "conclusion"))
    assert "renomm" in conclusion["cause_confirmee"]
    assert llm.derniere_reponse_info["modele_repondu"] == "faux-profond"
    correctif = json.loads(llm.completer(donnees_prompt("correctif", {
        "hypothese": hypothese, "contexte": {"composant": "c", "schema_attendu": ["a:int"], "schema_observe": ["b:int"]},
        "confiance": 0.7}), "correctif"))
    assert correctif["elements_touches"] == ["c"] and correctif["confiance"] == 0.7
    assert llm.usage.par_voie["rapide"].nb_appels == 2 and llm.usage.par_voie["profonde"].nb_appels == 2


def test_faux_anciens_roles_inchanges():
    llm = LLMFaux()
    assert len(json.loads(llm.completer("Incident"))) == 4
    assert json.loads(llm.completer("x", "sceptique"))["statut"] == "ouverte"
    assert json.loads(llm.completer("x", "historien"))["statut"] == "ouverte"
    with pytest.raises(ValueError, match="Role LLM inconnu"):
        llm.completer("x", "autre")


def test_check_meterless_exige_confirmer(capsys):
    def interdit(request):
        pytest.fail("Appel reseau interdit sans --confirmer")
    with httpx.Client(transport=httpx.MockTransport(interdit)) as client:
        assert check_meterless.main([], client=client) == 2
    assert "--confirmer" in capsys.readouterr().out


def test_check_meterless_ok(capsys):
    choix = {"voie-rapide-configuree": "nvidia/nemotron-lightning", "voie-profonde-configuree": "nvidia/Nemotron-Super"}
    with serveur([], modeles=choix) as client:
        assert check_meterless.main(["--confirmer"], client=client) == 0
    sortie = capsys.readouterr().out
    assert "OK : les deux voies" in sortie
    assert "['nvidia/nemotron-lightning', 'nvidia/Nemotron-Super']" in sortie
    assert CLE_FACTICE not in sortie


def test_check_meterless_a_corriger(capsys):
    choix = {"voie-rapide-configuree": "nvidia/nemotron-lightning", "voie-profonde-configuree": "auto-choisi"}
    with serveur([], modeles=choix) as client:
        assert check_meterless.main(["--confirmer"], client=client) == 1
    sortie = capsys.readouterr().out
    assert "A CORRIGER : la voie profonde repond avec auto-choisi" in sortie
    assert CLE_FACTICE not in sortie


def test_check_meterless_variable_manquante(monkeypatch, capsys):
    monkeypatch.delenv("NIGHTSHIFT_VOIE_PROFONDE")
    assert check_meterless.main(["--confirmer"]) == 1
    sortie = capsys.readouterr().out
    assert "NIGHTSHIFT_VOIE_PROFONDE" in sortie and CLE_FACTICE not in sortie
