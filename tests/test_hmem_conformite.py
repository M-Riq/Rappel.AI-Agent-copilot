"""Portage des vecteurs H-MEM officiels, distinct de la suite officielle TypeScript."""

import json
from pathlib import Path

import pytest

from rappel.hmem import HMEM

VECTEURS = json.loads(
    (Path(__file__).parent / "fixtures" / "hmem" / "vectors.json").read_text(encoding="utf-8")
)
JOUR = 24 * 60 * 60 * 1000


def test_vecteurs_validation_enregistrement():
    for vector in VECTEURS["sections"]["record-validation"]:
        moteur = HMEM(clock=lambda: VECTEURS["clock"])
        try:
            rejet = False
            try:
                moteur.add(vector["input"])
            except (ValueError, TypeError):
                rejet = True
            assert rejet == (vector["expect"] == "reject"), vector["id"]
        finally:
            moteur.close()


def test_vecteurs_deltas_feedback():
    for vector in VECTEURS["sections"]["feedback-deltas"]:
        moteur = HMEM(clock=lambda: VECTEURS["clock"])
        try:
            souvenir = moteur.add({
                "content": "feedback target record",
                "confidence": vector["start"], "source": "conformance",
            })
            acces = souvenir.accessCount
            moteur.feedback(souvenir.id, vector["kind"])
            actual = moteur.store.get(souvenir.id)
            assert actual.confidence == pytest.approx(vector["expectConfidence"]), vector["id"]
            if "expectAccessDelta" in vector:
                assert actual.accessCount - acces == vector["expectAccessDelta"], vector["id"]
            if vector.get("expectTag"):
                assert vector["expectTag"] in actual.tags
        finally:
            moteur.close()


def test_vecteurs_ordre_classement():
    for vector in VECTEURS["sections"]["ranking-order"]:
        moteur = HMEM(clock=lambda: VECTEURS["clock"])
        try:
            by_key = {}
            for row in vector["records"]:
                by_key[row["key"]] = moteur.add({
                    "content": row["content"], "confidence": row["confidence"],
                    "layer": row["layer"], "source": "conformance",
                })
            for row in vector["records"]:
                if row.get("supersededByKey"):
                    by_key[row["key"]].supersededBy = by_key[row["supersededByKey"]].id
            answer = moteur.query(vector["query"], {"threshold": 0, "topN": 10})
            order = [row["memory"].id for row in answer["memories"]]
            expected = [by_key[key].id for key in vector["expectOrder"]]
            for index in range(len(expected) - 1):
                assert order.index(expected[index]) < order.index(expected[index + 1]), vector["id"]
        finally:
            moteur.close()


def test_vecteurs_garde_fous_sommeil():
    vector = VECTEURS["sections"]["sleep-guardrails"][0]
    clock = [VECTEURS["clock"]]
    moteur = HMEM(clock=lambda: clock[0])
    try:
        by_key = {
            row["key"]: moteur.add({
                "content": row["content"], "source": row["source"],
                **({"derivedFrom": row["derivedFrom"]} if row.get("derivedFrom") else {}),
            })
            for row in vector["records"]
        }
        clock[0] += vector["ageDays"] * JOUR
        ids_before = sorted(row.id for row in moteur.store.all())
        plan = moteur.sleep.preview()
        assert sorted(row.id for row in moteur.store.all()) == ids_before
        for key in vector["expectArchived"]:
            assert by_key[key].id in plan["toArchive"]
        for key in vector["expectProtected"]:
            assert by_key[key].id not in plan["toArchive"]
        report = moteur.sleep.execute(plan)
        for key in vector["expectProtected"]:
            assert moteur.store.get(by_key[key].id) is not None
        assert report["backupId"]
        moteur.sleep.restore(report["backupId"])
        assert moteur.store.get(by_key["plain"].id) is not None
    finally:
        moteur.close()


def test_vecteurs_porte_conflit():
    vector = VECTEURS["sections"]["conflict-gate"][0]
    moteur = HMEM(clock=lambda: VECTEURS["clock"])
    try:
        first = moteur.add({**vector["a"], "source": "conformance"})
        second = moteur.add({**vector["b"], "source": "conformance"})
        found = moteur.conflicts.scan()
        assert found, vector["id"]
        outcome = moteur.conflicts.autoResolve(found[0])
        assert outcome in ("resolved", "queued")
        if outcome == "resolved":
            assert moteur.store.get(first.id).supersededBy or moteur.store.get(second.id).supersededBy
        assert moteur.store.get(first.id) and moteur.store.get(second.id)
    finally:
        moteur.close()


def test_vecteurs_journal_audit():
    vector = VECTEURS["sections"]["ledger-audit"][0]
    moteur = HMEM(clock=lambda: VECTEURS["clock"])
    try:
        souvenir = moteur.add({
            "content": "audited record", "confidence": vector["start"], "source": "conformance",
        })
        moteur.feedback(souvenir.id, vector["kind"])
        history = moteur.ledger.history(souvenir.id)
        assert [entry.action for entry in history] == vector["expectActions"]
        assert history[-1].previousState["confidence"] == pytest.approx(vector["expectPrev"])
        assert history[-1].newState["confidence"] == pytest.approx(vector["expectNew"])
    finally:
        moteur.close()


def test_vecteurs_frontiere_reve():
    vector = VECTEURS["sections"]["dream-boundary"][0]
    moteur = HMEM(clock=lambda: VECTEURS["clock"])
    try:
        for row in vector["records"]:
            moteur.add({**row, "source": "conformance"})
        count_before = len(moteur.store.all())
        proposals = moteur.dreaming.dream()
        assert proposals
        assert len(moteur.store.all()) == count_before
        created = moteur.dreaming.approve(proposals[0].id)
        if created:
            assert created.layer == "long_term"
            assert created.derivedFrom
    finally:
        moteur.close()
