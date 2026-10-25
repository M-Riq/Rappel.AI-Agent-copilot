"""Adaptateur du protocole de mémoire de Rappel vers H-MEM/SQLite."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from rappel.config import RACINE
from rappel.enquete.modeles import EnregistrementMemoire
from rappel.hmem import HMEM
from rappel.hmem.recherche import contexte_recherche


class MemoireHMEM:
    def __init__(self, chemin="data/hmem.sqlite", clock=None):
        chemin = Path(chemin)
        self.chemin = chemin if chemin.is_absolute() else RACINE / chemin
        self.hmem = HMEM(clock=clock, persist_path=self.chemin)
        self.last_search_trace = None
        self._last_memories = []

    @property
    def ledger(self):
        return self.hmem.ledger

    def __repr__(self):
        return f"MemoireHMEM(chemin={str(self.chemin)!r})"

    @staticmethod
    def _refs(memory):
        return memory.provenance.refs or []

    @classmethod
    def _valeur(cls, memory, cle, defaut=""):
        prefix = f"{cle}:"
        return next((ref[len(prefix):] for ref in cls._refs(memory) if ref.startswith(prefix)), defaut)

    def _vers_enregistrement(self, memory):
        if memory.type != "factual" or not memory.source.startswith("enquete:"):
            return None
        signature = self._valeur(memory, "signature")
        cause = self._valeur(memory, "cause")
        if not signature or not cause:
            return None
        refutees = [ref[len("hypothese_refutee:"):] for ref in self._refs(memory)
                    if ref.startswith("hypothese_refutee:")]
        date = self._valeur(memory, "date", datetime.fromtimestamp(
            memory.timestamp / 1000, tz=timezone.utc
        ).date().isoformat())
        resultat = self._valeur(memory, "resultat", "reussi")
        if resultat not in ("reussi", "echoue"):
            resultat = "reussi"
        return EnregistrementMemoire(
            signature=signature, cause_confirmee=cause,
            hypotheses_refutees=refutees,
            correctif_essaye=self._valeur(memory, "correctif"),
            resultat=resultat, date=date,
        )

    def _chercher(self, query):
        resultat = self.hmem.query(query, {"topN": 5, "threshold": 0.35})
        self.last_search_trace = {
            **resultat["trace"],
            "scores": dict(resultat["trace"]["scores"]),
            "context": contexte_recherche(resultat),
            "retrieved": [
                {"id": row["memory"].id, "score": row["relevance"],
                 "retrievalReason": resultat["trace"]["retrievalReason"]}
                for row in resultat["memories"]
            ],
        }
        self._last_memories = [row["memory"] for row in resultat["memories"]]
        memorises = []
        for row in resultat["memories"]:
            mapped = self._vers_enregistrement(row["memory"])
            if mapped is not None:
                memorises.append((row["relevance"], mapped))
        return [item for _, item in memorises]

    def chercher(self, signature):
        souvenirs = self._chercher(signature)
        if souvenirs:
            return souvenirs
        exacts = [
            mapped for memory in self.hmem.store.all()
            if self._valeur(memory, "signature") == signature
            and (mapped := self._vers_enregistrement(memory)) is not None
        ]
        if exacts:
            self._last_memories = [
                memory for memory in self.hmem.store.all()
                if self._valeur(memory, "signature") == signature
            ]
            self.last_search_trace = {
                "retrievalReason": "compatibilité du protocole : correspondance exacte de signature",
                "strategy": "minimal", "scores": {}, "context": "", "retrieved": [],
            }
        return exacts

    def chercher_contexte(self, signature, contexte):
        composant = ""
        schema = ""
        logs = ""
        if contexte is not None:
            composant = signature.partition(":")[2] or signature
            schema_obj = getattr(contexte, "schema", None)
            if schema_obj is not None:
                schema = " ".join([*getattr(schema_obj, "attendu", []), *getattr(schema_obj, "observe", [])])
            logs = " ".join(
                f"{log.template} {log.compte}" for log in getattr(contexte, "logs", [])
            )
        requete = " ".join(part for part in (signature, composant, schema, logs) if part)
        return self._chercher(requete)

    def lister(self):
        return [
            mapped for memory in self.hmem.store.all()
            if (mapped := self._vers_enregistrement(memory)) is not None
        ]

    def vider(self):
        memorias = self.hmem.store.all()
        for memory in memorias:
            self.hmem.store.delete(memory.id, "réinitialisation Rappel")
        return sum(memory.type == "factual" and memory.source.startswith("enquete:") for memory in memorias)

    def ecrire(self, enregistrement):
        self.ecrire_incident(enregistrement)

    def ecrire_incident(
        self, enregistrement, *, incident_id=None, cause=None, confiance=0.7,
        preuves_sql=None, hypotheses=None, composants=None, contexte=None,
    ):
        record = EnregistrementMemoire.model_validate(
            enregistrement.model_dump() if hasattr(enregistrement, "model_dump") else enregistrement
        )
        preuves_sql = preuves_sql or []
        if not cause or not str(cause).strip() or not any(
            isinstance(preuve, dict) and str(preuve.get("sql", "")).strip()
            and preuve.get("nb_lignes", 0) > 0 and preuve.get("erreur") is None
            for preuve in preuves_sql
        ):
            raise ValueError("H-MEM n'enregistre une cause confirmée qu'avec une preuve SQL exécutée non vide.")
        incident_id = incident_id or "inconnu"
        refs = [
            f"incident:{incident_id}", f"signature:{record.signature}",
            f"cause:{cause}", f"correctif:{record.correctif_essaye}",
            f"resultat:{record.resultat}", f"date:{record.date}",
            *(f"hypothese_refutee:{texte}" for texte in record.hypotheses_refutees),
        ]
        refs.extend(
            f"preuve_sql:{preuve.get('id', '')}:{preuve['sql']}"
            for preuve in preuves_sql if isinstance(preuve, dict) and str(preuve.get("sql", "")).strip()
        )
        summary = {
            "incident_id": incident_id, "signature": record.signature, "cause": cause,
            "correctif": record.correctif_essaye, "resultat": record.resultat,
            "date": record.date, "preuves_sql_ids": [p.get("id", "") for p in preuves_sql],
            "contexte": contexte or {},
        }
        contenu = (
            f"Incident {incident_id}, signature {record.signature}. Cause confirmée : {cause}. "
            f"Correctif : {record.correctif_essaye}. "
            + json.dumps(contexte or {}, ensure_ascii=False, sort_keys=True)
            + "\n" + json.dumps(summary, ensure_ascii=False, sort_keys=True)
        )
        identifiant = f"enquete:{incident_id}"
        exists = next((m for m in self.hmem.store.all() if m.source == identifiant), None)
        entites_contexte = []
        if isinstance(contexte, dict):
            entites_contexte.extend(contexte.get("schema_attendu", []))
            entites_contexte.extend(contexte.get("schema_observe", []))
            entites_contexte.extend(
                log.get("template", "") for log in contexte.get("logs_principaux", [])
                if isinstance(log, dict)
            )
        entites_finales = list(dict.fromkeys([*(composants or []), *entites_contexte]))[:10]
        if exists:
            return self.hmem.store.update(exists.id, {
                "content": contenu, "confidence": confiance, "tags": list(dict.fromkeys(
                    [*exists.tags, f"signature:{record.signature}"])),
                "provenance": {
                    "origin": "decision", "learnedAt": self.hmem.clock(), "label": "cause d'incident confirmée par SQL",
                    "refs": refs,
                },
                "entities": list(dict.fromkeys([*exists.entities, *entites_finales]))[:10],
            })
        return self.hmem.add({
            "content": contenu, "type": "factual", "layer": "long_term",
            "tags": ["incident", f"signature:{record.signature}"],
            "confidence": confiance, "source": identifiant,
            "provenance": {
                "origin": "decision", "learnedAt": self.hmem.clock(),
                "label": "cause d'incident confirmée par requête SQL exécutée", "refs": refs,
            },
            "entities": entites_finales,
        })

    def hypotheses_refutees(self, signature):
        values = []
        memories = self._last_memories or self.hmem.store.all()
        for memory in memories:
            if self._valeur(memory, "signature") == signature or memory in self._last_memories:
                values.extend(
                    ref[len("hypothese_refutee:"):] for ref in self._refs(memory)
                    if ref.startswith("hypothese_refutee:")
                )
            elif memory.source.startswith("enquete:") and memory.type != "factual":
                if (self._valeur(memory, "signature") == signature or memory in self._last_memories) \
                        and self._valeur(memory, "statut") == "refutee":
                    values.append(memory.content)
        return list(dict.fromkeys(values))

    def mettre_a_jour_resultat(self, signature, resultat):
        if resultat not in ("reussi", "echoue"):
            raise ValueError("Le résultat doit valoir reussi ou echoue.")
        correspondants = [
            memory for memory in self.hmem.store.all()
            if self._valeur(memory, "signature") == signature and memory.type == "factual"
        ]
        if not correspondants:
            return False
        memory = correspondants[-1]
        refs = [ref for ref in self._refs(memory) if not ref.startswith("resultat:")]
        refs.append(f"resultat:{resultat}")
        self.hmem.feedback(memory.id, "helpful" if resultat == "reussi" else "wrong")
        self.hmem.store.update(memory.id, {
            "provenance": {**memory.provenance.__dict__, "refs": refs},
        })
        return True

    def marquer_non_confirme(self, enregistrement):
        cle = (enregistrement.signature, enregistrement.correctif_essaye, enregistrement.date)
        for memory in self.hmem.store.all():
            if (
                self._valeur(memory, "signature") == cle[0]
                and self._valeur(memory, "correctif") == cle[1]
                and self._valeur(memory, "date") == cle[2]
            ):
                refs = [ref for ref in self._refs(memory) if ref != "statut_resultat:non_confirme"]
                refs.append("statut_resultat:non_confirme")
                self.hmem.store.update(memory.id, {
                    "provenance": {**memory.provenance.__dict__, "refs": refs},
                })
                return

    def statut_resultat(self, enregistrement):
        for memory in self.hmem.store.all():
            mapped = self._vers_enregistrement(memory)
            if mapped and (
                mapped.signature, mapped.correctif_essaye, mapped.date, mapped.cause_confirmee
            ) == (
                enregistrement.signature, enregistrement.correctif_essaye,
                enregistrement.date, enregistrement.cause_confirmee,
            ):
                return "non_confirme" if "statut_resultat:non_confirme" in self._refs(memory) else mapped.resultat
        return enregistrement.resultat

    def ecrire_hypotheses(self, incident_id, signature, hypotheses):
        for hypothesis in hypotheses:
            data = hypothesis.model_dump(mode="json") if hasattr(hypothesis, "model_dump") else hypothesis
            tags = ["hypothese", f"statut:{data['statut']}"]
            provenance = {
                "origin": "decision", "learnedAt": self.hmem.clock(),
                "label": "hypothèse d'enquête (non assimilée à un fait)",
                "refs": [f"incident:{incident_id}", f"signature:{signature}",
                         f"statut:{data['statut']}", f"confiance:{data['confiance']}",
                         f"raison_rejet:{data.get('raison_rejet') or ''}",
                         *(f"preuve:{preuve}" for preuve in data.get("preuves", []))],
            }
            source = f"enquete:{incident_id}:hypothese:{data['id']}"
            existant = next((m for m in self.hmem.store.all() if m.source == source), None)
            valeurs = {
                "content": data["texte"], "type": "general", "layer": "working",
                "tags": tags, "confidence": min(data["confiance"], 0.5),
                "source": source, "provenance": provenance,
            }
            if existant:
                self.hmem.store.update(existant.id, {k: v for k, v in valeurs.items() if k != "source"})
            else:
                self.hmem.add(valeurs)

    def lister_hypotheses(self, signature=None):
        lignes = []
        for memory in self.hmem.store.all():
            if not memory.source.startswith("enquete:") or "hypothese" not in memory.tags:
                continue
            sig = self._valeur(memory, "signature")
            if signature is not None and sig != signature:
                continue
            statut = self._valeur(memory, "statut", "ouverte")
            refs = self._refs(memory)
            incident = self._valeur(memory, "incident", "")
            identifiant = memory.source.rsplit(":", 1)[-1]
            lignes.append({
                "id": identifiant, "incident_id": incident, "signature": sig,
                "texte": memory.content, "statut": statut, "preuves": [],
                "confiance": float(self._valeur(memory, "confiance", str(memory.confidence))),
                "raison_rejet": None, "cree_le": datetime.fromtimestamp(
                    memory.timestamp / 1000, tz=timezone.utc
                ).isoformat(),
            })
            lignes[-1]["preuves"] = [
                ref[len("preuve:"):] for ref in refs if ref.startswith("preuve:")
            ]
            raison = self._valeur(memory, "raison_rejet")
            lignes[-1]["raison_rejet"] = raison or None
        return lignes

    def trace_recherche(self):
        return self.last_search_trace
