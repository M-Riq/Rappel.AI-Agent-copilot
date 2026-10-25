"""Recherche hybride et réinjection traçable H-MEM."""

import math
from collections import defaultdict

from .embeddings import cosine, embed, jaccard, tokenize
from .modeles import DEFAULTS, clamp01

POIDS_COUCHE = {"long_term": 1.0, "working": 0.8, "short_term": 0.6}


class MemoryRetrievalService:
    def __init__(self, store, ledger, clock, actor="system"):
        self.store, self.ledger, self.clock, self.actor = store, ledger, clock, actor

    def scoreOne(self, memory, query):
        return self._score(memory, embed(query), tokenize(query)[:8],
                           self.store.extract_entities(query),
                           self.store.classify_domain(query, [], "general"))

    def _score(self, memory, query_embedding, query_tokens, query_entities, query_domain):
        semantic = clamp01(cosine(query_embedding, memory.embedding))
        keyword = jaccard(query_tokens, tokenize(memory.content))
        tag = jaccard(query_tokens, [value.lower() for value in memory.tags])
        domain = float(memory.domain == query_domain)
        entity = jaccard(query_entities, memory.entities)
        layer = POIDS_COUCHE[memory.layer]
        age_days = (self.clock() - memory.lastAccessed) / 86_400_000
        recency = math.exp(-age_days / DEFAULTS["recencyDays"])
        superseded = float(bool(memory.supersededBy))
        brut = (
            0.35 * semantic + 0.20 * keyword + 0.10 * tag + 0.10 * domain
            + 0.15 * entity + 0.05 * layer + 0.05 * recency - 0.20 * superseded
        )
        return clamp01(brut) * memory.confidence

    def query(self, query, opts=None):
        opts = opts or {}
        query_embedding = embed(query)
        query_tokens = tokenize(query)[:8]
        query_entities = self.store.extract_entities(query)
        query_domain = self.store.classify_domain(query, [], "general")
        threshold = opts.get("threshold", DEFAULTS["retrievalThreshold"])
        top_n = opts.get("topN", opts.get("top_n", DEFAULTS["retrievalTopN"]))
        scores = []
        for memory in self.store.all():
            if opts.get("chatId") and memory.chatId and memory.chatId != opts["chatId"]:
                continue
            relevance = self._score(memory, query_embedding, query_tokens, query_entities, query_domain)
            if relevance >= threshold:
                scores.append({"memory": memory, "relevance": relevance})
        scores.sort(key=lambda item: (-item["relevance"], item["memory"].id))
        selected = scores[:top_n]
        for item in selected:
            memory = item["memory"]
            self.store.touch(memory.id, query=query)
        strategy = (
            "minimal" if len(selected) <= 1
            else "personal" if any(row["memory"].type in ("personal", "preference") for row in selected)
            else "comprehensive"
        )
        return {
            "memories": selected,
            "context": self.formatReinjection(selected),
            "trace": {
                "retrievalReason": f'hybrid ranking for "{query}" (domain: {query_domain})',
                "strategy": strategy,
                "scores": {row["memory"].id: round(row["relevance"], 4) for row in selected},
            },
        }

    def formatReinjection(self, items):
        if not items:
            return ""
        grouped = defaultdict(list)
        for item in items:
            grouped[item["memory"].domain].append(item)
        lines = ["[MEMORY CONTEXT]"]
        for domain in sorted(grouped):
            lines.append(f"## {domain}")
            for item in grouped[domain]:
                memory = item["memory"]
                marker = {"long_term": "LT", "working": "WK", "short_term": "ST"}[memory.layer]
                entities = f" (entities: {', '.join(memory.entities)})" if memory.entities else ""
                lines.append(f"- [{marker}] {memory.content}{entities} <{memory.id}>")
        lines.append("[/MEMORY CONTEXT]")
        return "\n".join(lines)

    def feedback(self, memory_id, kind):
        deltas = {"helpful": 0.05, "not_helpful": -0.03, "wrong": -0.20}
        if kind not in deltas:
            raise ValueError("Retour H-MEM inconnu.")
        memory = self.store.get(memory_id)
        if memory is None:
            raise ValueError(f"H-MEM: souvenir inconnu {memory_id}")
        return self.store.apply_feedback(memory_id, kind, deltas[kind])


def contexte_recherche(resultat):
    """Bloc de réinjection explicitement marqué comme piste à revérifier."""
    contexte = resultat.get("context", "")
    if not contexte:
        return ""
    return (
        "[MÉMOIRE H-MEM À REVÉRIFIER — ce souvenir n'est pas une preuve. "
        "Exécuter et citer les requêtes SQL actuelles avant toute conclusion.]\n"
        f"{contexte}"
    )
