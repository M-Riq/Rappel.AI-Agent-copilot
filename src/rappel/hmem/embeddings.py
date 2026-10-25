"""Empreintes déterministes H-MEM (portage du hash FNV-1a de la référence)."""

import math
import re

DIMENSIONS = 64


def tokenize(texte: str) -> list[str]:
    return [mot for mot in re.sub(r"[^a-z0-9\s]", " ", texte.lower()).split() if len(mot) > 1]


def _fnv1a(texte: str) -> int:
    empreinte = 0x811C9DC5
    for caractere in texte:
        empreinte ^= ord(caractere)
        empreinte = (empreinte * 0x01000193) & 0xFFFFFFFF
    return empreinte


def embed(texte: str) -> list[float]:
    vecteur = [0.0] * DIMENSIONS
    for mot in tokenize(texte):
        empreinte = _fnv1a(mot)
        index = empreinte % DIMENSIONS
        signe = 1 if (empreinte >> 8) % 2 == 0 else -1
        magnitude = 1 + ((empreinte >> 16) % 8) / 8
        vecteur[index] += signe * magnitude
    norme = math.sqrt(sum(valeur * valeur for valeur in vecteur))
    return [valeur / norme for valeur in vecteur] if norme else vecteur


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def jaccard(a, b) -> float:
    gauche, droite = set(a), set(b)
    union = gauche | droite
    return len(gauche & droite) / len(union) if union else 0.0
