"""Diagnostic explicite de la connexion Meterless et du modèle effectif.

  python scripts/check_meterless.py             # connexion, modèles, puis un petit chat (appel facturé)
  python scripts/check_meterless.py --sans-chat # connexion et découverte des modèles seulement

Codes de sortie : 0 ok, 2 non autorisé (401/403), 3 injoignable (hors ligne, délai), 1 autre erreur.
Le jeton n'est jamais affiché ; un 401 reste « non autorisé », jamais « hors ligne ».
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests

from rappel.config import charger
from rappel.interfaces.llm import MeterlessLLM, MeterlessInjoignable, MeterlessNonAutorise
from rappel.securite import masquer_secrets


def afficher(texte) -> None:
    print(masquer_secrets(texte))


def main(argv=None):
    analyseur = argparse.ArgumentParser(description="Teste le Runtime Meterless Nightshift.")
    analyseur.add_argument(
        "--sans-chat",
        action="store_true",
        help="Teste uniquement la connexion et la découverte des modèles.",
    )
    arguments = analyseur.parse_args(argv if argv is not None else [])
    try:
        configuration = charger()
        llm = MeterlessLLM(configuration)
        connexion = llm.tester_connexion()
    except (requests.RequestException, RuntimeError, ValueError):
        afficher("Connexion Meterless impossible ; vérifiez la configuration et le service.")
        return 1

    statut = connexion["statut"]
    afficher(f"Statut de connexion : {statut}")
    afficher(connexion["message"])
    if statut != "ok":
        return {"non_autorise": 2, "injoignable": 3}.get(statut, 1)
    afficher("Modèles disponibles :")
    for modele in connexion["modeles"]:
        suffixe = " (mode de routage Meterless)" if modele.startswith("meterless/") else ""
        afficher(f"- {modele}{suffixe}")
    if arguments.sans_chat:
        return 0

    try:
        resultat = llm.completer("Réponds simplement : connexion opérationnelle.", "detective")
    except MeterlessNonAutorise:
        afficher("Test de chat non autorisé par le Runtime Meterless.")
        return 2
    except MeterlessInjoignable:
        afficher("Runtime Meterless injoignable pendant le test de chat.")
        return 3
    except (RuntimeError, ValueError):
        afficher("Erreur pendant le test de chat Meterless.")
        return 1

    modele_effectif = resultat.get("modele") or "non communiqué"
    afficher(f"Réponse : {resultat.get('texte', '')}")
    afficher(f"Modèle effectif : {modele_effectif}")
    if "nemotron" not in str(modele_effectif).lower():
        afficher("AVERTISSEMENT : le modèle effectif n'est pas un Nemotron confirmé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
