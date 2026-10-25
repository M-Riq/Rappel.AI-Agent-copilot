"""Modèles canoniques H-MEM."""

from dataclasses import asdict, dataclass, field
from typing import Literal

TypeMemoire = Literal["personal", "factual", "preference", "general"]
CoucheMemoire = Literal["short_term", "working", "long_term"]
ORIGINES = frozenset({
    "chat_message", "user_correction", "file_save", "file_open", "file_edit",
    "model_response", "plan_completion", "document", "manual", "dream", "sleep_synthesis",
    "chat", "import", "web", "decision", "curiosity", "goal_run", "user_explicit",
    "inference", "plan_run", "mission", "swarm_run", "tool", "brain",
})
ACTIONS_AUDIT = frozenset({
    "create", "read", "update", "delete", "feedback", "promote", "demote", "merge",
    "conflict_detected", "conflict_resolved", "sleep_consolidate", "sleep_archive",
    "sleep_synthesize", "dream_proposed", "dream_approved", "dream_rejected", "restore",
})


@dataclass
class Provenance:
    origin: str
    learnedAt: int
    label: str
    refs: list[str] | None = None

    @classmethod
    def depuis(cls, valeur) -> "Provenance":
        if isinstance(valeur, cls):
            valeur = asdict(valeur)
        if not isinstance(valeur, dict):
            raise ValueError("La provenance H-MEM doit être un objet.")
        origine = valeur.get("origin")
        libelle = valeur.get("label")
        appris = valeur.get("learnedAt")
        if origine not in ORIGINES or not isinstance(libelle, str) or not libelle.strip():
            raise ValueError("La provenance H-MEM doit contenir une origine et un label valides.")
        if not isinstance(appris, int) or isinstance(appris, bool):
            raise ValueError("provenance.learnedAt doit être un entier en millisecondes.")
        refs = valeur.get("refs")
        if refs is not None and (not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs)):
            raise ValueError("provenance.refs doit être une liste de chaînes.")
        return cls(origine, appris, libelle, list(refs) if refs is not None else None)


@dataclass
class MemoryRecord:
    id: str
    content: str
    type: TypeMemoire
    layer: CoucheMemoire
    timestamp: int
    lastAccessed: int
    accessCount: int
    tags: list[str]
    embedding: list[float]
    confidence: float
    source: str
    provenance: Provenance
    domain: str
    namespace: str
    entities: list[str] = field(default_factory=list)
    relatedTo: list[str] = field(default_factory=list)
    score: float | None = None
    chatId: str | None = None
    missionId: str | None = None
    goalRunId: str | None = None
    supersedes: str | None = None
    supersededBy: str | None = None
    derivedFrom: list[str] = field(default_factory=list)

    def valider(self) -> "MemoryRecord":
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("H-MEM: un enregistrement sans source doit être rejeté.")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("Le contenu H-MEM doit être une chaîne non vide.")
        if self.type not in ("personal", "factual", "preference", "general"):
            raise ValueError("Type de mémoire H-MEM inconnu.")
        if self.layer not in ("short_term", "working", "long_term"):
            raise ValueError("Couche de mémoire H-MEM inconnue.")
        if not 0 <= self.confidence <= 1:
            raise ValueError("La confiance H-MEM doit être comprise entre 0 et 1.")
        if len(self.entities) > 10 or len(self.relatedTo) > 5:
            raise ValueError("H-MEM accepte au plus 10 entités et 5 relations.")
        self.provenance = Provenance.depuis(self.provenance)
        return self

    @classmethod
    def depuis(cls, donnees: dict) -> "MemoryRecord":
        if isinstance(donnees, cls):
            return donnees.valider()
        if not isinstance(donnees, dict):
            raise ValueError("Un enregistrement H-MEM doit être un objet.")
        source = donnees.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("H-MEM: un enregistrement sans source doit être rejeté.")
        try:
            resultat = cls(
                id=str(donnees.get("id", "")),
                content=donnees["content"],
                type=donnees.get("type", "general"),
                layer=donnees.get("layer", "working"),
                timestamp=donnees.get("timestamp", 0),
                lastAccessed=donnees.get("lastAccessed", donnees.get("timestamp", 0)),
                accessCount=donnees.get("accessCount", 0),
                tags=list(donnees.get("tags", [])),
                embedding=list(donnees.get("embedding", [])),
                score=donnees.get("score"),
                confidence=donnees.get("confidence", 0.7),
                source=source,
                provenance=Provenance.depuis(donnees["provenance"]),
                chatId=donnees.get("chatId"),
                missionId=donnees.get("missionId"),
                goalRunId=donnees.get("goalRunId"),
                domain=donnees.get("domain", "general"),
                namespace=donnees.get("namespace", "general/notes"),
                entities=list(donnees.get("entities", [])),
                relatedTo=list(donnees.get("relatedTo", [])),
                supersedes=donnees.get("supersedes"),
                supersededBy=donnees.get("supersededBy"),
                derivedFrom=list(donnees.get("derivedFrom", [])),
            )
        except (KeyError, TypeError) as erreur:
            raise ValueError("Enregistrement H-MEM incomplet.") from erreur
        return resultat.valider()

    def vers_dict(self) -> dict:
        return asdict(self)


@dataclass
class LedgerEntry:
    memoryId: str
    action: str
    actor: str
    timestamp: int
    previousState: dict | None = None
    newState: dict | None = None
    details: dict | None = None


@dataclass
class DreamProposal:
    id: str
    type: Literal["insight", "domain", "invariant"]
    content: str
    derivedFrom: list[str]
    status: Literal["proposed", "approved", "rejected"] = "proposed"
    suggestedDomain: str | None = None


@dataclass
class ConflictRecord:
    id: str
    memoryA: str
    memoryB: str
    reason: str
    confidence: float
    resolved: bool = False
    resolution: str | None = None


DEFAULTS = {
    "retrievalTopN": 5,
    "retrievalThreshold": 0.35,
    "consolidationDays": 7,
    "archiveMinDays": 30,
    "archiveMaxAccess": 2,
    "synthesisCosine": 0.82,
    "dreamClusterMinSize": 2,
    "dreamRelatedness": 0.35,
    "maxDreamProposalsPerType": 8,
    "conflictAutoResolveGate": 0.70,
    "entityCap": 10,
    "relatedCap": 5,
    "recencyDays": 14,
}


def clamp01(valeur: float) -> float:
    return min(1.0, max(0.0, valeur))
