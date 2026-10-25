"""Client HTTP minimal de l'API Rappel pour l'interface (aucun détail d'exception exposé)."""

import requests

DELAI_S = 5


class ErreurApi(Exception):
    """Erreur assainie : statut HTTP éventuel et message sans détail technique."""

    def __init__(self, message: str, statut: int | None = None):
        super().__init__(message)
        self.statut = statut


class ClientApi:
    def __init__(self, url_base: str, session=None, delai_s: float = DELAI_S):
        self.url_base = url_base.rstrip("/")
        self.session = session or requests
        self.delai_s = delai_s

    def _appel(self, methode: str, chemin: str, **options):
        try:
            reponse = getattr(self.session, methode)(f"{self.url_base}{chemin}", timeout=self.delai_s, **options)
        except requests.RequestException:
            raise ErreurApi("API coupée ou indisponible.") from None
        statut = getattr(reponse, "status_code", 200)
        if statut >= 400:
            detail = None
            try:
                corps = reponse.json()
                detail = corps.get("detail") if isinstance(corps, dict) else None
            except ValueError:
                pass
            message = detail if isinstance(detail, str) and len(detail) <= 200 else "requête refusée"
            raise ErreurApi(f"Erreur API {statut} : {message}", statut)
        try:
            return reponse.json()
        except ValueError:
            raise ErreurApi("Réponse de l'API illisible.") from None

    def sante(self) -> dict:
        return self._appel("get", "/healthz")

    def creer_depuis_demo(self, incident_id: str) -> dict:
        return self._appel("post", "/incidents/depuis-demo", json={"incident_id": incident_id})

    def lire_incident(self, incident_id: str) -> dict:
        return self._appel("get", f"/incidents/{incident_id}")

    def fil(self, incident_id: str, apres: int = 0, jusqua: int | None = None) -> list:
        parametres = {"apres": apres} if jusqua is None else {"apres": apres, "jusqua": jusqua}
        donnees = self._appel("get", f"/incidents/{incident_id}/enquete/evenements", params=parametres)
        if not isinstance(donnees, list):
            raise ErreurApi("Format d'événements invalide.")
        return [evenement for evenement in donnees if isinstance(evenement, dict)]

    def comparaison(self, a: str, b: str) -> dict:
        return self._appel("get", "/comparaison", params={"a": a, "b": b})

    def decision(self, incident_id: str, decision: str) -> dict:
        return self._appel("post", f"/correctifs/{incident_id}/decision", json={"decision": decision})

    def graphe(self, incident_id: str | None = None) -> dict:
        return self._appel("get", "/graphe" if incident_id is None else f"/graphe/{incident_id}")
