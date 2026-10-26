"""Faux services locaux pour les tests de contrat (bibliothèque standard, 127.0.0.1, port éphémère).

FAUX SERVICE LOCAL, NON UNE PREUVE SPONSOR : ces serveurs imitent l'interface de Meterless
Nightshift (OpenAI-compatible), de l'API REST Zetaris et d'un pilote Postgres pour tester les
VRAIS adaptateurs de bout en bout. Aucun résultat obtenu contre eux ne prouve l'usage d'un sponsor.
"""

from contextlib import contextmanager
import os
from urllib.parse import urlsplit

import requests

from tests.faux_services.nightshift import FauxNightshift
from tests.faux_services.postgres import FauxPsycopg
from tests.faux_services.zetaris import FauxZetaris

ETIQUETTE = "faux service local, NON une preuve sponsor"
# Session.request réelle, capturée avant que le conftest n'interdise le réseau.
_REQUETE_ORIGINALE = requests.sessions.Session.request
HOTES_AUTORISES = ("127.0.0.1", "localhost", "::1")


def _requete_locale(session, method, url, *args, **kwargs):
    if urlsplit(url).hostname not in HOTES_AUTORISES:
        raise AssertionError("Appel réseau hors 127.0.0.1 interdit dans les tests.")
    return _REQUETE_ORIGINALE(session, method, url, *args, **kwargs)


@contextmanager
def reseau_local(monkeypatch):
    """Autorise uniquement le réseau de bouclage (faux services) pendant un test."""
    monkeypatch.setattr(requests.sessions.Session, "request", _requete_locale)
    yield


def environnement_faux_services(monkeypatch, nightshift=None, zetaris=None, psycopg=None):
    """Variables d'environnement pointant vers les faux services ; RAPPEL_FAUX_SERVICES=1 l'indique."""
    monkeypatch.setenv("RAPPEL_FAUX_SERVICES", "1")
    if nightshift is not None:
        for nom, valeur in (("METERLESS_BASE_URL", nightshift.url + "/v1"), ("METERLESS_RUNTIME_TOKEN", nightshift.jeton),
                            ("METERLESS_MODEL_RAPIDE", "meterless/fast"), ("METERLESS_MODEL_PROFOND", "meterless/deep")):
            monkeypatch.setenv(nom, valeur)
    if zetaris is not None:
        for nom, valeur in (("ZETARIS_BASE_URL", zetaris.url + "/api/v1.0"), ("ZETARIS_API_KEY", zetaris.cle),
                            ("ZETARIS_ORG_ID", zetaris.org_id), ("ZETARIS_CHEMIN_SQL", zetaris.chemin),
                            ("ZETARIS_CLE_SQL", zetaris.cle_sql)):
            monkeypatch.setenv(nom, valeur)
    if psycopg is not None:
        import sys

        monkeypatch.setitem(sys.modules, "psycopg", psycopg)
        monkeypatch.setenv("DATABASE_URL", "postgresql://rappel:" + "factice-local@127.0.0.1:5432/rappel")
    return os.environ


__all__ = ["ETIQUETTE", "FauxNightshift", "FauxPsycopg", "FauxZetaris", "environnement_faux_services", "reseau_local"]
