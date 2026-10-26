"""Page Streamlit du fil d'enquête."""

from pathlib import Path
import sys

RACINE_UI = Path(__file__).resolve().parents[1]
for chemin in (RACINE_UI, RACINE_UI.parent / "src"):
    if str(chemin) not in sys.path:
        sys.path.insert(0, str(chemin))

import zones

zones.afficher_page("enquete")
