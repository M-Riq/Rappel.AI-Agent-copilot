"""Serveur JSON Lines synchrone pour la suite officielle de conformité H-MEM."""

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
import sys

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "src"))

from rappel.hmem import HMEM, ConflictRecord


def serialiser(valeur):
    if is_dataclass(valeur):
        return serialiser(asdict(valeur))
    if isinstance(valeur, dict):
        return {cle: serialiser(element) for cle, element in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [serialiser(element) for element in valeur]
    return valeur


def executer(requete):
    horloge = [requete["now"]]
    moteur = HMEM(
        clock=lambda: horloge[0],
        persist_path=requete["path"],
        actor=requete.get("actor", "conformance"),
    )
    try:
        operation, donnees = requete["op"], requete.get("data", {})
        if operation == "add":
            resultat = moteur.add(donnees)
        elif operation == "query":
            resultat = moteur.query(donnees["query"], donnees.get("opts"))
        elif operation == "feedback":
            resultat = moteur.feedback(donnees["id"], donnees["kind"])
        elif operation == "get":
            resultat = moteur.store.get(donnees["id"])
        elif operation == "all":
            resultat = moteur.store.all()
        elif operation == "set":
            resultat = moteur.store.update(donnees["id"], {donnees["key"]: donnees["value"]})
        elif operation == "sleep.preview":
            resultat = moteur.sleep.preview()
        elif operation == "sleep.execute":
            resultat = moteur.sleep.execute(donnees.get("preview"))
        elif operation == "sleep.restore":
            resultat = moteur.sleep.restore(donnees["backupId"])
        elif operation == "conflicts.scan":
            resultat = moteur.conflicts.scan()
        elif operation == "conflicts.autoResolve":
            resultat = moteur.conflicts.autoResolve(ConflictRecord(**donnees["conflict"]))
        elif operation == "ledger.history":
            resultat = moteur.ledger.history(donnees["id"])
        elif operation == "ledger.all":
            resultat = moteur.ledger.all()
        elif operation == "dreaming.dream":
            resultat = moteur.dreaming.dream()
        elif operation == "dreaming.pending":
            resultat = moteur.dreaming.pending()
        elif operation == "dreaming.approve":
            resultat = moteur.dreaming.approve(donnees["id"])
        elif operation == "dreaming.reject":
            resultat = moteur.dreaming.reject(donnees["id"], donnees.get("reason"))
        else:
            raise ValueError("Opération H-MEM serveur inconnue.")
        return {"ok": True, "result": serialiser(resultat)}
    finally:
        moteur.close()


def main():
    for ligne in sys.stdin:
        try:
            reponse = executer(json.loads(ligne))
        except Exception as erreur:
            reponse = {"ok": False, "error": str(erreur) or "Erreur H-MEM."}
        sys.stdout.write(json.dumps(reponse, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
