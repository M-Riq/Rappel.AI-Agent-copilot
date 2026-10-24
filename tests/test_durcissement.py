"""Durcissement des adaptateurs réels (R4) : reformulation SQL unique, budget de session, prompts bornés,
journaux sans secret, arrêt propre sur Ctrl+C. Faux services locaux : NON une preuve sponsor."""

import json
import logging
import os

import pytest

from rappel.action import executeur
from rappel.config import Configuration
from rappel.enquete import boucle_sql
from rappel.enquete.adaptateurs import LLMBudgetEpuise, LLMEnquete, LLMFaux
from rappel.interfaces.llm import MeterlessLLM
from rappel.interfaces.run_sql import RunSqlZetaris
from rappel.pipeline_demo import empreinte_dossier, injecter
from rappel.securite import ecrire_texte_atomique
from scripts import boucle_agents
from scripts._essai_commun import executer_main
from tests.faux_services import FauxNightshift, FauxZetaris, reseau_local
from tests.faux_services.zetaris import contexte_fixture
from tests.test_audit_r1 import correctif

JETON = "jeton-" + "factice-durci-31"


@pytest.fixture
def local(monkeypatch):
    with reseau_local(monkeypatch):
        yield


def configuration_zetaris(serveur):
    return Configuration(source_provider="zetaris", zetaris_base_url=serveur.url + "/api/v1.0", zetaris_api_key=serveur.cle,
                         zetaris_org_id=serveur.org_id, reseau_attente_base_s=0)


def test_une_seule_reformulation_guidee_puis_succes(local):
    with FauxZetaris() as serveur:
        serveur.refuser_limit = True
        resultat = RunSqlZetaris(contexte_fixture("p1"), configuration_zetaris(serveur)).executer("SELECT template FROM logs")
        assert resultat.erreur is None and resultat.nb_lignes == 1
        assert resultat.requete.endswith("FETCH FIRST 50 ROWS ONLY")  # le SQL exécuté est celui renvoyé
        envoyes = [requete["corps"]["sql"] for requete in serveur.requetes]
        assert len(envoyes) == 2 and envoyes[0].endswith("LIMIT 50") and envoyes[1] == resultat.requete


def test_reformulation_echouee_abandon_propre(local):
    with FauxZetaris() as serveur:
        serveur.scenarios.extend(["syntaxe_limit", "syntaxe_limit"])
        resultat = RunSqlZetaris(contexte_fixture("p1"), configuration_zetaris(serveur)).executer("SELECT template FROM logs")
        assert resultat.erreur.startswith("erreur SQL : apres une reformulation : Zetaris : dialecte ou syntaxe SQL refusé")
        assert len(serveur.requetes) == 2 and serveur.cle not in resultat.erreur


def test_pas_de_reformulation_pour_une_table_inconnue(local):
    with FauxZetaris() as serveur:
        contexte = contexte_fixture("p1")
        resultat = RunSqlZetaris(contexte, configuration_zetaris(serveur)).executer("SELECT nom FROM colonnes_attendues")
        assert resultat.erreur is None
        serveur.contexte = {**contexte}
        resultat = RunSqlZetaris(contexte, configuration_zetaris(serveur))
        resultat.tables_autorisees = ("logs", "absente")
        sortie = resultat.executer("SELECT x FROM absente")
        assert "table inconnue" in sortie.erreur and len(serveur.requetes) == 2


def test_budget_de_session_avant_tout_appel_facture(local, monkeypatch):
    monkeypatch.setenv("LLM_MAX_APPELS_SESSION", "3")
    with FauxNightshift() as serveur:
        from rappel.config import charger
        configuration = Configuration(**{**charger().__dict__, "meterless_base_url": serveur.url + "/v1",
                                         "meterless_runtime_token": serveur.jeton})
        llm = LLMEnquete(MeterlessLLM(configuration), configuration)
        for _ in range(3):
            llm.completer("p", role="hypotheses")
        with pytest.raises(LLMBudgetEpuise, match="LLM_MAX_APPELS_SESSION"):
            LLMEnquete(MeterlessLLM(configuration), configuration).completer("p", role="conclusion")
        assert len(serveur.requetes) == 3
    faux = LLMFaux()
    for _ in range(5):  # le double hors réseau n'est pas facturé
        faux.completer("Role: hypotheses.\nDONNEES:\n{}", role="hypotheses")


def test_prompt_borne_et_sans_secret(monkeypatch):
    monkeypatch.setenv("ZETARIS_API_KEY", JETON)
    donnees = {"logs": [{"template": "x" * 100_000, "compte": 1}] * 200, "cle": f"fuite {JETON}"}
    prompt = boucle_sql.construire_prompt("hypotheses", "consignes", donnees)
    assert len(prompt) < boucle_sql.LONGUEUR_MAX_DONNEES_PROMPT + 1000
    assert JETON not in prompt and "[tronque]" in prompt


def test_journaux_sans_secret(monkeypatch, caplog):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", JETON)
    with caplog.at_level(logging.WARNING):
        logging.getLogger("rappel.interfaces.test").warning("echec avec %s et postgresql://u:%s@h/b", JETON, "mdp-factice")
    assert JETON not in caplog.text and "mdp-factice" not in caplog.text and "[MASQUE]" in caplog.text


def test_action_restauree_sur_ctrl_c(monkeypatch):
    injecter("p1")
    avant = empreinte_dossier()

    def interrompre(dossier=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(executeur, "executer_pipeline", interrompre)
    with pytest.raises(KeyboardInterrupt):
        executeur.executer_correctif(correctif())
    assert empreinte_dossier() == avant


def test_ecriture_atomique_resiste_a_l_interruption(tmp_path, monkeypatch):
    chemin = tmp_path / "rapport.json"
    chemin.write_text('{"ancien": true}', encoding="utf-8")

    def couper(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", couper)
    with pytest.raises(KeyboardInterrupt):
        ecrire_texte_atomique(chemin, json.dumps({"nouveau": "x" * 10_000}))
    assert json.loads(chemin.read_text(encoding="utf-8")) == {"ancien": True}
    assert [element.name for element in tmp_path.iterdir()] == ["rapport.json"]


def test_script_ctrl_c_code_130_sans_rapport(tmp_path, capsys):
    def interrompre():
        raise KeyboardInterrupt

    destination = tmp_path / "boucle.json"
    code = executer_main(lambda: boucle_agents.main(["--scenario", "p1", "--json", str(destination)],
                                                    fabrique_llm=interrompre))
    assert code == 130 and not destination.exists()
    assert "arrêt propre" in capsys.readouterr().out
