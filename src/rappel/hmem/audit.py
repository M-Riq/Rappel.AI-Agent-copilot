"""Journal H-MEM append-only des actions et changements de confiance."""

import json
from dataclasses import asdict

from .modeles import ACTIONS_AUDIT, LedgerEntry


class TrustLedgerService:
    def __init__(self, stockage, clock):
        self.stockage = stockage
        self.clock = clock

    def log(self, memoryId, action, actor, *, previousState=None, newState=None, details=None, connexion=None):
        if action not in ACTIONS_AUDIT:
            raise ValueError(f"Action d'audit H-MEM inconnue : {action}")
        entry = LedgerEntry(memoryId, action, actor, self.clock(), previousState, newState, details)

        def ajouter(db):
            db.execute(
                "INSERT INTO hmem_audit(memory_id, action, acteur, horodatage, previous_state, new_state, details) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    memoryId, action, actor, entry.timestamp,
                    _json(previousState), _json(newState), _json(details),
                ),
            )
        if connexion is not None:
            ajouter(connexion)
        else:
            self.stockage.executer(ajouter)
        return entry

    def history(self, memoryId):
        rows = self.stockage.executer(lambda db: db.execute(
            "SELECT memory_id, action, acteur, horodatage, previous_state, new_state, details "
            "FROM hmem_audit WHERE memory_id = ? ORDER BY sequence", (memoryId,)
        ).fetchall())
        return [_entry(row) for row in rows]

    def all(self):
        rows = self.stockage.executer(lambda db: db.execute(
            "SELECT memory_id, action, acteur, horodatage, previous_state, new_state, details "
            "FROM hmem_audit ORDER BY sequence"
        ).fetchall())
        return [_entry(row) for row in rows]

    def stats(self):
        return self.stockage.executer(lambda db: dict(db.execute(
            "SELECT action, COUNT(*) FROM hmem_audit GROUP BY action"
        ).fetchall()))

    def range(self, since=None, actions=None):
        entries = self.all()
        return [
            entry for entry in entries
            if (since is None or entry.timestamp >= since)
            and (actions is None or entry.action in actions)
        ]


def _json(valeur):
    if valeur is None:
        return None
    return json.dumps(valeur, ensure_ascii=False, separators=(",", ":"))


def _entry(row):
    def lire(cle):
        valeur = row[cle]
        return json.loads(valeur) if valeur is not None else None
    return LedgerEntry(
        memoryId=row["memory_id"], action=row["action"], actor=row["acteur"],
        timestamp=row["horodatage"], previousState=lire("previous_state"),
        newState=lire("new_state"), details=lire("details"),
    )
