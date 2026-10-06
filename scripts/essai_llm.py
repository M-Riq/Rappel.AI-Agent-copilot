import argparse
import os
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._essai_commun import afficher, autoriser, fermer
from src.rappel.interfaces.llm import LLMAvecSecours, creer_llm


def main(argv=None, fabrique_llm=None) -> int:
    parseur = argparse.ArgumentParser(description="Essai manuel de connexion LLM.")
    parseur.add_argument("--confirmer", action="store_true")
    arguments = parseur.parse_args(argv)
    if not autoriser(arguments.confirmer, appels_prevus=2):
        return 2
    llm = None
    try:
        fournisseur = os.getenv("LLM_PROVIDER", "faux").strip().lower() or "faux"
        llm = (fabrique_llm or creer_llm)()
        principal = llm.principal if isinstance(llm, LLMAvecSecours) else llm
        debut = perf_counter()
        llm.completer("Réponds uniquement par: connexion réussie", role="detective")
        duree = (perf_counter() - debut) * 1000
        usage = llm.usage
        afficher(f"Fournisseur : {fournisseur}")
        afficher(f"Modèle : {getattr(principal, 'modele', 'aucun')}")
        afficher(f"Durée : {duree:.1f} ms")
        afficher(f"Tokens entrée : {usage.tokens_entree}")
        afficher(f"Tokens sortie : {usage.tokens_sortie}")
        return 0
    except Exception:
        afficher("Essai impossible : verifier la configuration et le budget. Aucun detail sensible affiche.")
        return 1
    finally:
        if llm is not None:
            fermer(llm)


if __name__ == "__main__":
    raise SystemExit(main())