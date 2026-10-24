"""Réduction des logs en modèles (templates) avec compte.

Réducteur maison par normalisation (horodatage retiré, nombres, identifiants hexadécimaux et
UUID remplacés par <*>). drain3 est utilisé s'il est importable ; sinon, ou en cas d'erreur,
le réducteur maison prend le relais. La fonction indique toujours lequel a servi.
"""

from collections import Counter
import importlib
import re

MAX_MODELES = 9
HORODATAGE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\S+\s+")
NORMALISATIONS = (
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<*>"),
    (re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{12,}\b"), "<*>"),
    (re.compile(r"(?<![A-Za-z_<*])-?\d+(?:\.\d+)?"), "<*>"),
)
PRIORITE_NIVEAU = {"ALERT": 0, "CRITICAL": 0, "ERROR": 1, "WARN": 2, "WARNING": 2, "INFO": 3, "DEBUG": 4}


def sans_horodatage(ligne: str) -> str:
    return HORODATAGE.sub("", ligne.strip())


def normaliser(ligne: str) -> str:
    texte = sans_horodatage(ligne)
    for motif, remplacement in NORMALISATIONS:
        texte = motif.sub(remplacement, texte)
    return re.sub(r"\s+", " ", texte).strip()


def _modeles_maison(lignes) -> Counter:
    return Counter(normaliser(ligne) for ligne in lignes if ligne.strip())


def _modeles_drain3(module, lignes) -> Counter:
    mineur = module.TemplateMiner()
    for ligne in lignes:
        if ligne.strip():
            mineur.add_log_message(sans_horodatage(ligne))
    return Counter({grappe.get_template(): grappe.size for grappe in mineur.drain.clusters})


def _priorite(modele: str) -> int:
    niveau = modele.split(" ", 1)[0].upper()
    return PRIORITE_NIVEAU.get(niveau, 3)


def reduire_logs(lignes, max_modeles: int = MAX_MODELES) -> tuple[list[dict], str]:
    """Modèles triés (erreurs d'abord, puis par compte), au plus max_modeles ; et le réducteur utilisé."""
    lignes = list(lignes)
    reducteur = "maison"
    try:
        module = importlib.import_module("drain3")
        modeles = _modeles_drain3(module, lignes)
        reducteur = "drain3"
    except Exception:
        modeles = _modeles_maison(lignes)
    tries = sorted(modeles.items(), key=lambda element: (_priorite(element[0]), -element[1], element[0]))
    return [{"template": modele, "compte": compte} for modele, compte in tries[:max_modeles]], reducteur
