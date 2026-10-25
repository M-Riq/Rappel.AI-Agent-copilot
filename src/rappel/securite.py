"""Sécurité transverse : masquage des secrets et validation des URL de services.

- masquer_secrets : retire de tout texte les valeurs des variables d'environnement sensibles et
  les identifiants intégrés dans une URL (schema://utilisateur:motdepasse@hote).
- url_service : valide et normalise une URL de service (http/https seulement, hôte obligatoire,
  aucun identifiant dans l'URL) avant d'y envoyer un jeton.
"""

import ipaddress
import logging
import os
from pathlib import Path
import re
import tempfile
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


def borner(objet, longueur_texte: int = 4000, elements: int = 50):
    """Copie bornée d'une structure JSON : textes tronqués, listes et dictionnaires limités."""
    if isinstance(objet, str):
        return objet if len(objet) <= longueur_texte else objet[:longueur_texte] + "...[tronque]"
    if isinstance(objet, list):
        return [borner(element, longueur_texte, elements) for element in objet[:elements]]
    if isinstance(objet, dict):
        return {cle: borner(valeur, longueur_texte, elements) for cle, valeur in list(objet.items())[:elements]}
    return objet


_FABRIQUE_INSTALLEE = {"fait": False}


def installer_filtre_journal() -> None:
    """Masque les secrets de tout enregistrement de journal des modules rappel.* dès sa création.

    Une fabrique d'enregistrements couvre aussi les journaux enfants (rappel.interfaces...), ce que
    ne fait pas un filtre posé sur le seul journal « rappel ».
    """
    if _FABRIQUE_INSTALLEE["fait"]:
        return
    precedente = logging.getLogRecordFactory()

    def fabrique(*args, **kwargs):
        enregistrement = precedente(*args, **kwargs)
        if enregistrement.name == "rappel" or enregistrement.name.startswith("rappel."):
            try:
                message = enregistrement.getMessage()
            except Exception:
                message = str(enregistrement.msg)
            enregistrement.msg, enregistrement.args = masquer_secrets(message), None
        return enregistrement

    logging.setLogRecordFactory(fabrique)
    _FABRIQUE_INSTALLEE["fait"] = True


def ecrire_texte_atomique(chemin, texte: str) -> None:
    """Écrit via un fichier temporaire du même dossier puis le remplace : jamais de fichier tronqué (Ctrl+C)."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    descripteur, temporaire = tempfile.mkstemp(dir=chemin.parent, prefix=f".{chemin.name}.", suffix=".tmp")
    try:
        with os.fdopen(descripteur, "w", encoding="utf-8", newline="") as fichier:
            fichier.write(texte)
        os.replace(temporaire, chemin)
    finally:
        if os.path.exists(temporaire):
            os.unlink(temporaire)
