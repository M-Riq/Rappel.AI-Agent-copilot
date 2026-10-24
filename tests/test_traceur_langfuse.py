"""Liaison optionnelle au SDK Langfuse avec de faux modules v2 et v3 (aucun réseau)."""

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from rappel.enquete import enqueter, traceur
from rappel.enquete.adaptateurs import LLMFaux, MemoireEnquete
from rappel.enquete.modeles import ContexteIncident
from rappel.interfaces.abstention import AbstentionSeuil

CLE_PUBLIQUE = "pk-lf-" + "factice-publique"
CLE_SECRETE = "sk-lf-" + "factice-secrete"


@pytest.fixture(autouse=True)
def isoler_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", CLE_PUBLIQUE)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", CLE_SECRETE)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.delenv("LANGFUSE_ENVOYER_TEXTES", raising=False)
    monkeypatch.setattr(traceur, "_avertissement_configuration_emis", False)


class Objet:
    def __init__(self, journal, nom):
        self.journal, self.nom = journal, nom

    def update(self, **options):
        self.journal.append((self.nom, "update", options))

    def end(self):
        self.journal.append((self.nom, "end", {}))

    def event(self, **options):
        self.journal.append((self.nom, "event", options))

    def create_event(self, **options):
        self.journal.append((self.nom, "create_event", options))


def module_v3(journal, options_recues):
    class Langfuse:
        def __init__(self, **options):
            options_recues.update(options)

        def start_span(self, **options):
            journal.append(("client", "start_span", options))
            return Objet(journal, "span")

        def flush(self):
            journal.append(("client", "flush", {}))
    return SimpleNamespace(Langfuse=Langfuse)


def module_v2(journal, options_recues):
    class Langfuse:
        def __init__(self, **options):
            options_recues.update(options)

        def trace(self, **options):
            journal.append(("client", "trace", options))
            return Objet(journal, "trace")

        def flush(self):
            journal.append(("client", "flush", {}))
    return SimpleNamespace(Langfuse=Langfuse)


def contexte():
    chemin = Path(__file__).resolve().parents[1] / "fixtures" / "contexte_p1.json"
    return ContexteIncident.model_validate_json(chemin.read_text(encoding="utf-8"))


def enquete(tmp_path, suivi):
    return enqueter(contexte(), MemoireEnquete.json(tmp_path / "m.json"), LLMFaux(), AbstentionSeuil(), traceur=suivi)


@pytest.mark.parametrize("fabrique, version, methode_debut, methode_evenement", [
    (module_v3, "v3", "start_span", "create_event"),
    (module_v2, "v2", "trace", "event"),
])
def test_cycle_complet_sdk(monkeypatch, tmp_path, caplog, fabrique, version, methode_debut, methode_evenement):
    journal, options = [], {}
    monkeypatch.setattr(traceur.importlib, "import_module", lambda nom: fabrique(journal, options))
    with caplog.at_level(logging.INFO):
        suivi = traceur.creer_traceur()
    assert isinstance(suivi, traceur.TraceurLangfuse)
    assert suivi._client.version == version
    assert options == {"public_key": CLE_PUBLIQUE, "secret_key": CLE_SECRETE}
    resultat = enquete(tmp_path, suivi)
    assert suivi.nb_erreurs == 0
    assert journal[0][1] == methode_debut and journal[0][2]["name"] == f"enquete {contexte().incident_id}"
    evenements = [entree for entree in journal if entree[1] == methode_evenement]
    assert len(evenements) == len(resultat.evenements)
    assert journal[-1] == ("client", "flush", {})
    assert any(entree[1] == "update" and "metriques" in entree[2]["output"] for entree in journal)
    assert (("span", "end", {}) in journal) == (version == "v3")
    assert CLE_SECRETE not in caplog.text and CLE_PUBLIQUE not in caplog.text


def test_hote_transmis_si_defini(monkeypatch):
    monkeypatch.setenv("LANGFUSE_HOST", "http://127.0.0.1:3000")
    journal, options = [], {}
    monkeypatch.setattr(traceur.importlib, "import_module", lambda nom: module_v3(journal, options))
    traceur.creer_traceur()
    assert options["host"] == "http://127.0.0.1:3000"


def test_v3_sans_create_event_utilise_un_span_enfant(monkeypatch, tmp_path):
    journal, options = [], {}

    class SpanSansEvenement:
        def update(self, **options):
            journal.append(("span", "update", options))

        def end(self):
            journal.append(("span", "end", {}))

        def start_span(self, **options):
            journal.append(("span", "start_span", options))
            return Objet(journal, "enfant")

    module = module_v3(journal, options)
    module.Langfuse.start_span = lambda self, **o: SpanSansEvenement()
    monkeypatch.setattr(traceur.importlib, "import_module", lambda nom: module)
    suivi = traceur.creer_traceur()
    resultat = enquete(tmp_path, suivi)
    assert suivi.nb_erreurs == 0
    assert sum(1 for entree in journal if entree[:2] == ("enfant", "end")) == len(resultat.evenements)


def test_constructeur_en_echec_sans_fuite(monkeypatch, caplog):
    class Langfuse:
        def __init__(self, **options):
            raise RuntimeError(f"auth refusée pour {options['secret_key']}")
    monkeypatch.setattr(traceur.importlib, "import_module", lambda nom: SimpleNamespace(Langfuse=Langfuse))
    with caplog.at_level(logging.WARNING):
        assert isinstance(traceur.creer_traceur(), traceur.TraceurNul)
    assert len(caplog.records) == 1
    assert CLE_SECRETE not in caplog.text


def test_sdk_non_reconnu_nul(monkeypatch, caplog):
    class Langfuse:
        def __init__(self, **options):
            pass
    monkeypatch.setattr(traceur.importlib, "import_module", lambda nom: SimpleNamespace(Langfuse=Langfuse))
    with caplog.at_level(logging.WARNING):
        assert isinstance(traceur.creer_traceur(), traceur.TraceurNul)
    assert "inactif" in caplog.text


def test_envoi_en_panne_enquete_identique(monkeypatch, tmp_path, caplog):
    journal, options = [], {}
    module = module_v3(journal, options)

    def panne(self, **o):
        raise ConnectionError(f"https://{CLE_PUBLIQUE}:{CLE_SECRETE}@langfuse.invalid")
    module.Langfuse.start_span = panne
    monkeypatch.setattr(traceur.importlib, "import_module", lambda nom: module)
    reference = enquete(tmp_path / "ref", traceur.TraceurNul())
    suivi = traceur.creer_traceur()
    with caplog.at_level(logging.WARNING):
        resultat = enquete(tmp_path / "avec", suivi)
    assert resultat.correctif == reference.correctif
    assert [e.type for e in resultat.evenements] == [e.type for e in reference.evenements]
    assert suivi.nb_erreurs >= 1
    assert CLE_SECRETE not in caplog.text and "langfuse.invalid" not in caplog.text
