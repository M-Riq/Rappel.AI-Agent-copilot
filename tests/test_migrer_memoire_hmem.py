"""Tests de migration idempotente vers H-MEM."""

from scripts.migrer_memoire_vers_hmem import migrer
from rappel.hmem import HMEM


def test_migration_simule_sans_ecriture_puis_importe_sans_supprimer_source(tmp_path):
    destination = tmp_path / "hmem.sqlite"
    incidents = [{"signature": "sig:orders", "cause_confirmee": "colonne renommée"}]
    hypotheses = [{"texte": "cause écartée", "statut": "refutee"}]

    simulation = migrer(incidents, hypotheses, destination, simuler=True)
    assert simulation["a_migrer"] == 2
    assert not destination.exists()

    rapport = migrer(incidents, hypotheses, destination)
    assert rapport["importes"] == 2
    assert incidents == [{"signature": "sig:orders", "cause_confirmee": "colonne renommée"}]
    assert hypotheses == [{"texte": "cause écartée", "statut": "refutee"}]

    repetition = migrer(incidents, hypotheses, destination)
    assert repetition["deja_presentes"] == 2
    assert repetition["a_migrer"] == 0

    moteur = HMEM(persist_path=destination)
    try:
        memoires = moteur.store.all()
        assert len(memoires) == 2
        assert all(memoire.provenance.origin == "migration" for memoire in memoires)
        assert all(memoire.type == "general" and memoire.confidence <= 0.3 for memoire in memoires)
        assert any(any("legacy_record" in tag for tag in memoire.tags) for memoire in memoires)
        assert any(any("legacy_hypothesis" in tag for tag in memoire.tags) for memoire in memoires)
    finally:
        moteur.close()
