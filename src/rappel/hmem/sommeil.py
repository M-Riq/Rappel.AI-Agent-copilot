"""Cycle de sommeil minimal, avec aperçu, sauvegarde et restauration."""

from .embeddings import cosine
from .modeles import DEFAULTS

JOUR_MS = 86_400_000
SEUIL_HUB = 3


class SleepCycleService:
    def __init__(self, store, ledger, clock, actor="system"):
        self.store, self.ledger, self.clock, self.actor = store, ledger, clock, actor
        self._backup_seq = 0

    def preview(self):
        now = self.clock()
        all_records = self.store.all()
        references = set()
        for memory in all_records:
            references.update(memory.relatedTo)
            if memory.supersedes:
                references.add(memory.supersedes)
            references.update(memory.derivedFrom)
        consolidate = [
            m.id for m in all_records
            if m.layer == "short_term" and now - m.lastAccessed >= DEFAULTS["consolidationDays"] * JOUR_MS
        ]
        archive = []
        for memory in all_records:
            old = now - memory.timestamp >= DEFAULTS["archiveMinDays"] * JOUR_MS
            low_access = memory.accessCount <= DEFAULTS["archiveMaxAccess"]
            if not old or not low_access or memory.id in references:
                continue
            if memory.supersedes or memory.derivedFrom or len(memory.relatedTo) >= SEUIL_HUB:
                continue
            archive.append(memory.id)
        synthesize, used = [], set()
        for index, first in enumerate(all_records):
            if first.id in used:
                continue
            group = [first]
            for second in all_records[index + 1:]:
                if second.id not in used and cosine(first.embedding, second.embedding) >= DEFAULTS["synthesisCosine"]:
                    group.append(second)
            if len(group) >= 2:
                used.update(memory.id for memory in group)
                synthesize.append([memory.id for memory in group])
        return {"toConsolidate": consolidate, "toArchive": archive, "toSynthesize": synthesize}

    def execute(self, preview=None):
        plan = preview or self.preview()
        if not isinstance(plan, dict):
            raise ValueError("Aperçu H-MEM invalide.")
        valid = self.preview()
        # Un plan externe ne peut pas contourner les garde-fous d'archivage.
        archived = set(plan.get("toArchive", [])) & set(valid["toArchive"])
        consolidates = set(plan.get("toConsolidate", [])) & set(valid["toConsolidate"])
        synthesizes = plan.get("toSynthesize", [])
        self._backup_seq += 1
        backup_id = f"backup-{self._backup_seq}"
        snapshot = self.store.snapshot_all()
        self.store.stockage.executer(lambda db: db.execute(
            "INSERT INTO hmem_backups(id, donnees) VALUES (?, ?)",
            (backup_id, __import__("json").dumps(snapshot, ensure_ascii=False)),
        ))
        journal = [f"backup {backup_id} created"]
        for memory_id in consolidates:
            if self.store.get(memory_id):
                self.store.promote(memory_id, "long_term")
                self.ledger.log(memory_id, "sleep_consolidate", self.actor,
                                details={"from": "short_term", "to": "long_term"})
                journal.append(f"consolidated {memory_id}")
        for memory_id in archived:
            memory = self.store.get(memory_id)
            if memory:
                self.ledger.log(memory_id, "sleep_archive", self.actor,
                                details={"age": self.clock() - memory.timestamp, "accessCount": memory.accessCount})
                self.store.delete(memory_id, "sleep archive")
                journal.append(f"archived {memory_id}")
        count_synth = 0
        for group in synthesizes:
            members = [self.store.get(memory_id) for memory_id in group]
            members = [memory for memory in members if memory]
            if len(members) < 2:
                continue
            merged = self.store.add({
                "content": "Synthesis: " + " | ".join(memory.content for memory in members),
                "type": "factual", "layer": "long_term",
                "tags": sorted({tag for memory in members for tag in memory.tags}),
                "confidence": max(memory.confidence for memory in members),
                "source": "sleep:synthesis",
                "provenance": {"origin": "sleep_synthesis", "learnedAt": self.clock(),
                               "label": f"synthesized from {len(members)} memories"},
                "derivedFrom": [memory.id for memory in members],
            })
            self.ledger.log(merged.id, "sleep_synthesize", self.actor,
                            details={"sources": list(group)})
            for memory in members:
                self.store.delete(memory.id, "merged by sleep synthesis")
            count_synth += 1
            journal.append(f"synthesized {merged.id} from {', '.join(group)}")
        self.store.persist()
        return {
            "backupId": backup_id, "consolidated": len(consolidates),
            "archived": len(archived), "synthesized": count_synth, "actionLog": journal,
        }

    def restore(self, backup_id):
        row = self.store.stockage.executer(lambda db: db.execute(
            "SELECT donnees FROM hmem_backups WHERE id = ?", (backup_id,)
        ).fetchone())
        if row is None:
            raise ValueError(f"H-MEM: sauvegarde inconnue {backup_id}")
        import json
        snapshot = json.loads(row["donnees"])
        self.store.restore_all(snapshot["records"], snapshot["seq"])
        self.ledger.log("*", "restore", self.actor, details={"backupId": backup_id})
