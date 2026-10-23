from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from ..contrats import EnregistrementMemoire, Hypothese


class Memoire(Protocol):
    def lister(self) -> list[EnregistrementMemoire]: ...

    def vider(self) -> int: ...

    def chercher(self, signature: str) -> list[EnregistrementMemoire]: ...

    def ecrire(self, enregistrement: EnregistrementMemoire) -> None: ...

    def hypotheses_refutees(self, signature: str) -> list[str]: ...

    def mettre_a_jour_resultat(
        self, signature: str, resultat: Literal["reussi", "echoue"]
    ) -> bool: ...

    def ecrire_hypotheses(
        self, incident_id: str, signature: str, hypotheses: list[Hypothese]
    ) -> None: ...


class MemoireJSON:
    def __init__(self, chemin: str | Path = "data/memoire.json") -> None:
        self.chemin = Path(chemin)
        # Table "hypotheses" : fichier JSON separe, a cote du fichier des incidents.
        self.chemin_hypotheses = self.chemin.with_name(f"{self.chemin.stem}_hypotheses.json")
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        if not self.chemin.exists():
            self.chemin.write_text("[]\n", encoding="utf-8")

    def _lire(self) -> list[EnregistrementMemoire]:
        donnees = json.loads(self.chemin.read_text(encoding="utf-8"))
        if not isinstance(donnees, list):
            raise ValueError("La memoire JSON doit contenir une liste d'enregistrements")
        return [EnregistrementMemoire.model_validate(donnee) for donnee in donnees]

    def chercher(self, signature: str) -> list[EnregistrementMemoire]:
        return [
            enregistrement
            for enregistrement in self._lire()
            if enregistrement.signature == signature
        ]

    def lister(self) -> list[EnregistrementMemoire]:
        return self._lire()

    def vider(self) -> int:
        nombre = len(self._lire())
        self.chemin.write_text("[]\n", encoding="utf-8")
        if self.chemin_hypotheses.exists():
            self.chemin_hypotheses.write_text("[]\n", encoding="utf-8")
        return nombre

    def lister_hypotheses(self, signature: str | None = None) -> list[dict]:
        if not self.chemin_hypotheses.exists():
            return []
        lignes = json.loads(self.chemin_hypotheses.read_text(encoding="utf-8"))
        if not isinstance(lignes, list):
            raise ValueError("Le fichier des hypotheses doit contenir une liste")
        return [ligne for ligne in lignes if signature is None or ligne.get("signature") == signature]

    def ecrire_hypotheses(
        self, incident_id: str, signature: str, hypotheses: list[Hypothese]
    ) -> None:
        cree_le = datetime.now(timezone.utc).isoformat()
        lignes = self.lister_hypotheses()
        for hypothese in hypotheses:
            hypothese = Hypothese.model_validate(hypothese.model_dump())
            lignes.append({
                "id": uuid4().hex, "hypothese_id": hypothese.id, "incident_id": incident_id,
                "signature": signature, "texte": hypothese.texte, "statut": hypothese.statut,
                "preuves": hypothese.preuves, "confiance": hypothese.confiance,
                "raison_rejet": hypothese.raison_rejet, "cree_le": cree_le,
            })
        self.chemin_hypotheses.write_text(
            json.dumps(lignes, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
        )

    def ecrire(self, enregistrement: EnregistrementMemoire) -> None:
        enregistrements = self._lire()
        enregistrements.append(enregistrement)
        contenu = json.dumps(
            [element.model_dump() for element in enregistrements],
            indent=2,
            ensure_ascii=True,
        )
        self.chemin.write_text(contenu + "\n", encoding="utf-8")

    def hypotheses_refutees(self, signature: str) -> list[str]:
        hypotheses = [
            hypothese
            for enregistrement in self.chercher(signature)
            for hypothese in enregistrement.hypotheses_refutees
        ]
        return list(dict.fromkeys(hypotheses))

    def mettre_a_jour_resultat(
        self, signature: str, resultat: Literal["reussi", "echoue"]
    ) -> bool:
        if resultat not in {"reussi", "echoue"}:
            raise ValueError("resultat doit etre reussi ou echoue")
        enregistrements = self._lire()
        for indice in range(len(enregistrements) - 1, -1, -1):
            if enregistrements[indice].signature == signature:
                enregistrements[indice].resultat = resultat
                contenu = json.dumps(
                    [element.model_dump() for element in enregistrements],
                    indent=2, ensure_ascii=True,
                )
                self.chemin.write_text(contenu + "\n", encoding="utf-8")
                return True
        return False