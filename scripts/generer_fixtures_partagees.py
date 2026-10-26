import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "src"))

from rappel.enquete.modeles import ContexteIncident, CorrectifPropose, EvenementEnquete
from rappel.enquete import enqueter
from rappel.interfaces.abstention import AbstentionSeuil
from rappel.enquete.adaptateurs import LLMFaux
from rappel.enquete.adaptateurs import MemoireEnquete


def generer_documents():
    contexte = ContexteIncident.model_validate_json(
        (RACINE / "fixtures" / "contexte_p1.json").read_text(encoding="utf-8")
    )
    with TemporaryDirectory() as dossier:
        resultat = enqueter(
            contexte, MemoireEnquete.json(Path(dossier) / "memoire.json"),
            LLMFaux(), AbstentionSeuil(), war_room_active=False, mode_preuves="sql",
        )
    if resultat.correctif is None:
        raise ValueError("L'enquete P1 ne produit plus de correctif")
    correctif = CorrectifPropose.model_validate(resultat.correctif.model_dump())
    origine = datetime.fromisoformat(contexte.horodatage)
    evenements = []
    for indice, evenement in enumerate(resultat.evenements):
        # Horodatages synthetiques : une seconde par emission depuis le contexte.
        donnees = {
            **evenement.model_dump(),
            "horodatage": (origine + timedelta(seconds=indice)).isoformat(),
        }
        # Contrat evenement_enquete : modele et requete_sql absents plutot que null.
        evenements.append(EvenementEnquete.model_validate(donnees).model_dump(mode="json", exclude_none=True))
    return {"correctif_p1.json": correctif.model_dump(), "evenements_p1.json": evenements}


def generer_octets():
    return {
        nom: (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        for nom, document in generer_documents().items()
    }


def main(argv=None, destination=None):
    parseur = argparse.ArgumentParser(
        description="Generer les sorties reelles P1 avec horodatages synthetiques deterministes."
    )
    parseur.add_argument("--verifier", action="store_true", help="Comparer sans reecrire")
    arguments = parseur.parse_args(argv)
    # Sorties du moteur dans fixtures/moteur : les fixtures de l'equipe ne sont jamais ecrasees.
    dossier = RACINE / "fixtures" / "moteur" if destination is None else Path(destination)
    try:
        documents = generer_octets()
        if arguments.verifier:
            perimes = [nom for nom, contenu in documents.items()
                       if not (dossier / nom).is_file() or (dossier / nom).read_bytes() != contenu]
            if perimes:
                print("Fixtures absentes ou perimees : " + ", ".join(perimes))
                return 1
            print("OK : les deux fixtures correspondent a l'enquete actuelle.")
            return 0
        dossier.mkdir(parents=True, exist_ok=True)
        for nom, contenu in documents.items():
            (dossier / nom).write_bytes(contenu)
            print("Genere et valide : " + nom)
        print("Horodatages synthetiques : pas d'une seconde depuis l'horodatage du contexte.")
        return 0
    except Exception:
        print("Generation impossible : verifier le contexte et le resultat P1 ; aucun detail sensible affiche.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())