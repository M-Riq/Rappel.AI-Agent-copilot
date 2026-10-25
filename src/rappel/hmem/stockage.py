"""Stockage transactionnel SQLite et service de mémoire H-MEM."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from .embeddings import cosine, embed
from .enrichissement import domaine, entites, namespace, relations
from .modeles import MemoryRecord, Provenance, clamp01

MESSAGE_STOCKAGE = "Stockage H-MEM SQLite indisponible ou écriture impossible."


class StockageSQLite:
    def __init__(self, chemin: str | Path | None = None):
        self.chemin = Path(chemin) if chemin is not None else None
        self.verrou = RLock()
        try:
            if self.chemin is not None:
                self.chemin.parent.mkdir(parents=True, exist_ok=True)
                self.connexion = sqlite3.connect(
                    str(self.chemin), timeout=5, check_same_thread=False,
                    isolation_level=None,
                )
                self.connexion.execute("PRAGMA journal_mode=WAL")
            else:
                self.connexion = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
            self.connexion.row_factory = sqlite3.Row
            self.connexion.execute("PRAGMA foreign_keys=ON")
            self.connexion.executescript("""
                CREATE TABLE IF NOT EXISTS memoires_hmem (
                    id TEXT PRIMARY KEY,
                    couche TEXT NOT NULL,
                    donnees TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hmem_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    acteur TEXT NOT NULL,
                    horodatage INTEGER NOT NULL,
                    previous_state TEXT,
                    new_state TEXT,
                    details TEXT
                );
                CREATE TABLE IF NOT EXISTS hmem_meta (
                    cle TEXT PRIMARY KEY,
                    valeur TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hmem_backups (
                    id TEXT PRIMARY KEY,
                    donnees TEXT NOT NULL
                );
            """)
        except (OSError, sqlite3.Error) as erreur:
            raise RuntimeError(MESSAGE_STOCKAGE) from erreur

    def fermer(self):
        with self.verrou:
            try:
                self.connexion.close()
            except sqlite3.Error as erreur:
                raise RuntimeError(MESSAGE_STOCKAGE) from erreur

    def executer(self, fonction):
        with self.verrou:
            try:
                return fonction(self.connexion)
            except (OSError, sqlite3.Error) as erreur:
                raise RuntimeError(MESSAGE_STOCKAGE) from erreur

    @contextmanager
    def transaction(self):
        with self.verrou:
            try:
                self.connexion.execute("BEGIN IMMEDIATE")
                yield self.connexion
                self.connexion.execute("COMMIT")
            except (OSError, sqlite3.Error) as erreur:
                if self.connexion.in_transaction:
                    self.connexion.execute("ROLLBACK")
                raise RuntimeError(MESSAGE_STOCKAGE) from erreur
            except Exception:
                if self.connexion.in_transaction:
                    self.connexion.execute("ROLLBACK")
                raise


class MemoryStoreService:
    def __init__(self, clock, ledger, actor="system", persist_path=None, stockage=None):
        self.clock = clock
        self.ledger = ledger
        self.actor = actor
        self.stockage = stockage or StockageSQLite(persist_path)
        self._records: dict[str, MemoryRecord] = {}
        self._seq = 0
        self._charger()

    def _charger(self):
        def lire(connexion):
            rows = connexion.execute(
                "SELECT donnees FROM memoires_hmem WHERE couche != 'short_term' ORDER BY rowid"
            ).fetchall()
            meta = connexion.execute("SELECT valeur FROM hmem_meta WHERE cle = 'seq'").fetchone()
            return rows, int(meta["valeur"]) if meta else 0
        rows, self._seq = self.stockage.executer(lire)
        for ligne in rows:
            record = MemoryRecord.depuis(json.loads(ligne["donnees"]))
            if not record.embedding:
                record.embedding = embed(record.content)
            self._records[record.id] = record
        for record in self._records.values():
            if record.layer != "long_term":
                age = max(0, (self.clock() - record.lastAccessed) / 86_400_000)
                record.confidence = clamp01(record.confidence * math.exp(-age / 14))

    def all(self) -> list[MemoryRecord]:
        return list(self._records.values())

    def get(self, memory_id: str) -> MemoryRecord | None:
        return self._records.get(memory_id)

    def _persist_record(self, record: MemoryRecord, connexion=None):
        data = json.dumps(record.vers_dict(), ensure_ascii=False, separators=(",", ":"))

        def ecrire(connexion):
            connexion.execute(
                "INSERT INTO memoires_hmem(id, couche, donnees) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET couche=excluded.couche, donnees=excluded.donnees",
                (record.id, record.layer, data),
            )
            connexion.execute(
                "INSERT INTO hmem_meta(cle, valeur) VALUES ('seq', ?) "
                "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur",
                (str(self._seq),),
            )
        def sauvegarder(db):
            if record.layer == "short_term":
                db.execute("DELETE FROM memoires_hmem WHERE id = ?", (record.id,))
            else:
                ecrire(db)
        if connexion is not None:
            sauvegarder(connexion)
        else:
            self.stockage.executer(sauvegarder)

    def persist(self):
        def ecrire(connexion):
            connexion.execute("BEGIN IMMEDIATE")
            try:
                connexion.execute("DELETE FROM memoires_hmem")
                connexion.executemany(
                    "INSERT INTO memoires_hmem(id, couche, donnees) VALUES (?, ?, ?)",
                    [
                        (r.id, r.layer, json.dumps(r.vers_dict(), ensure_ascii=False, separators=(",", ":")))
                        for r in self._records.values() if r.layer != "short_term"
                    ],
                )
                connexion.execute(
                    "INSERT INTO hmem_meta(cle, valeur) VALUES ('seq', ?) "
                    "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur",
                    (str(self._seq),),
                )
                connexion.execute("COMMIT")
            except Exception:
                connexion.execute("ROLLBACK")
                raise
        self.stockage.executer(ecrire)

    def add(self, input: dict | None = None, **kwargs) -> MemoryRecord:
        donnees = dict(input or {})
        donnees.update(kwargs)
        source = donnees.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("H-MEM: un enregistrement sans source doit être rejeté.")
        now = self.clock()
        type_memoire = donnees.get("type", "general")
        couche = donnees.get("layer", "working")
        contenu = donnees.get("content")
        if not isinstance(contenu, str) or not contenu.strip():
            raise ValueError("Le contenu H-MEM doit être une chaîne non vide.")
        tags = list(donnees.get("tags", []))
        self._seq += 1
        record = MemoryRecord.depuis({
            **donnees, "id": f"mem-{self._seq}", "type": type_memoire, "layer": couche,
            "timestamp": now, "lastAccessed": now, "accessCount": 0,
            "tags": tags, "embedding": embed(contenu),
            "confidence": clamp01(donnees.get("confidence", 0.7)),
            "provenance": donnees.get("provenance") or {
                "origin": "manual", "learnedAt": now, "label": source,
            },
            "domain": donnees.get("domain") or domaine(contenu, tags, type_memoire),
            "namespace": donnees.get("namespace") or namespace(domaine(contenu, tags, type_memoire), type_memoire),
            "entities": donnees.get("entities") or entites(contenu),
            "relatedTo": [], "derivedFrom": list(donnees.get("derivedFrom", [])),
        })
        avant_relations = {
            autre.id: list(autre.relatedTo) for autre in self.all()
        }
        relations(record, self.all(), cosine)
        try:
            with self.stockage.transaction() as connexion:
                self._persist_record(record, connexion)
                for linked_id in record.relatedTo:
                    linked = self.get(linked_id)
                    if linked:
                        self._persist_record(linked, connexion)
                self.ledger.log(record.id, "create", self.actor, newState=record.vers_dict(),
                                connexion=connexion)
        except Exception:
            self._seq -= 1
            for linked_id, relations_avant in avant_relations.items():
                linked = self.get(linked_id)
                if linked:
                    linked.relatedTo = relations_avant
            raise
        self._records[record.id] = record
        return record

    def update(self, memory_id: str, patch: dict):
        record = self._require(memory_id)
        avant = record.vers_dict()
        for key, value in patch.items():
            if not hasattr(record, key):
                raise ValueError(f"Champ H-MEM inconnu : {key}")
            setattr(record, key, value)
        record.valider()
        if "content" in patch:
            record.embedding = embed(record.content)
        try:
            with self.stockage.transaction() as connexion:
                self._persist_record(record, connexion)
                self.ledger.log(memory_id, "update", self.actor, previousState=avant,
                                newState=record.vers_dict(), connexion=connexion)
        except Exception:
            restored = MemoryRecord.depuis(avant)
            record.__dict__.update(restored.__dict__)
            raise
        return record

    def touch(self, memory_id: str, query=None):
        record = self._require(memory_id)
        avant = record.vers_dict()
        record.lastAccessed = self.clock()
        record.accessCount += 1
        try:
            with self.stockage.transaction() as connexion:
                self._persist_record(record, connexion)
                self.ledger.log(memory_id, "read", self.actor, details={"query": query},
                                connexion=connexion)
        except Exception:
            restored = MemoryRecord.depuis(avant)
            record.__dict__.update(restored.__dict__)
            raise

    def apply_feedback(self, memory_id: str, kind: str, delta: float):
        record = self._require(memory_id)
        before = record.vers_dict()
        if kind == "helpful":
            record.accessCount += 1
        record.confidence = clamp01(record.confidence + delta)
        if kind == "wrong" and "review" not in record.tags:
            record.tags.append("review")
        try:
            with self.stockage.transaction() as connexion:
                self._persist_record(record, connexion)
                self.ledger.log(
                    memory_id, "feedback", self.actor, previousState=before,
                    newState=record.vers_dict(), details={"kind": kind, "delta": delta},
                    connexion=connexion,
                )
        except Exception:
            record.__dict__.update(MemoryRecord.depuis(before).__dict__)
            raise
        return record

    def promote(self, memory_id: str, layer: str):
        record = self._require(memory_id)
        before = record.vers_dict()
        record.layer = layer
        try:
            with self.stockage.transaction() as connexion:
                self._persist_record(record, connexion)
                self.ledger.log(memory_id, "promote", self.actor, previousState=before,
                                newState=record.vers_dict(), connexion=connexion)
        except Exception:
            record.__dict__.update(MemoryRecord.depuis(before).__dict__)
            raise

    def delete(self, memory_id: str, reason: str):
        record = self._require(memory_id)
        with self.stockage.transaction() as connexion:
            connexion.execute("DELETE FROM memoires_hmem WHERE id = ?", (memory_id,))
            self.ledger.log(memory_id, "delete", self.actor, previousState=record.vers_dict(),
                            details={"reason": reason}, connexion=connexion)
        del self._records[memory_id]

    def snapshot_all(self):
        return {"records": [r.vers_dict() for r in self.all()], "seq": self._seq}

    def restore_all(self, records: list[dict], seq: int):
        self._records = {r["id"]: MemoryRecord.depuis(r) for r in records}
        self._seq = seq
        self.persist()

    def _require(self, memory_id):
        record = self.get(memory_id)
        if record is None:
            raise ValueError(f"H-MEM: souvenir inconnu {memory_id}")
        return record

    def extract_entities(self, content: str):
        return entites(content)

    def classify_domain(self, content: str, tags: list[str], type_memoire: str):
        return domaine(content, tags, type_memoire)
