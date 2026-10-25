"""Détection et résolution prudente des contradictions."""

import re

from .embeddings import cosine
from .modeles import DEFAULTS, ConflictRecord, clamp01

PAIRES_OPPOSEES = (
    (r"\balways\b", r"\bnever\b"),
    (r"\benable[ds]?\b", r"\bdisable[ds]?\b"),
    (r"\bprefer[s]?\b", r"\bavoid[s]?|\bdislike[s]?\b"),
    (r"\bon\b", r"\boff\b"),
    (r"\byes\b", r"\bno\b"),
)


class ConflictDetectionService:
    def __init__(self, store, ledger, clock, actor="system"):
        self.store, self.ledger, self.clock, self.actor = store, ledger, clock, actor
        self._conflicts = []
        self._seq = 0

    def scan(self):
        found = []
        records = self.store.all()
        for index, first in enumerate(records):
            for second in records[index + 1:]:
                conflict = self._check(first, second)
                if conflict:
                    found.append(conflict)
                    self._conflicts.append(conflict)
                    self.ledger.log(first.id, "conflict_detected", self.actor, details={
                        "with": second.id, "reason": conflict.reason, "confidence": conflict.confidence,
                    })
        return found

    def _check(self, first, second):
        if (
            first.supersedes == second.id or second.supersedes == first.id
            or first.supersededBy == second.id or second.supersededBy == first.id
        ):
            return None
        similarity = cosine(first.embedding, second.embedding)
        reason, confidence = "", 0.0
        for a, b in PAIRES_OPPOSEES:
            if (re.search(a, first.content, re.I) and re.search(b, second.content, re.I)) or (
                re.search(b, first.content, re.I) and re.search(a, second.content, re.I)
            ):
                if similarity > 0.5:
                    reason = "opposing phrase pair on similar content"
                    confidence = 0.5 + 0.3 * similarity
                    break
        if not reason and similarity > 0.8:
            numbers_a = re.findall(r"\d+(?:\.\d+)?", first.content)
            numbers_b = re.findall(r"\d+(?:\.\d+)?", second.content)
            if numbers_a and numbers_b and numbers_a != numbers_b:
                reason = "high-similarity conflicting numeric values"
                confidence = 0.55 + 0.25 * similarity
        if not reason:
            return None
        if set(first.entities) & set(second.entities):
            confidence += 0.1
        if first.domain == second.domain:
            confidence += 0.05
        if second.id in first.relatedTo:
            confidence -= 0.1
        self._seq += 1
        return ConflictRecord(
            f"conflict-{self._seq}", first.id, second.id, reason, clamp01(confidence)
        )

    def autoResolve(self, conflict):
        first, second = self.store.get(conflict.memoryA), self.store.get(conflict.memoryB)
        if first is None or second is None:
            raise ValueError("Souvenir absent pendant la résolution du conflit.")

        def score(memory):
            return (
                0.3 * (memory.lastAccessed / max(1, self.clock()))
                + 0.2 * min(1, memory.accessCount / 10)
                + 0.25 * memory.confidence
                + 0.1 * min(1, len(memory.content) / 200)
                + 0.15 * (1 if memory.provenance.origin == "user_correction" else 0.5)
            )
        score_a, score_b = score(first), score(second)
        marge = abs(score_a - score_b) / max(score_a, score_b, 1e-12)
        confiance_decision = clamp01(conflict.confidence * 0.6 + marge * 0.8)
        if confiance_decision < DEFAULTS["conflictAutoResolveGate"]:
            return "queued"
        gagnant, perdant = (first, second) if score_a >= score_b else (second, first)
        before = perdant.vers_dict()
        perdant.supersededBy = gagnant.id
        conflict.resolved = True
        conflict.resolution = "keep_a" if gagnant.id == first.id else "keep_b"
        self.ledger.log(
            perdant.id, "conflict_resolved", self.actor,
            previousState=before, newState=perdant.vers_dict(),
            details={"conflictId": conflict.id, "winner": gagnant.id,
                     "decisionConfidence": round(confiance_decision, 3)},
        )
        self.store.persist()
        return "resolved"

    def resolve(self, conflict_id, resolution, winner_id=None):
        conflict = next((c for c in self._conflicts if c.id == conflict_id), None)
        if conflict is None:
            raise ValueError(f"Conflit H-MEM inconnu : {conflict_id}")
        if resolution not in ("keep_a", "keep_b", "keep_both", "merge", "delete_both"):
            raise ValueError("Stratégie de conflit H-MEM inconnue.")
        # Les deux souvenirs sont conservés ; une résolution explicite marque le perdant.
        winner_id = winner_id or (
            conflict.memoryA if resolution == "keep_a" else conflict.memoryB if resolution == "keep_b" else None
        )
        if winner_id:
            loser_id = conflict.memoryB if winner_id == conflict.memoryA else conflict.memoryA
            loser = self.store.get(loser_id)
            if loser:
                before = loser.vers_dict()
                loser.supersededBy = winner_id
                self.ledger.log(loser_id, "conflict_resolved", self.actor,
                                previousState=before, newState=loser.vers_dict(),
                                details={"conflictId": conflict.id, "winner": winner_id})
        conflict.resolved, conflict.resolution = True, resolution
        if winner_id:
            self.store.persist()
        return conflict

    def queue(self):
        return [conflict for conflict in self._conflicts if not conflict.resolved]
