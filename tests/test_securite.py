import json
import re
import subprocess

from rappel.config import RACINE
from rappel.securite import masquer_secrets


def test_masquage_ne_casse_pas_les_litteraux_json(monkeypatch):
    monkeypatch.setenv("EXEMPLE_SECRET", "true")
    texte = masquer_secrets('{"actif": true, "libelle": "true"}')
    assert json.loads(texte) == {"actif": True, "libelle": "true"}


def test_aucun_secret_dans_les_fichiers_suivis():
    chemins = subprocess.check_output(
        ["git", "-C", str(RACINE), "ls-files", "-z"]
    ).decode().split("\0")
    prefixe_interdit = "nvapi" + "-"
    motif_jeton = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})")
    for chemin in filter(None, chemins):
        contenu = (RACINE / chemin).read_text(encoding="utf-8")
        assert prefixe_interdit not in contenu, chemin
        assert not motif_jeton.search(contenu), chemin
