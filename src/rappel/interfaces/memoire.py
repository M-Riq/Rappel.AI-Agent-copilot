import json
from pathlib import Path
from typing import Protocol

from ..contrats import EnregistrementMemoire


class Memoire(Protocol):
    def chercher(self, signature: str) -> list[EnregistrementMemoire]: ...

    def ecrire(self, enregistrement: EnregistrementMemoire) -> None: ...


class MemoireJSON:
    def __init__(self, chemin: str | Path = "data/memoire.json") -> None:
        self.chemin = Path(chemin)
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