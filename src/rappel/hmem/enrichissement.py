"""Enrichissement déterministe des souvenirs d'incident."""

import re

from .embeddings import tokenize
from .modeles import DEFAULTS

TERMES_TECH = (
    "react", "python", "typescript", "javascript", "rust", "sqlite", "postgres",
    "indexeddb", "node", "docker", "graphql", "redis", "webrtc", "tauri",
    "electron", "llm", "embedding",
)
MOTS_DOMAINE = {
    "tech": {"code", "bug", "api", "database", "deploy", "framework", "library", "server", *TERMES_TECH},
    "work": {"meeting", "deadline", "client", "project", "sprint", "review", "report"},
    "personal": {"family", "hobby", "prefer", "like", "dislike", "morning", "evening"},
}


def domaine(contenu: str, tags: list[str], type_memoire: str) -> str:
    for tag in tags:
        if tag.startswith("project:"):
            return tag.removeprefix("project:")
        if tag.startswith("tech:"):
            return "tech"
    jetons = set(tokenize(contenu))
    scores = {nom: len(jetons & mots) for nom, mots in MOTS_DOMAINE.items()}
    if type_memoire in ("personal", "preference"):
        scores["personal"] += 1
    meilleur = max(scores, key=scores.get)
    return meilleur if scores[meilleur] else "general"


def namespace(domain: str, type_memoire: str) -> str:
    categorie = "profile" if type_memoire in ("personal", "preference") else (
        "facts" if type_memoire == "factual" else "notes"
    )
    return f"{domain}/{categorie}"


def entites(contenu: str) -> list[str]:
    resultat = []
    for token in tokenize(contenu):
        if token in TERMES_TECH and token not in resultat:
            resultat.append(token)
    for mot in re.findall(r"\b([A-Z][a-zA-Z]{2,})\b", contenu):
        normalise = mot.lower()
        if normalise not in ("the", "this", "that", "with") and normalise not in resultat:
            resultat.append(normalise)
    return resultat[:DEFAULTS["entityCap"]]


def relations(nouveau, existants, similarite) -> None:
    correction = re.compile(r"\b(actually|instead|no longer|not anymore|changed to|correction)\b", re.I)
    for autre in existants:
        if len(nouveau.relatedTo) >= DEFAULTS["relatedCap"]:
            break
        partages = set(nouveau.entities) & set(autre.entities)
        if not nouveau.supersedes and similarite(nouveau.embedding, autre.embedding) > 0.85 and correction.search(nouveau.content):
            nouveau.supersedes = autre.id
            continue
        if partages and autre.domain == nouveau.domain:
            nouveau.relatedTo.append(autre.id)
            if len(autre.relatedTo) < DEFAULTS["relatedCap"] and nouveau.id not in autre.relatedTo:
                autre.relatedTo.append(nouveau.id)
