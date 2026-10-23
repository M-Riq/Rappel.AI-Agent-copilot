import argparse
from pathlib import Path
import sys

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from dotenv import load_dotenv

from scripts._essai_commun import afficher, fermer
from src.rappel.interfaces.llm import (
    LLMBudgetEpuise, LLMIndisponible, LLMNightshift, VOIE_PROFONDE, VOIE_RAPIDE,
)


MOT_ATTENDU = "nemotron"
# Un role representatif par voie (aiguillage de LLMNightshift.completer).
ROLES_PAR_VOIE = {VOIE_RAPIDE: "hypotheses", VOIE_PROFONDE: "conclusion"}
VARIABLES_VOIE = {VOIE_RAPIDE: "NIGHTSHIFT_VOIE_RAPIDE", VOIE_PROFONDE: "NIGHTSHIFT_VOIE_PROFONDE"}
MESSAGE_ESSAI = "Reponds uniquement par : ok"


def contient_nemotron(modele: str | None) -> bool:
    return bool(modele) and MOT_ATTENDU in modele.lower()


def verifier(llm) -> tuple[int, dict]:
    rapport = {"modeles_nemotron": None, "voies": {}, "a_corriger": []}
    try:
        modeles = llm.lister_modeles()
        rapport["modeles_nemotron"] = [modele for modele in modeles if contient_nemotron(modele)]
        afficher(f"GET /models : {len(modeles)} modele(s), dont nemotron : {rapport['modeles_nemotron']}")
    except LLMIndisponible as erreur:
        afficher(f"GET /models impossible : {erreur}")
        rapport["a_corriger"].append(f"liste des modeles indisponible ({erreur})")
    for voie, role in ROLES_PAR_VOIE.items():
        try:
            llm.completer(MESSAGE_ESSAI, role=role)
            info = llm.derniere_reponse_info or {}
            repondu = info.get("modele_repondu")
            rapport["voies"][voie] = {"modele_demande": info.get("modele_demande"), "modele_repondu": repondu}
            afficher(f"Voie {voie} ({VARIABLES_VOIE[voie]}, role {role}) : modele demande "
                     f"{info.get('modele_demande')} ; modele repondu {repondu or 'non renvoye'}")
            if not contient_nemotron(repondu):
                rapport["a_corriger"].append(f"la voie {voie} repond avec {repondu or 'un modele non renvoye'}")
        except (LLMIndisponible, LLMBudgetEpuise) as erreur:
            rapport["voies"][voie] = {"erreur": str(erreur)}
            afficher(f"Voie {voie} : aucune reponse ({erreur})")
            rapport["a_corriger"].append(f"la voie {voie} ne repond pas ({erreur})")
    if rapport["a_corriger"]:
        for raison in rapport["a_corriger"]:
            afficher(f"A CORRIGER : {raison}")
        return 1, rapport
    afficher("OK : les deux voies repondent avec un modele nemotron.")
    return 0, rapport


def main(argv=None, client=None):
    parseur = argparse.ArgumentParser(
        description="Controle Meterless Nightshift : modeles nemotron et modele reellement repondu par voie."
    )
    parseur.add_argument("--confirmer", action="store_true", help="Autoriser les appels reels (3 requetes)")
    arguments = parseur.parse_args(argv)
    load_dotenv(override=False)
    afficher("Appels prevus : GET /models + 1 message par voie (2 appels LLM).")
    if not arguments.confirmer:
        afficher("REFUS : appel reel a Meterless Nightshift. Relancer avec --confirmer pour autoriser la depense.")
        return 2
    llm = None
    try:
        llm = LLMNightshift(client)
        code, _ = verifier(llm)
        return code
    except ValueError as erreur:
        # Messages de configuration : noms de variables uniquement, jamais leurs valeurs.
        afficher(f"Configuration incomplete : {erreur}")
        return 1
    except Exception:
        afficher("Controle impossible : verifier la configuration Nightshift. Aucun detail sensible affiche.")
        return 1
    finally:
        if llm is not None:
            fermer(llm)


if __name__ == "__main__":
    raise SystemExit(main())
