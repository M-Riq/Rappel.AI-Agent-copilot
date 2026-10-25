"""Rappel, copilote d'enquête d'incidents ML/DevOps."""

from rappel.securite import installer_filtre_journal

# Aucun secret dans les journaux des modules rappel.* (valeurs sensibles, URL, jetons).
installer_filtre_journal()
