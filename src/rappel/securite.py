"""Sécurité transverse : masquage des secrets et validation des URL de services.

- masquer_secrets : retire de tout texte les valeurs des variables d'environnement sensibles et
  les identifiants intégrés dans une URL (schema://utilisateur:motdepasse@hote).
- url_service : valide et normalise une URL de service (http/https seulement, hôte obligatoire,
  aucun identifiant dans l'URL) avant d'y envoyer un jeton.
"""

import ipaddress
import os
import re
from urllib.parse import urlsplit, urlunsplit

MOTS_SENSIBLES = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "DATABASE_URL", "JETON")
MASQUE = "[MASQUE]"
_IDENTIFIANTS_URL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@")
_JETON_PORTEUR = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
HOTES_LOCAUX = ("localhost",)
SCHEMAS_AUTORISES = ("http", "https")


def masquer_secrets(texte) -> str:
    """Texte sans valeur secrète connue, sans identifiant d'URL et sans jeton « Bearer »."""
    texte = str(texte)
    valeurs = sorted((valeur for nom, valeur in os.environ.items()
                      if valeur and len(valeur) >= 4 and any(mot in nom.upper() for mot in MOTS_SENSIBLES)),
                     key=len, reverse=True)
    for valeur in valeurs:
        texte = texte.replace(valeur, MASQUE)
    texte = _IDENTIFIANTS_URL.sub(lambda trouve: f"{trouve.group(1)}{MASQUE}@", texte)
    return _JETON_PORTEUR.sub(lambda trouve: f"{trouve.group(1)} {MASQUE}", texte)


def est_hote_local(hote: str | None) -> bool:
    if not hote:
        return False
    if hote.lower() in HOTES_LOCAUX:
        return True
    try:
        return ipaddress.ip_address(hote.strip("[]")).is_loopback
    except ValueError:
        return False


def url_service(url: str, nom: str, suffixes_a_retirer: tuple[str, ...] = (),
                chemin_api: str | None = None) -> str:
    """URL de base validée et normalisée (sans « / » final).

    Sans schéma : http:// pour un hôte local, https:// sinon. Les suffixes d'endpoint collés par
    erreur (ex. /chat/completions, /models, /healthz) sont retirés ; chemin_api (ex. « /v1 ») est
    ajouté une seule fois s'il manque. Lève ValueError avec un message sans secret.
    """
    texte = (url or "").strip()
    if not texte:
        raise ValueError(f"{nom} est vide : renseigner l'URL du service dans .env.")
    if "://" not in texte:
        hote = re.split(r"[/:]", texte.lstrip("["), maxsplit=1)[0].rstrip("]")
        texte = ("http://" if est_hote_local(hote) or texte.startswith("[::1]") else "https://") + texte
    try:
        parties = urlsplit(texte)
        port = parties.port
    except ValueError:
        raise ValueError(f"{nom} n'est pas une URL valide.") from None
    if parties.scheme.lower() not in SCHEMAS_AUTORISES:
        raise ValueError(f"{nom} : schéma « {parties.scheme} » refusé (http ou https uniquement).")
    if not parties.hostname:
        raise ValueError(f"{nom} : hôte manquant.")
    if parties.username or parties.password:
        raise ValueError(f"{nom} : aucun identifiant ne doit figurer dans l'URL (utiliser la variable de jeton).")
    if parties.query or parties.fragment:
        raise ValueError(f"{nom} : l'URL ne doit contenir ni paramètres ni fragment.")
    chemin = re.sub(r"/{2,}", "/", parties.path).rstrip("/")
    modifie = True
    while modifie:
        modifie = False
        for suffixe in suffixes_a_retirer:
            if chemin.lower().endswith(suffixe.lower()):
                chemin = chemin[: -len(suffixe)].rstrip("/")
                modifie = True
    if chemin_api:
        while chemin.lower().endswith(chemin_api.lower() + chemin_api.lower()):
            chemin = chemin[: -len(chemin_api)]
        if not chemin.lower().endswith(chemin_api.lower()):
            chemin += chemin_api
    hote = parties.hostname if ":" not in parties.hostname else f"[{parties.hostname}]"
    reseau = hote + (f":{port}" if port else "")
    return urlunsplit((parties.scheme.lower(), reseau, chemin, "", ""))
