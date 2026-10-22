import pytest
import requests


@pytest.fixture(autouse=True)
def environnement_local(monkeypatch):
    for nom, valeur in (
        ("LLM_PROVIDER", "fake"),
        ("SOURCE_PROVIDER", "fichier"),
        ("MEMOIRE_PROVIDER", "json"),
        ("SEUIL_ABSTENTION", "0.6"),
        ("MAX_TOURS_ENQUETE", "6"),
    ):
        monkeypatch.setenv(nom, valeur)

    def interdire_reseau(*args, **kwargs):
        raise AssertionError("Appel réseau interdit dans les tests.")

    monkeypatch.setattr(requests.sessions.Session, "request", interdire_reseau)
