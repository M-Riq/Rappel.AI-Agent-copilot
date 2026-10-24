"""Page Streamlit : enquête en direct en trois zones (pipeline, fil, résultat et compteur)."""

from pathlib import Path
import sys

RACINE_UI = Path(__file__).resolve().parents[1]
for chemin in (RACINE_UI, RACINE_UI.parent / "src"):
    if str(chemin) not in sys.path:
        sys.path.insert(0, str(chemin))

import streamlit as st

from rappel.config import charger
import zones

st.title("Rappel — enquête en direct")
try:
    configuration = charger()
except (ValueError, TypeError):
    st.error("Configuration invalide. Vérifiez les paramètres de l'application.")
else:
    zones.afficher(configuration.api_url)
