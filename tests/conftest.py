import pytest
import requests


@pytest.fixture(autouse=True)
def environnement_local(monkeypatch, tmp_path):
    for nom, valeur in (
        ("LLM_PROVIDER", "fake"),
        ("SOURCE_PROVIDER", "fichier"),
        ("MEMOIRE_PROVIDER", "json"),
        ("SEUIL_ABSTENTION", "0.6"),
        ("MAX_TOURS_ENQUETE", "6"),
    ):
        monkeypatch.setenv(nom, valeur)
    # Le moteur lancé par l'API écrit sa mémoire : jamais dans data/ du dépôt pendant les tests.
    monkeypatch.setenv("MEMOIRE_JSON_PATH", str(tmp_path / "memoire.json"))
    # Pipeline de demo isole : jamais data/pipeline_demo du depot pendant les tests.
    monkeypatch.setenv("PIPELINE_DEMO_DIR", str(tmp_path / "pipeline_demo"))
    # Journal de la boucle des 4 agents : jamais data/ du depot pendant les tests.
    monkeypatch.setenv("BOUCLE_JOURNAL_PATH", str(tmp_path / "boucle_journal.jsonl"))
    # Relances réseau sans attente pendant les tests (la politique reste la même).
    monkeypatch.setenv("RESEAU_ATTENTE_BASE_S", "0")
    for nom in ("ACTION_REELLE_DEMO", "WAR_ROOM_ACTIVE"):
        monkeypatch.delenv(nom, raising=False)

    def interdire_reseau(*args, **kwargs):
        raise AssertionError("Appel réseau interdit dans les tests.")

    monkeypatch.setattr(requests.sessions.Session, "request", interdire_reseau)
