"""Rêve H-MEM : propositions contrôlées, sans matérialisation automatique."""

from .embeddings import cosine, jaccard
from .modeles import DEFAULTS, DreamProposal


class DreamingService:
    def __init__(self, store, ledger, clock, actor="system"):
        self.store, self.ledger, self.clock, self.actor = store, ledger, clock, actor
        self._proposals = {}
        self._seq = 0

    def _relatedness(self, a, b):
        return (
            0.5 * max(0, cosine(a.embedding, b.embedding))
            + 0.2 * jaccard(a.tags, b.tags)
            + 0.1 * (a.domain == b.domain)
            + 0.2 * jaccard(a.entities, b.entities)
        )

    def _clusters(self):
        records = self.store.all()
        used, clusters = set(), []
        for seed in records:
            if seed.id in used:
                continue
            group = [seed]
            for other in records:
                if other.id != seed.id and other.id not in used and self._relatedness(seed, other) >= DEFAULTS["dreamRelatedness"]:
                    group.append(other)
            if len(group) >= DEFAULTS["dreamClusterMinSize"]:
                used.update(memory.id for memory in group)
                clusters.append(group)
        return clusters

    def dream(self):
        created = []

        def propose(type_proposition, content, lineage, suggested=None):
            self._seq += 1
            proposal = DreamProposal(
                f"dream-{self._seq}", type_proposition, content, lineage,
                suggestedDomain=suggested,
            )
            self._proposals[proposal.id] = proposal
            created.append(proposal)
            self.ledger.log(proposal.id, "dream_proposed", self.actor,
                            details={"type": type_proposition, "derivedFrom": lineage})

        for cluster in self._clusters()[:DEFAULTS["maxDreamProposalsPerType"]]:
            domains = "/".join(sorted({memory.domain for memory in cluster}))
            propose("insight",
                    f"Across {len(cluster)} related memories ({domains}): "
                    + " + ".join(memory.content for memory in cluster),
                    [memory.id for memory in cluster])
        domain_count = 0
        for memory in self.store.all():
            if domain_count >= DEFAULTS["maxDreamProposalsPerType"]:
                break
            suggestion = self.store.classify_domain(memory.content, memory.tags, memory.type)
            if memory.domain == "general" and suggestion != "general":
                propose("domain", f'Reclassify "{memory.content[:60]}" as {suggestion}',
                        [memory.id], suggestion)
                domain_count += 1
        preferences = [
            memory for memory in self.store.all()
            if memory.type in ("preference", "personal") or "event:user_correction" in memory.tags
        ]
        by_domain = {}
        for memory in preferences:
            by_domain.setdefault(memory.domain, []).append(memory)
        for domain, memories in by_domain.items():
            if len(memories) >= 2:
                propose("invariant", f"Stable pattern in {domain}: "
                        + "; ".join(memory.content for memory in memories),
                        [memory.id for memory in memories])
        return created

    def pending(self):
        return [item for item in self._proposals.values() if item.status == "proposed"]

    def approve(self, proposal_id):
        proposal = self._pending(proposal_id)
        proposal.status = "approved"
        self.ledger.log(proposal_id, "dream_approved", self.actor, details={"type": proposal.type})
        if proposal.type == "domain":
            for memory_id in proposal.derivedFrom:
                memory = self.store.get(memory_id)
                if memory and proposal.suggestedDomain:
                    self.store.update(memory_id, {"domain": proposal.suggestedDomain})
            return None
        return self.store.add({
            "content": proposal.content,
            "type": "factual" if proposal.type == "insight" else "preference",
            "layer": "long_term", "tags": [f"dream:{proposal.type}"], "confidence": 0.75,
            "source": "dreaming",
            "provenance": {"origin": "dream", "learnedAt": self.clock(),
                           "label": f"approved {proposal.type} proposal"},
            "derivedFrom": proposal.derivedFrom,
        })

    def reject(self, proposal_id, reason=None):
        proposal = self._pending(proposal_id)
        proposal.status = "rejected"
        self.ledger.log(proposal_id, "dream_rejected", self.actor,
                        details={"type": proposal.type, "reason": reason})

    def _pending(self, proposal_id):
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.status != "proposed":
            raise ValueError(f"H-MEM: aucune proposition en attente {proposal_id}")
        return proposal
