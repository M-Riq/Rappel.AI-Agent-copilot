"""Client HTTP durci pour les services réels (Meterless Nightshift, Zetaris).

- délais séparés connexion / lecture ;
- aucune redirection suivie (le jeton n'est envoyé qu'à l'hôte configuré) ;
- relances limitées avec attente croissante, seulement pour les erreurs passagères
  (hors ligne, délai dépassé, quota 429, erreur serveur 5xx) ;
- coupe-circuit : après N échecs passagers consécutifs, plus aucun appel pendant une durée fixe ;
- erreurs classées (ErreurService.categorie) avec un message actionnable en français, sans secret.
"""

from dataclasses import dataclass
import time

import requests

from rappel.securite import masquer_secrets

PASSAGERES = ("hors_ligne", "delai", "quota", "serveur")
LONGUEUR_DETAIL = 200
TAILLE_MAX_REPONSE = 5 * 1024 * 1024
ATTENTE_MAX_S = 30.0

LIBELLES = {
    "non_autorise": "non autorisé (401)",
    "interdit": "accès refusé (403)",
    "introuvable": "adresse introuvable (404)",
    "requete": "requête refusée",
    "quota": "quota ou crédits insuffisants (429)",
    "serveur": "erreur du service (5xx)",
    "hors_ligne": "indisponible (hors ligne)",
    "delai": "indisponible (délai dépassé)",
    "redirection": "redirection refusée",
    "reponse_invalide": "réponse invalide",
    "trop_volumineux": "réponse trop volumineuse",
    "coupe_circuit": "indisponible (coupe-circuit ouvert après plusieurs échecs)",
    "table_inconnue": "table inconnue",
    "syntaxe_sql": "dialecte ou syntaxe SQL refusé",
}


class ErreurService(RuntimeError):
    """Erreur classée d'un service réel ; le message ne contient jamais de secret."""

    def __init__(self, service: str, categorie: str, conseil: str = "", statut: int | None = None,
                 detail: str | None = None) -> None:
        self.service, self.categorie, self.statut = service, categorie, statut
        self.detail = None if detail is None else masquer_secrets(detail)[:LONGUEUR_DETAIL]
        message = f"{service} : {LIBELLES.get(categorie, categorie)}"
        if self.detail:
            message += f" ({self.detail})"
        if conseil:
            message += f" — {conseil}"
        super().__init__(masquer_secrets(message))


@dataclass(frozen=True)
class PolitiqueReseau:
    delai_connexion_s: float = 10.0
    delai_lecture_s: float = 180.0
    relances: int = 2
    attente_base_s: float = 1.0
    seuil_coupe_circuit: int = 3
    duree_coupure_s: float = 60.0


def detail_reponse(reponse) -> str | None:
    """Message d'erreur du service (champ error/message/detail), assaini et tronqué."""
    try:
        corps = reponse.json()
    except Exception:
        corps = None
    if isinstance(corps, dict):
        for cle in ("error", "message", "detail", "erreur"):
            valeur = corps.get(cle)
            if isinstance(valeur, dict):
                valeur = valeur.get("message")
            if isinstance(valeur, str) and valeur.strip():
                return masquer_secrets(" ".join(valeur.split()))[:LONGUEUR_DETAIL]
    texte = getattr(reponse, "text", None)
    if isinstance(texte, str) and texte.strip():
        return masquer_secrets(" ".join(texte.split()))[:LONGUEUR_DETAIL]
    return None


class ClientService:
    def __init__(self, service: str, politique: PolitiqueReseau | None = None, conseils: dict | None = None,
                 dormir=time.sleep, horloge=time.monotonic, secrets: tuple = ()) -> None:
        self.service = service
        # Jeton ou clé du service : masqués dans tout message, même s'ils ne sont pas dans l'environnement.
        self._secrets = tuple(secret for secret in secrets if secret)
        self.politique = politique or PolitiqueReseau()
        self.conseils = conseils or {}
        self._dormir, self._horloge = dormir, horloge
        self.echecs_consecutifs = 0
        self.ouvert_jusqua = 0.0
        self.nb_tentatives = 0

    def __repr__(self) -> str:
        return f"ClientService(service={self.service!r}, echecs_consecutifs={self.echecs_consecutifs})"

    def erreur(self, categorie: str, statut: int | None = None, detail: str | None = None) -> ErreurService:
        for secret in self._secrets:
            detail = None if detail is None else detail.replace(secret, "[MASQUE]")
        return ErreurService(self.service, categorie, self.conseils.get(categorie, ""), statut, detail)

    def _classer(self, reponse) -> str | None:
        statut = reponse.status_code
        if not isinstance(statut, int):
            return None
        if 200 <= statut < 300:
            return None
        if 300 <= statut < 400:
            return "redirection"
        return {401: "non_autorise", 403: "interdit", 404: "introuvable", 429: "quota"}.get(
            statut, "serveur" if statut >= 500 else "requete")

    def requete(self, methode: str, url: str, *, headers: dict, json=None, relances: int | None = None):
        """Réponse 2xx ou ErreurService ; jamais d'exception brute de requests."""
        if self.echecs_consecutifs >= self.politique.seuil_coupe_circuit and self._horloge() < self.ouvert_jusqua:
            raise self.erreur("coupe_circuit")
        relances = self.politique.relances if relances is None else relances
        derniere = None
        for tentative in range(relances + 1):
            if tentative:
                self._dormir(min(ATTENTE_MAX_S, self.politique.attente_base_s * 2 ** (tentative - 1)))
            self.nb_tentatives += 1
            try:
                options = {"headers": headers, "timeout": (self.politique.delai_connexion_s, self.politique.delai_lecture_s),
                           "allow_redirects": False}
                if json is not None:
                    options["json"] = json
                reponse = getattr(requests, methode)(url, **options)
            except requests.Timeout:
                derniere = self.erreur("delai")
                continue
            except requests.RequestException:
                derniere = self.erreur("hors_ligne")
                continue
            categorie = self._classer(reponse)
            if categorie is None:
                contenu = getattr(reponse, "content", b"")
                if isinstance(contenu, (bytes, bytearray)) and len(contenu) > TAILLE_MAX_REPONSE:
                    raise self.erreur("trop_volumineux")
                self.echecs_consecutifs = 0
                return reponse
            erreur = self.erreur(categorie, reponse.status_code,
                                 None if categorie in ("non_autorise", "interdit") else detail_reponse(reponse))
            if categorie not in PASSAGERES:
                raise erreur
            derniere = erreur
        self.echecs_consecutifs += 1
        if self.echecs_consecutifs >= self.politique.seuil_coupe_circuit:
            self.ouvert_jusqua = self._horloge() + self.politique.duree_coupure_s
        raise derniere
