"""Moteur H-MEM déterministe avec persistance SQLite."""

from datetime import datetime, timezone
from pathlib import Path

from .audit import TrustLedgerService
from .conflits import ConflictDetectionService
from .modeles import (
    ACTIONS_AUDIT, DEFAULTS, ConflictRecord, DreamProposal, LedgerEntry, MemoryRecord, Provenance,
)
from .recherche import MemoryRetrievalService
from .reve import DreamingService
from .sommeil import SleepCycleService
from .stockage import MemoryStoreService, StockageSQLite


def _horloge_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class HMEM:
    def __init__(self, clock=None, *, persist_path=None, persistDir=None, actor="system", minerFn=None):
        # Référence TS: HMEM({clock}); API Python: HMEM(clock=...).
        if isinstance(clock, dict):
            options = clock
            clock = options.get("clock")
            persist_path = options.get("persist_path", persist_path)
            persistDir = options.get("persistDir", persistDir)
            actor = options.get("actor", actor)
            minerFn = options.get("minerFn", minerFn)
        self.clock = clock or _horloge_ms
        chemin = persist_path
        if chemin is None and persistDir is not None:
            dossier = Path(persistDir)
            chemin = dossier / "hmem.sqlite"
        self.storage = StockageSQLite(chemin)
        self.ledger = TrustLedgerService(self.storage, self.clock)
        self.store = MemoryStoreService(self.clock, self.ledger, actor, stockage=self.storage)
        self.retrieval = MemoryRetrievalService(self.store, self.ledger, self.clock, actor)
        self.sleep = SleepCycleService(self.store, self.ledger, self.clock, actor)
        self.dreaming = DreamingService(self.store, self.ledger, self.clock, actor)
        self.conflicts = ConflictDetectionService(self.store, self.ledger, self.clock, actor)
        self.minerFn = minerFn

    def add(self, memory=None, **kwargs):
        return self.store.add(memory, **kwargs)

    def query(self, query, opts=None):
        return self.retrieval.query(query, opts)

    def feedback(self, memory_id, kind):
        return self.retrieval.feedback(memory_id, kind)

    def close(self):
        self.storage.fermer()


__all__ = [
    "HMEM", "MemoryRecord", "Provenance", "LedgerEntry", "DreamProposal", "ConflictRecord",
    "MemoryStoreService", "MemoryRetrievalService", "TrustLedgerService",
    "SleepCycleService", "DreamingService", "ConflictDetectionService",
    "ACTIONS_AUDIT", "DEFAULTS",
]
