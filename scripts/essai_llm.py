import os
from pathlib import Path
import sys
from time import perf_counter

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rappel.interfaces.llm import LLMAvecSecours, creer_llm


def main() -> None:
    load_dotenv()
    fournisseur = os.getenv("LLM_PROVIDER", "faux").strip().lower() or "faux"
    llm = creer_llm()
    principal = llm.principal if isinstance(llm, LLMAvecSecours) else llm
    debut = perf_counter()
    try:
        llm.completer("Réponds uniquement par: connexion réussie", role="detective")
        duree = (perf_counter() - debut) * 1000
        usage = llm.usage
        print(f"Fournisseur : {fournisseur}")
        print(f"Modèle : {getattr(principal, 'modele', 'aucun')}")
        print(f"Durée : {duree:.1f} ms")
        print(f"Tokens entrée : {usage.tokens_entree}")
        print(f"Tokens sortie : {usage.tokens_sortie}")
    finally:
        fournisseurs = (llm.principal, llm.secours) if isinstance(llm, LLMAvecSecours) else (llm,)
        for fournisseur_llm in fournisseurs:
            fermer = getattr(fournisseur_llm, "fermer", None)
            if callable(fermer):
                fermer()


if __name__ == "__main__":
    main()