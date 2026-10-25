"""Fusion de main (diagnostics Meterless Runtime) et du durcissement : les deux mondes ensemble.

Faux Nightshift local sur 127.0.0.1 : NON une preuve sponsor.
"""

from dataclasses import replace

import pytest

from rappel.config import Configuration
from rappel.enquete.adaptateurs import LLMEnquete
from rappel.interfaces import llm
from rappel.interfaces.client_http import ErreurService
from scripts import recette_reelle
from tests.faux_services import FauxNightshift, reseau_local
from tests.faux_services.serveur import ServeurLocal
from tests.test_scripts import charger_script


@pytest.fixture
def nightshift(monkeypatch):
    with reseau_local(monkeypatch), FauxNightshift() as serveur:
        yield serveur


def configuration(serveur, **options):
    return Configuration(llm_provider="meterless", meterless_base_url=serveur.url, meterless_runtime_token=serveur.jeton,
                         meterless_model_rapide="meterless/fast", meterless_model_profond="meterless/deep",
                         reseau_attente_base_s=0, **options)


def test_tester_connexion_contre_le_faux_nightshift(nightshift):
    resultat = llm.MeterlessLLM(configuration(nightshift)).tester_connexion()
    assert resultat["statut"] == "ok" and "meterless/fast" in resultat["modeles"]
    assert [requete["chemin"] for requete in nightshift.requetes] == ["/v1/models"]  # aucun appel facturé
    faux = llm.MeterlessLLM(replace(configuration(nightshift), meterless_runtime_token="jeton-" + "faux-fusion"))
    refus = faux.tester_connexion()
    assert refus["statut"] == "non_autorise" and "jeton" not in refus["modeles"]
    avant = len(nightshift.requetes)
    demo = llm.MeterlessLLM(replace(configuration(nightshift), meterless_runtime_token="rt-local")).tester_connexion()
    assert demo["statut"] == "non_autorise" and len(nightshift.requetes) == avant  # rt-local refusé localement
    for resultat in (refus, demo):
        assert nightshift.jeton not in resultat["message"] and "faux-fusion" not in resultat["message"]


def test_tester_connexion_serveur_arrete_injoignable(monkeypatch):
    with reseau_local(monkeypatch):
        with FauxNightshift() as serveur:
            config = configuration(serveur, reseau_relances=0)
        resultat = llm.MeterlessLLM(config).tester_connexion()
    assert resultat["statut"] == "injoignable" and "indisponible" in resultat["message"]


def test_exceptions_de_main_portent_la_categorie_du_client(nightshift):
    config = replace(configuration(nightshift), meterless_runtime_token="jeton-" + "faux-fusion")
    with pytest.raises(llm.MeterlessNonAutorise) as erreur:
        llm.MeterlessLLM(config).completer("p", "hypotheses")
    assert isinstance(erreur.value, ErreurService) and erreur.value.categorie == "non_autorise"
    assert "jeton Runtime Nightshift incorrect" in str(erreur.value) and "faux-fusion" not in repr(erreur.value)


@pytest.mark.parametrize("arguments, attendu", [(["--sans-chat"], 0), ([], 0)])
def test_check_meterless_contre_le_faux_nightshift_sans_fuite(nightshift, monkeypatch, capsys, arguments, attendu):
    script = charger_script("check_meterless")
    monkeypatch.setattr(script, "charger", lambda: configuration(nightshift))
    assert script.main(arguments) == attendu
    sortie = capsys.readouterr().out
    assert "Statut de connexion : ok" in sortie and "meterless/fast (mode de routage Meterless)" in sortie
    assert nightshift.jeton not in sortie
    chats = [requete for requete in nightshift.requetes if requete["chemin"] == "/v1/chat/completions"]
    assert len(chats) == (0 if arguments else 1)
    if not arguments:
        assert "Modèle effectif : nvidia/nemotron-faux-local-rapide" in sortie


def test_check_meterless_401_reste_non_autorise(nightshift, monkeypatch, capsys):
    script = charger_script("check_meterless")
    monkeypatch.setattr(script, "charger",
                        lambda: replace(configuration(nightshift), meterless_runtime_token="jeton-" + "faux-fusion"))
    assert script.main(["--sans-chat"]) == 2
    sortie = capsys.readouterr().out
    assert "non_autorise" in sortie and "injoignable" not in sortie and "faux-fusion" not in sortie


class Redirection(ServeurLocal):
    """Renvoie une redirection vers un autre hôte local : le jeton ne doit jamais l'atteindre."""

    def __init__(self, cible):
        self.cible = cible

    def traiter(self, methode, chemin, entetes, corps):
        return 302, {"redirection": True}, {"Location": self.cible + chemin}


def test_redirection_refusee_sans_envoi_du_jeton(nightshift):
    with Redirection(nightshift.url) as relais:
        config = replace(configuration(nightshift), meterless_base_url=relais.url)
        resultat = llm.MeterlessLLM(config).tester_connexion()
        assert resultat["statut"] == "erreur" and "redirection refusée" in resultat["message"]
        with pytest.raises(ErreurService) as erreur:
            llm.MeterlessLLM(config).completer("p", "hypotheses")
        assert erreur.value.categorie == "redirection"
    assert nightshift.requetes == []  # la cible de la redirection n'a rien reçu


def test_normalisation_unique_la_plus_stricte():
    assert llm.normaliser_url_base("http://h:8088/v1/chat/completions") == "http://h:8088/v1"
    assert llm.url_meterless(Configuration(meterless_base_url="h:8088/v1/v1")) == "https://h:8088/v1"
    for url in ("file:///etc/passwd", "http://u:p@h/v1", "ftp://h/v1"):
        with pytest.raises(ValueError, match="METERLESS_BASE_URL"):
            llm.normaliser_url_base(url)


def test_a14_inchange_et_champs_du_durcissement(nightshift):
    nightshift.modeles_repondus = {"*": "meta/llama-faux-local"}
    config = configuration(nightshift)
    resultat = recette_reelle.case_a14(lambda: LLMEnquete(llm.MeterlessLLM(config), config))
    assert resultat["statut"] == "ECHOUE"
    assert "le routage a choisi meta/llama-faux-local : fixer la voie" in resultat["detail"]
    fournisseur = llm.MeterlessLLM(config)
    assert fournisseur.facture is True and fournisseur.client.politique.delai_lecture_s == 180.0
    fournisseur.completer("p", "hypotheses")
    assert fournisseur.dernier_modele_repondu == "meta/llama-faux-local"
