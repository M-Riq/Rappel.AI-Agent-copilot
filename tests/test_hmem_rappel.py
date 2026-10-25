"""Intégration H-MEM/Rappel : persistance, preuves SQL, P1/P2/P3 et erreurs."""

from pathlib import Path
import sqlite3

import pytest

from rappel.enquete import enqueter
from rappel.enquete.adaptateurs import LLMFaux, MemoireEnquete
from rappel.enquete.modeles import ContexteIncident, EnregistrementMemoire
from rappel.hmem import HMEM
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.interfaces.memoire_hmem import MemoireHMEM

RACINE = Path(__file__).resolve().parents[1]


def charger_contexte(nom):
    return ContexteIncident.model_validate_json(
        (RACINE / "fixtures" / f"contexte_{nom}.json").read_text(encoding="utf-8")
    )


def lancer(nom, memoire):
    return enqueter(
        charger_contexte(nom), memoire, LLMFaux(), AbstentionSeuil(), mode_preuves="sql",
    )


def test_incident_hmem_persiste_avec_provenance_et_sql(tmp_path):
    chemin = tmp_path / "hmem.sqlite"
    backend = MemoireHMEM(chemin)
    memoire = MemoireEnquete(backend)
    resultat = lancer("p1", memoire)
    assert resultat.correctif is not None
    record = next(m for m in backend.hmem.store.all() if m.source == "enquete:p1")
    assert record.type == "factual" and record.layer == "long_term"
    assert record.confidence == resultat.decision_abstention.confiance
    assert record.provenance.origin == "decision"
    assert any(ref.startswith("preuve_sql:q") and "SELECT" in ref for ref in record.provenance.refs)
    assert any(ref.startswith("cause:") for ref in record.provenance.refs)
    backend.hmem.close()

    relu = MemoireHMEM(chemin)
    try:
        souvenir = relu.lister()[0]
        assert souvenir.signature == charger_contexte("p1").signature
        assert souvenir.hypotheses_refutees
        assert relu.hmem.ledger.history(record.id)
    finally:
        relu.hmem.close()


def test_p2_retrouve_p1_par_hmem_tout_en_reexecutant_sql(tmp_path):
    backend = MemoireHMEM(tmp_path / "hmem.sqlite")
    memoire = MemoireEnquete(backend)
    try:
        p1 = lancer("p1", memoire)
        p2 = lancer("p2", memoire)
        assert not p1.decision_abstention.abstient and not p2.decision_abstention.abstient
        assert p2.correctif is not None
        assert backend.last_search_trace["retrieved"]
        assert p2.metriques.nb_ecartees_par_memoire > 0
        assert p2.metriques.nb_appels_llm < p1.metriques.nb_appels_llm
        assert p2.metriques.nb_evenements <= p1.metriques.nb_evenements * 0.6
        assert any(
            requete["executee"] and requete["nb_lignes"] > 0
            for requete in p2.requetes_sql
        )
    finally:
        backend.hmem.close()


def test_p3_s_abstient_malgre_souvenir_resolu_tres_similaire(tmp_path):
    backend = MemoireHMEM(tmp_path / "hmem.sqlite")
    memoire = MemoireEnquete(backend)
    try:
        lancer("p1", memoire)
        p3 = lancer("p3", memoire)
        assert p3.decision_abstention.abstient
        assert p3.correctif is None
        assert p3.decision_abstention.manque
    finally:
        backend.hmem.close()


def test_feedback_resultat_et_vider_conservent_le_journal(tmp_path):
    backend = MemoireHMEM(tmp_path / "hmem.sqlite")
    try:
        backend.ecrire_incident(
            EnregistrementMemoire(
                signature="sig:orders", cause_confirmee="cause SQL",
                hypotheses_refutees=[], correctif_essaye="patch",
                resultat="reussi", date="2026-10-25",
            ),
            incident_id="i-1", cause="cause SQL", confiance=0.8,
            preuves_sql=[{"id": "q1", "sql": "SELECT 1", "nb_lignes": 1, "erreur": None}],
        )
        memory_id = backend.hmem.store.all()[0].id
        assert backend.mettre_a_jour_resultat("sig:orders", "echoue")
        assert backend.hmem.store.get(memory_id).confidence == pytest.approx(0.6)
        assert "review" in backend.hmem.store.get(memory_id).tags
        assert backend.vider() == 1
        assert backend.hmem.store.get(memory_id) is None
        assert [entry.action for entry in backend.ledger.history(memory_id)] == [
            "create", "feedback", "update", "delete",
        ]
    finally:
        backend.hmem.close()


def test_memoire_hmem_indisponible_et_ecriture_echouee_sont_explicites(tmp_path):
    mauvais_parent = tmp_path / "fichier"
    mauvais_parent.write_text("pas un dossier", encoding="utf-8")
    with pytest.raises(RuntimeError, match="H-MEM SQLite"):
        MemoireHMEM(mauvais_parent / "hmem.sqlite")

    backend = MemoireHMEM(tmp_path / "ecriture.sqlite")
    try:
        backend.hmem.storage.executer(lambda db: db.execute(
            "CREATE TRIGGER echec_hmem BEFORE INSERT ON memoires_hmem "
            "BEGIN SELECT RAISE(ABORT, 'échec volontaire'); END"
        ))
        with pytest.raises(RuntimeError, match="H-MEM SQLite"):
            backend.hmem.add({"content": "test écriture", "source": "test"})
        assert backend.hmem.store.all() == []
        assert backend.ledger.all() == []
    finally:
        backend.hmem.close()


def test_prompt_hmem_garde_les_types_json_pendant_le_masquage(monkeypatch):
    from rappel.enquete.boucle_sql import construire_prompt
    from rappel.enquete.protocoles import lire_donnees

    monkeypatch.setenv("DEMO_API_KEY", "secret-value")
    prompt = construire_prompt(
        "verdict", "Consignes", {"incident_similaire": True, "label": "secret-value"},
    )
    donnees = lire_donnees(prompt)
    assert donnees["incident_similaire"] is True
    assert donnees["label"] == "[MASQUE]"
