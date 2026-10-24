from unittest.mock import MagicMock

import pytest
import requests

from rappel.config import Configuration
from rappel.enquete.adaptateurs import LLMEnquete, LLMIndisponible
from rappel.interfaces import llm


JETON_FACTICE = "jeton-factice"


def reponse(donnees, statut=200):
    resultat = MagicMock(status_code=statut)
    resultat.json.return_value = donnees
    if statut >= 400:
        resultat.raise_for_status.side_effect = requests.HTTPError("réponse privée")
    return resultat


@pytest.mark.parametrize(
    "url,attendu",
    [
        ("http://h:8088", "http://h:8088/v1"),
        ("http://h:8088/", "http://h:8088/v1"),
        ("http://h:8088/v1", "http://h:8088/v1"),
        ("http://h:8088/v1/", "http://h:8088/v1"),
        ("http://h:8088/v1/v1", "http://h:8088/v1"),
        ("http://h:8088/v1/chat/completions", "http://h:8088/v1"),
        ("http://h:8088/v1/models", "http://h:8088/v1"),
        ("http://h:8088/healthz", "http://h:8088/v1"),
    ],
)
def test_normaliser_url_base(url, attendu):
    assert llm.normaliser_url_base(url) == attendu


@pytest.mark.parametrize(
    "statut,attendu",
    [(200, "ok"), (401, "non_autorise"), (403, "non_autorise"), (500, "erreur")],
)
def test_tester_connexion_statuts(monkeypatch, statut, attendu):
    monkeypatch.setattr(
        llm.requests,
        "get",
        MagicMock(return_value=reponse({"data": [{"id": "meterless/auto"}]}, statut)),
    )
    client = llm.MeterlessLLM(
        Configuration(meterless_runtime_token=JETON_FACTICE)
    )

    resultat = client.tester_connexion()

    assert resultat["statut"] == attendu
    assert resultat["modeles"] == (["meterless/auto"] if statut == 200 else [])


@pytest.mark.parametrize("erreur", [requests.ConnectionError, requests.Timeout])
def test_tester_connexion_erreur_reseau(monkeypatch, erreur):
    obtenir = MagicMock(side_effect=erreur("détail jeton-factice"))
    monkeypatch.setattr(llm.requests, "get", obtenir)
    client = llm.MeterlessLLM(Configuration(meterless_runtime_token=JETON_FACTICE))

    resultat = client.tester_connexion()

    assert resultat["statut"] == "injoignable"
    assert "jeton-factice" not in resultat["message"]


def test_jeton_absent_n_envoie_aucune_requete(monkeypatch):
    obtenir = MagicMock()
    monkeypatch.setattr(llm.requests, "get", obtenir)

    resultat = llm.MeterlessLLM(Configuration()).tester_connexion()

    assert resultat["statut"] == "non_autorise"
    obtenir.assert_not_called()


def test_lister_modeles_conserve_tous_les_identifiants(monkeypatch):
    obtenir = MagicMock(
        return_value=reponse(
            {"data": [{"id": "meterless/inconnu"}, {"id": "nvidia/nemotron"}]}
        )
    )
    monkeypatch.setattr(llm.requests, "get", obtenir)
    client = llm.MeterlessLLM(Configuration(meterless_runtime_token=JETON_FACTICE))

    modeles = client.lister_modeles()

    assert modeles == ["meterless/inconnu", "nvidia/nemotron"]
    url, parametres = obtenir.call_args.args[0], obtenir.call_args.kwargs
    assert url == "http://127.0.0.1:8088/v1/models"
    assert parametres["headers"] == {
        "Authorization": "Bearer " + JETON_FACTICE,
        "meterless-app": "rappel",
        "meterless-scope": "user",
    }


def test_chat_texte_retourne_texte_et_modele_effectif(monkeypatch):
    poster = MagicMock(
        return_value=reponse(
            {
                "model": "meterless/nemotron-effectif",
                "choices": [{"message": {"content": " Réponse exacte\n"}}],
            }
        )
    )
    monkeypatch.setattr(llm.requests, "post", poster)
    client = llm.MeterlessLLM(Configuration(meterless_runtime_token=JETON_FACTICE))

    resultat = client.completer("bonjour", "detective")

    assert resultat["texte"] == " Réponse exacte\n"
    assert resultat["modele"] == "meterless/nemotron-effectif"
    url, parametres = poster.call_args.args[0], poster.call_args.kwargs
    assert url == "http://127.0.0.1:8088/v1/chat/completions"
    assert parametres["headers"]["Authorization"] == "Bearer " + JETON_FACTICE
    assert parametres["headers"]["meterless-app"] == "rappel"
    assert parametres["headers"]["meterless-scope"] == "user"
    assert parametres["timeout"] == 180


def test_url_invalide_est_erreur_configuration(monkeypatch):
    poster = MagicMock()
    monkeypatch.setattr(llm.requests, "post", poster)
    client = llm.MeterlessLLM(
        Configuration(
            meterless_base_url="",
            meterless_runtime_token=JETON_FACTICE,
        )
    )

    with pytest.raises(ValueError, match="METERLESS_BASE_URL"):
        client.completer("bonjour", "detective")
    poster.assert_not_called()


def test_connection_error_est_convertie_par_llm_enquete_sans_secret(monkeypatch):
    monkeypatch.setattr(
        llm.requests,
        "post",
        MagicMock(side_effect=requests.ConnectionError("Authorization: Bearer " + JETON_FACTICE)),
    )
    configuration = Configuration(meterless_runtime_token=JETON_FACTICE)
    fournisseur = llm.MeterlessLLM(configuration)
    adaptateur = LLMEnquete(fournisseur, configuration)

    with pytest.raises(LLMIndisponible) as erreur:
        adaptateur.completer("bonjour", "detective")

    assert isinstance(erreur.value, RuntimeError)
    assert JETON_FACTICE not in str(erreur.value)
    assert JETON_FACTICE not in repr(erreur.value)


def test_dernier_modele_repondu_est_strictement_le_modele_du_service(monkeypatch):
    poster = MagicMock(
        side_effect=[
            reponse(
                {
                    "model": "meterless/fast",
                    "choices": [{"message": {"content": "première réponse"}}],
                }
            ),
            reponse({"choices": [{"message": {"content": "deuxième réponse"}}]}),
        ]
    )
    monkeypatch.setattr(llm.requests, "post", poster)
    client = llm.MeterlessLLM(Configuration(meterless_runtime_token=JETON_FACTICE))

    client.completer("premier", "hypotheses")
    assert client.dernier_modele_repondu == "meterless/fast"
    client.completer("deuxième", "verdict")
    assert client.dernier_modele_repondu is None


@pytest.mark.parametrize(
    "connexion,code",
    [
        ({"statut": "ok", "modeles": ["meterless/auto"], "message": "OK"}, 0),
        ({"statut": "non_autorise", "modeles": [], "message": "Refus"}, 2),
        ({"statut": "injoignable", "modeles": [], "message": "Absent"}, 3),
        ({"statut": "erreur", "modeles": [], "message": "Erreur"}, 1),
    ],
)
def test_check_meterless_codes_statut(monkeypatch, capsys, connexion, code):
    from tests.test_scripts import charger_script

    script = charger_script("check_meterless")
    client = MagicMock()
    client.tester_connexion.return_value = connexion
    monkeypatch.setattr(script, "charger", lambda: Configuration())
    monkeypatch.setattr(script, "MeterlessLLM", lambda configuration: client)

    assert script.main() == code
    assert JETON_FACTICE not in capsys.readouterr().out


def test_check_meterless_sans_chat(monkeypatch):
    from tests.test_scripts import charger_script

    script = charger_script("check_meterless")
    client = MagicMock()
    client.tester_connexion.return_value = {
        "statut": "ok",
        "modeles": ["meterless/auto"],
        "message": "Connexion OK.",
    }
    monkeypatch.setattr(script, "charger", lambda: Configuration())
    monkeypatch.setattr(script, "MeterlessLLM", lambda configuration: client)

    assert script.main(["--sans-chat"]) == 0
    client.completer.assert_not_called()


@pytest.mark.parametrize("statut", [401, 403])
def test_exception_auth_ne_contient_pas_le_jeton(monkeypatch, statut):
    monkeypatch.setattr(
        llm.requests, "post", MagicMock(return_value=reponse({}, statut))
    )
    client = llm.MeterlessLLM(Configuration(meterless_runtime_token=JETON_FACTICE))

    with pytest.raises(llm.MeterlessNonAutorise) as erreur:
        client.completer("bonjour", "detective")

    assert isinstance(erreur.value, RuntimeError)
    assert JETON_FACTICE not in str(erreur.value)
    assert JETON_FACTICE not in repr(erreur.value)
