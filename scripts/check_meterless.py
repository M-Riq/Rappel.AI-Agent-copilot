"""Diagnostic explicite de la connexion Meterless et du modèle effectif."""

import argparse
from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rappel.config import charger
from rappel.interfaces.llm import MeterlessLLM, MeterlessInjoignable, MeterlessNonAutorise


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
    except requests.RequestException:
        print("Connexion Meterless impossible. Vérifiez le service et sa configuration.")
        return 1
    except (RuntimeError, ValueError):
        print("Erreur de configuration du Runtime Meterless.")
        return 1

    statut = connexion["statut"]
    print(f"Statut de connexion : {statut}")
    print(connexion["message"])
    if statut != "ok":
        return {"non_autorise": 2, "injoignable": 3}.get(statut, 1)
    print("Modèles disponibles :")
    for modele in connexion["modeles"]:
        suffixe = " (mode de routage Meterless)" if modele.startswith("meterless/") else ""
        print(f"- {modele}{suffixe}")
    if arguments.sans_chat:
        return 0

    try:
        resultat = llm.completer("Réponds simplement : connexion opérationnelle.", "detective")
    except MeterlessNonAutorise:
        print("Test de chat non autorisé par le Runtime Meterless.")
        return 2
    except MeterlessInjoignable:
        print("Runtime Meterless injoignable pendant le test de chat.")
        return 3
    except (RuntimeError, ValueError):
        print("Erreur pendant le test de chat Meterless.")
        return 1

    modele_effectif = resultat.get("modele") or "non communiqué"
    print(f"Réponse : {resultat.get('texte', '')}")
    print(f"Modèle effectif : {modele_effectif}")
    if "nemotron" not in str(modele_effectif).lower():
        print("AVERTISSEMENT : le modèle effectif n'est pas un Nemotron confirmé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
