"""Mémoire JSON locale ou Postgres, sans charger psycopg en mode léger."""

import json
import os
import tempfile
from pathlib import Path
from threading import RLock

from rappel.config import RACINE, charger
from rappel.contrats import valider

_VERROU = RLock()


class MemoireJSON:
    def __init__(self, chemin):
        chemin = Path(chemin)
        self.chemin = chemin if chemin.is_absolute() else RACINE / chemin

    def _lire(self):
        if not self.chemin.exists():
            return []
        donnees = json.loads(self.chemin.read_text(encoding="utf-8"))
        if not isinstance(donnees, list):
            raise ValueError("La mémoire JSON doit contenir une liste.")
        for enregistrement in donnees:
            valider("enregistrement_memoire", enregistrement)
        return donnees

    def chercher(self, signature):
        with _VERROU:
            return [element for element in self._lire() if element["signature"] == signature]

    def ecrire(self, enregistrement):
        valider("enregistrement_memoire", enregistrement)
        with _VERROU:
            donnees = self._lire()
            donnees.append(enregistrement)
            self.chemin.parent.mkdir(parents=True, exist_ok=True)
            temporaire = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.chemin.parent, delete=False
                ) as fichier:
                    temporaire = fichier.name
                    json.dump(donnees, fichier, ensure_ascii=False, indent=2)
                os.replace(temporaire, self.chemin)
            finally:
                if temporaire and os.path.exists(temporaire):
                    os.unlink(temporaire)


class MemoirePostgres:
    def __init__(self, database_url):
        self.database_url = database_url

    def _connecter(self):
        try:
            import psycopg
        except ImportError:
            raise RuntimeError("Installer requirements-demo.txt pour la mémoire Postgres.") from None
        try:
            return psycopg.connect(self.database_url, connect_timeout=5)
        except psycopg.Error:
            raise RuntimeError("Connexion à la mémoire Postgres impossible.") from None

    def chercher(self, signature):
        with self._connecter() as connexion, connexion.cursor() as curseur:
            curseur.execute(
                "SELECT signature, cause_confirmee, hypotheses_refutees, correctif_essaye, "
                "resultat, date FROM memoire_incidents WHERE signature = %s ORDER BY id",
                (signature,),
            )
            noms = ("signature", "cause_confirmee", "hypotheses_refutees", "correctif_essaye", "resultat", "date")
            enregistrements = [dict(zip(noms, ligne)) for ligne in curseur.fetchall()]
            for element in enregistrements:
                element["date"] = element["date"].isoformat()
            return enregistrements

    def ecrire(self, enregistrement):
        valider("enregistrement_memoire", enregistrement)
        with self._connecter() as connexion, connexion.cursor() as curseur:
            curseur.execute(
                "INSERT INTO memoire_incidents "
                "(signature, cause_confirmee, hypotheses_refutees, correctif_essaye, resultat, date) "
                "VALUES (%s, %s, %s::jsonb, %s, %s, %s)",
                (
                    enregistrement["signature"],
                    enregistrement["cause_confirmee"],
                    json.dumps(enregistrement["hypotheses_refutees"], ensure_ascii=False),
                    enregistrement["correctif_essaye"],
                    enregistrement["resultat"],
                    enregistrement["date"],
                ),
            )


def _memoire():
    configuration = charger()
    if configuration.memoire_provider == "json":
        return MemoireJSON(configuration.memoire_json_path)
    return MemoirePostgres(configuration.database_url)


def chercher(signature):
    return _memoire().chercher(signature)


def ecrire(enregistrement):
    _memoire().ecrire(enregistrement)
