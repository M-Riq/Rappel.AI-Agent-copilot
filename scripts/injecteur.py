"""Raccourci : python scripts/injecteur.py p1|p2|p3|reset (sans PYTHONPATH)."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rappel.pipeline_demo.injecteur import main

if __name__ == "__main__":
    raise SystemExit(main())
