"""Diagnostic explicite de la connexion Meterless et du modèle effectif."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests

from rappel.config import charger
from rappel.interfaces.llm import MeterlessLLM


def main():
    try:
        configuration = charger()
        llm = MeterlessLLM(configuration)
        reponse = requests.get(
            f"{configuration.meterless_base_url.rstrip('/')}/models",
            headers=llm.entetes(),
            timeout=5,
        )
        reponse.raise_for_status()
        catalogue = reponse.json()
        print("Modèles disponibles :")
        for modele in catalogue["data"]:
            print(f"- {modele['id']}")
        resultat = llm.completer("Réponds simplement : connexion opérationnelle.", "detective")
        modele_effectif = resultat.get("modele") or "non communiqué"
        print(f"Réponse : {resultat.get('texte', '')}")
        print(f"Modèle effectif : {modele_effectif}")
        if "nemotron" not in str(modele_effectif).lower():
            print("AVERTISSEMENT : le modèle effectif n'est pas un Nemotron confirmé.")
        return 0
    except (requests.RequestException, ValueError, TypeError, KeyError, RuntimeError):
        print("Connexion Meterless impossible. Vérifiez le service et sa configuration.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
