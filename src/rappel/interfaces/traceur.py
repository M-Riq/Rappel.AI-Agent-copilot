from copy import deepcopy
import importlib
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Protocol

from dotenv import load_dotenv

from ..contrats import EvenementEnquete


LOGGER = logging.getLogger(__name__)
_verrou_configuration = Lock()
_avertissement_configuration_emis = False


class Traceur(Protocol):
    def demarrer(self, incident_id: str, signature: str) -> None: ...

    def evenement(self, evenement: EvenementEnquete) -> None: ...

    def terminer(self, metriques: dict, decision: str) -> None: ...


class TraceurNul:
    def demarrer(self, incident_id: str, signature: str) -> None:
        pass

    def evenement(self, evenement: EvenementEnquete) -> None:
        pass

    def terminer(self, metriques: dict, decision: str) -> None:
        pass


class TraceurProtege:
    def __init__(self, traceur: Traceur) -> None:
        self._traceur = traceur
        self.nb_erreurs = 0
        self._averti = False
        self._verrou = Lock()

    def _appeler(self, methode: str, *arguments) -> None:
        try:
            getattr(self._traceur, methode)(*arguments)
        except Exception:
            self._signaler_erreur()

    def _signaler_erreur(self) -> None:
        with self._verrou:
            self.nb_erreurs += 1
            if not self._averti:
                self._averti = True
                LOGGER.warning("Erreur de tracage ignoree ; l'enquete continue sans detail sensible.")

    def demarrer(self, incident_id: str, signature: str) -> None:
        self._appeler("demarrer", incident_id, signature)

    def evenement(self, evenement: EvenementEnquete) -> None:
        self._appeler("evenement", evenement.model_copy(deep=True))

    def terminer(self, metriques: dict, decision: str) -> None:
        self._appeler("terminer", deepcopy(metriques), decision)


class _AdaptateurLangfuseSDK:
    # A verifier dans la documentation et le code de la version installee.
    def __init__(self, module, public_key: str, secret_key: str, host: str) -> None:
        raise NotImplementedError("Liaison SDK Langfuse non verifiee")

    def demarrer(self, donnees: dict) -> None:
        raise NotImplementedError

    def evenement(self, donnees: dict) -> None:
        raise NotImplementedError

    def terminer(self, donnees: dict) -> None:
        raise NotImplementedError


class TraceurLangfuse(TraceurProtege):
    def __init__(self, client, envoyer_textes: bool | None = None) -> None:
        super().__init__(TraceurNul())
        self._client = client
        self.envoyer_textes = (
            os.getenv("LANGFUSE_ENVOYER_TEXTES", "1").strip() != "0"
            if envoyer_textes is None else envoyer_textes
        )

    def demarrer(self, incident_id: str, signature: str) -> None:
        self._envoyer("demarrer", {"incident_id": incident_id, "signature": signature})

    def evenement(self, evenement: EvenementEnquete) -> None:
        donnees = {"type": evenement.type, "acteur": evenement.acteur, "etape": evenement.etape}
        if self.envoyer_textes:
            donnees["texte"] = evenement.texte
        self._envoyer("evenement", donnees)

    def terminer(self, metriques: dict, decision: str) -> None:
        self._envoyer("terminer", {"metriques": deepcopy(metriques), "decision": decision})

    def _envoyer(self, methode: str, donnees: dict) -> None:
        try:
            getattr(self._client, methode)(donnees)
        except Exception:
            self._signaler_erreur()


def proteger_traceur(traceur: Traceur | None) -> Traceur:
    if traceur is None:
        return TraceurNul()
    if type(traceur) in (TraceurNul, TraceurProtege, TraceurLangfuse):
        return traceur
    return TraceurProtege(traceur)


def _avertir_configuration() -> None:
    global _avertissement_configuration_emis
    with _verrou_configuration:
        if not _avertissement_configuration_emis:
            _avertissement_configuration_emis = True
            LOGGER.warning("Tracage Langfuse inactif : configuration ou liaison SDK indisponible.")


def creer_traceur() -> Traceur:
    try:
        load_dotenv(dotenv_path=Path(".env"), override=False)
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        host = os.getenv("LANGFUSE_HOST", "").strip()
        if not public_key or not secret_key:
            _avertir_configuration()
            return TraceurNul()
        module = importlib.import_module("langfuse")
        client = _AdaptateurLangfuseSDK(module, public_key, secret_key, host)
        return TraceurLangfuse(client)
    except Exception:
        _avertir_configuration()
        return TraceurNul()