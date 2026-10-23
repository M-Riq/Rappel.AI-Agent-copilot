"""Diagnostic de Zetaris, indépendamment du fournisseur par défaut."""

from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests

from rappel.config import charger
from rappel.interfaces.tools_zetaris import run_sql


def main():
    try:
        configuration = replace(charger(), source_provider="zetaris")
        resultat = run_sql("SELECT 1 AS one", configuration=configuration)
        print(json.dumps(
            {"requete": resultat["requete"], "lignes": resultat["lignes"]},
            ensure_ascii=False,
        ))
        return 0
    except (requests.RequestException, ValueError, TypeError, KeyError, RuntimeError):
        print("Connexion Zetaris impossible. Vérifiez le service et sa configuration.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
