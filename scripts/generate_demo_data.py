"""Transactions financières synthétiques reproductibles pour la démo Rappel.

Aucune donnée réelle : identifiants fictifs, montants et dates tirés avec une graine fixe.
Usage : python scripts/generate_demo_data.py [--lignes 500]
"""

import argparse
import csv
import hashlib
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
SORTIE = RACINE / "pipeline_demo" / "donnees"
GRAINE = 20261027
COLONNES = ("transaction_id", "buyer_id", "amount", "recipient_id", "transaction_date", "status")
STATUTS = ("completed", "pending", "failed")
POIDS_STATUTS = (0.85, 0.10, 0.05)
# Contrat de schéma attendu par le pipeline (P1 : « amount » renommé en « total » côté observé).
CONTRAT = (
    ("transactions", "transaction_id", "string", True),
    ("transactions", "buyer_id", "string", True),
    ("transactions", "amount", "decimal(12,2)", True),
    ("transactions", "recipient_id", "string", True),
    ("transactions", "transaction_date", "timestamp", True),
    ("transactions", "status", "string", True),
)


def generer(nombre: int, graine: int = GRAINE) -> list[dict]:
    if not 100 <= nombre <= 1000:
        raise ValueError("Le nombre de transactions doit être compris entre 100 et 1000.")
    alea = random.Random(graine)
    debut = datetime(2026, 10, 1, tzinfo=timezone.utc)
    acheteurs = [f"B{i:04d}" for i in range(1, 121)]
    beneficiaires = [f"R{i:04d}" for i in range(1, 61)]
    brutes = []
    for _ in range(nombre):
        montant = min(max(alea.lognormvariate(3.5, 1.0), 1.0), 5000.0)  # médiane ~33, plafond 5000
        brutes.append({
            "buyer_id": alea.choice(acheteurs),
            "amount": f"{montant:.2f}",
            "recipient_id": alea.choice(beneficiaires),
            "transaction_date": debut + timedelta(seconds=alea.randint(0, 26 * 24 * 3600)),
            "status": alea.choices(STATUTS, POIDS_STATUTS)[0],
        })
    brutes.sort(key=lambda ligne: ligne["transaction_date"])
    return [
        {
            "transaction_id": f"T{rang:06d}",
            **ligne,
            "transaction_date": ligne["transaction_date"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        for rang, ligne in enumerate(brutes, 1)
    ]


def ecrire_csv(chemin: Path, colonnes, lignes) -> str:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", encoding="utf-8", newline="") as fichier:
        ecrivain = csv.DictWriter(fichier, fieldnames=colonnes, lineterminator="\n")
        ecrivain.writeheader()
        ecrivain.writerows(lignes)
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--lignes", type=int, default=500)
    arguments = parseur.parse_args()
    transactions = generer(arguments.lignes)
    empreinte = ecrire_csv(SORTIE / "transactions.csv", COLONNES, transactions)
    contrat = [dict(zip(("dataset_name", "column_name", "expected_type", "required"),
                        (jeu, colonne, type_, str(requis).lower())))
               for jeu, colonne, type_, requis in CONTRAT]
    ecrire_csv(SORTIE / "schema_contract.csv", ("dataset_name", "column_name", "expected_type", "required"), contrat)
    print(f"{len(transactions)} transactions -> {SORTIE / 'transactions.csv'} (sha256 {empreinte[:12]})")
    print(f"{len(contrat)} colonnes de contrat -> {SORTIE / 'schema_contract.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())