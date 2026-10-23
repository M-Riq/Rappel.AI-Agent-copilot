import logging
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from rappel.enquete.modeles import ContexteIncident, EvenementEnquete
from rappel.enquete import enqueter, graphe
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.enquete.adaptateurs import LLMFaux
from rappel.enquete.adaptateurs import MemoireEnquete
from rappel.enquete import traceur


@pytest.fixture(autouse=True)
def isoler_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for nom in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST",
                "LANGFUSE_ENVOYER_TEXTES", "WAR_ROOM_ACTIVE"):
        monkeypatch.delenv(nom, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "fake")


def charger(nom="contexte_p1.json"):
    chemin = Path(__file__).resolve().parents[1] / "fixtures" / nom
    return ContexteIncident.model_validate_json(chemin.read_text(encoding="utf-8"))


def lancer(tmp_path, suivi=None, nom="contexte_p1.json", callback=None):
    return enqueter(charger(nom), MemoireEnquete.json(tmp_path / "memoire.json"),
                    LLMFaux(), AbstentionSeuil(), traceur=suivi, sur_evenement=callback)


class ClientFaux:
    def __init__(self):
        self.appels = []

    def demarrer(self, donnees):
        self.appels.append(("demarrer", donnees))

    def evenement(self, donnees):
        self.appels.append(("evenement", donnees))

    def terminer(self, donnees):
        self.appels.append(("terminer", donnees))


def fixer_horloge(monkeypatch):
    monkeypatch.setattr(graphe, "maintenant", lambda: "2026-10-06T10:00:00Z")
    monkeypatch.setattr(graphe, "perf_counter", lambda: 42.0)


def evenement():
    return EvenementEnquete(incident_id="inc", etape=1, type="decision", acteur="systeme",
                           texte="texte prive", horodatage="2026-10-06T10:00:00Z")


def test_sans_cles_retourne_nul(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(traceur, "_avertissement_configuration_emis", False)
    with caplog.at_level(logging.WARNING):
        assert isinstance(traceur.creer_traceur(), traceur.TraceurNul)
        assert isinstance(traceur.creer_traceur(), traceur.TraceurNul)
    assert len(caplog.records) == 1


def test_client_erreurs_assainies_et_comptees(caplog):
    class Client:
        def demarrer(self, donnees):
            raise RuntimeError("secret-factice")
        evenement = demarrer
        terminer = demarrer
    suivi = traceur.TraceurLangfuse(Client())
    with caplog.at_level(logging.WARNING):
        suivi.demarrer("inc", "signature")
        suivi.evenement(evenement())
        suivi.terminer({"nb_evenements": 1}, "abstention")
    assert suivi.nb_erreurs == 3
    assert len(caplog.records) == 1
    assert "secret-factice" not in caplog.text


def test_textes_non_transmis(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENVOYER_TEXTES", "0")
    recus = []
    class Client:
        def evenement(self, donnees):
            recus.append(donnees)
    traceur.TraceurLangfuse(Client()).evenement(evenement())
    assert recus == [{"type": "decision", "acteur": "systeme", "etape": 1}]


def test_sans_cles_resultat_strictement_identique(tmp_path, monkeypatch):
    fixer_horloge(monkeypatch)
    reference = lancer(tmp_path / "reference")
    avec = lancer(tmp_path / "avec", traceur.creer_traceur())
    assert avec == reference


@pytest.mark.parametrize("nom", ["contexte_p1.json", "contexte_p3.json"])
def test_cycle_complet_et_confidentialite(tmp_path, nom):
    client = ClientFaux()
    suivi = traceur.TraceurLangfuse(client)
    recus = []
    resultat = lancer(tmp_path, suivi, nom=nom, callback=recus.append)
    assert recus == resultat.evenements
    assert client.appels[0] == ("demarrer", {
        "incident_id": charger(nom).incident_id, "signature": charger(nom).signature
    })
    milieu = client.appels[1:-1]
    assert len(milieu) == len(resultat.evenements)
    for (methode, donnees), element in zip(milieu, resultat.evenements):
        assert methode == "evenement"
        assert donnees == {"type": element.type, "acteur": element.acteur,
                           "etape": element.etape, "texte": element.texte}
    assert client.appels[-1] == ("terminer", {
        "metriques": asdict(resultat.metriques),
        "decision": "abstention" if resultat.decision_abstention.abstient else "correctif_propose",
    })
    assert suivi.nb_erreurs == 0


def test_client_en_panne_enquete_identique(tmp_path, monkeypatch, caplog):
    fixer_horloge(monkeypatch)
    reference = lancer(tmp_path / "reference")
    class Client:
        def demarrer(self, donnees):
            raise RuntimeError("cle-factice-secret-et-entetes")
        evenement = demarrer
        terminer = demarrer
    suivi = traceur.TraceurLangfuse(Client())
    with caplog.at_level(logging.WARNING):
        resultat = lancer(tmp_path / "avec", suivi)
    assert resultat == reference
    assert suivi.nb_erreurs == len(resultat.evenements) + 2
    assert len(caplog.records) == 1
    assert "cle-factice" not in caplog.text
    assert "entetes" not in caplog.text


def test_traceur_externe_en_panne_protege(tmp_path, monkeypatch, caplog):
    fixer_horloge(monkeypatch)
    class Externe:
        def demarrer(self, *arguments):
            raise RuntimeError("secret-factice")
        evenement = demarrer
        terminer = demarrer
    reference = lancer(tmp_path / "reference")
    suivi = traceur.proteger_traceur(Externe())
    with caplog.at_level(logging.WARNING):
        resultat = lancer(tmp_path / "avec", suivi)
    assert resultat == reference
    assert suivi.nb_erreurs == len(resultat.evenements) + 2
    assert len(caplog.records) == 1
    assert "secret-factice" not in caplog.text


def test_client_mutation_ne_modifie_pas_resultat(tmp_path, monkeypatch):
    fixer_horloge(monkeypatch)
    class Client:
        def demarrer(self, donnees):
            donnees.clear()
        evenement = demarrer
        terminer = demarrer
    assert lancer(tmp_path / "avec", traceur.TraceurLangfuse(Client())) == lancer(tmp_path / "reference")


def test_absence_sdk_avec_cles_sans_exception(monkeypatch, caplog):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public-factice")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret-factice")
    monkeypatch.setattr(traceur, "_avertissement_configuration_emis", False)
    def importer(nom):
        assert nom == "langfuse"
        raise ModuleNotFoundError("secret-factice")
    monkeypatch.setattr(traceur.importlib, "import_module", importer)
    with caplog.at_level(logging.WARNING):
        assert isinstance(traceur.creer_traceur(), traceur.TraceurNul)
    assert len(caplog.records) == 1
    assert "secret-factice" not in caplog.text


def test_sdk_present_mais_liaison_non_verifiee(monkeypatch, caplog):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public-factice")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret-factice")
    monkeypatch.setattr(traceur, "_avertissement_configuration_emis", False)
    monkeypatch.setattr(traceur.importlib, "import_module", lambda nom: SimpleNamespace())
    with caplog.at_level(logging.WARNING):
        assert isinstance(traceur.creer_traceur(), traceur.TraceurNul)
    assert len(caplog.records) == 1
    assert "public-factice" not in caplog.text and "secret-factice" not in caplog.text


def test_trace_terminee_sur_erreur_enquete(tmp_path):
    class LLMPanne(LLMFaux):
        def completer(self, prompt, role="detective"):
            raise RuntimeError("secret-panne")
    client = ClientFaux()
    with pytest.raises(RuntimeError, match="secret-panne"):
        enqueter(charger(), MemoireEnquete.json(tmp_path / "memoire.json"), LLMPanne(),
                 AbstentionSeuil(), traceur=traceur.TraceurLangfuse(client))
    assert client.appels[-1][1]["decision"] == "erreur"
    assert set(client.appels[-1][1]["metriques"]) == {"duree_ms"}
    assert "secret-panne" not in str(client.appels)
