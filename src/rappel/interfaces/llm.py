"""Fournisseurs LLM : simulation locale ou Runtime Meterless Nightshift."""

from urllib.parse import urlsplit, urlunsplit

import requests

from rappel.config import Configuration, charger
from rappel.interfaces import _faux_sql

# Rôles de la boucle d'enquête SQL ; les autres rôles gardent leur comportement.
ROLES_ENQUETE_RAPIDES = ("hypotheses", "verdict")
ROLES_ENQUETE_PROFONDS = ("conclusion", "correctif")


class MeterlessNonAutorise(RuntimeError):
    """Le Runtime refuse le jeton Meterless."""


class MeterlessInjoignable(RuntimeError):
    """Le Runtime Meterless ne peut pas être joint."""


def normaliser_url_base(url):
    """Ramène une URL Meterless à son origine et son unique suffixe /v1."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("METERLESS_BASE_URL doit contenir une URL.")
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    morceaux = urlsplit(url)
    if not morceaux.netloc:
        raise ValueError("METERLESS_BASE_URL doit contenir un hôte valide.")

    chemin = morceaux.path.rstrip("/")
    for suffixe in ("/v1/chat/completions", "/v1/models", "/healthz"):
        if chemin.endswith(suffixe):
            chemin = chemin[: -len(suffixe)]
            break
    while chemin.endswith("/v1"):
        chemin = chemin[:-3]
    return urlunsplit((morceaux.scheme, morceaux.netloc, chemin + "/v1", "", "")).rstrip("/")


class FakeLLM:
    def modele_pour_role(self, role):
        if role in ROLES_ENQUETE_RAPIDES:
            return "faux-rapide"
        if role in ROLES_ENQUETE_PROFONDS:
            return "faux-profond"
        return "fake-deterministe"

    def completer(self, prompt, role, tools=None):
        if role in ROLES_ENQUETE_RAPIDES + ROLES_ENQUETE_PROFONDS:
            from rappel.enquete.protocoles import lire_donnees

            try:
                donnees = lire_donnees(prompt)
            except ValueError:
                donnees = None
            # Sans bloc DONNEES du moteur, la simulation historique s'applique.
            if donnees is not None:
                return {
                    "texte": _faux_sql.repondre(role, donnees),
                    "tool_calls": [],
                    "modele": self.modele_pour_role(role),
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
        return {
            "texte": f"[Simulation {role}] Vérifier les preuves avant de conclure.",
            "tool_calls": [],
            "modele": "fake-deterministe",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }


class MeterlessLLM:
    def __init__(self, configuration: Configuration | None = None):
        self.configuration = configuration or charger()
        self.dernier_modele_repondu = None

    def entetes(self):
        jeton = self.configuration.meterless_runtime_token
        if not jeton or not jeton.strip() or jeton == "rt-local":
            raise ValueError("METERLESS_RUNTIME_TOKEN est requis pour Nightshift.")
        return {
            "Authorization": "Bearer " + jeton,
            "meterless-app": "rappel",
            "meterless-scope": "user",
        }

    @staticmethod
    def _requete(methode, url, **parametres):
        try:
            reponse = methode(url, **parametres)
        except (requests.ConnectionError, requests.Timeout):
            raise MeterlessInjoignable("Runtime Meterless injoignable (indisponible).") from None
        except requests.RequestException:
            raise RuntimeError("Erreur de communication avec le Runtime Meterless.") from None
        if reponse.status_code in (401, 403):
            raise MeterlessNonAutorise(
                "jeton Runtime Nightshift incorrect ou non autorisé "
                "(ne pas utiliser la clé NVIDIA ; utiliser le jeton Runtime de la Console Nightshift)."
            )
        try:
            reponse.raise_for_status()
        except requests.HTTPError:
            raise RuntimeError("Erreur HTTP du Runtime Meterless.") from None
        return reponse

    def lister_modeles(self):
        reponse = self._requete(
            requests.get,
            normaliser_url_base(self.configuration.meterless_base_url) + "/models",
            headers=self.entetes(),
            timeout=10,
        )
        try:
            donnees = reponse.json()
            modeles = donnees["data"]
            if not isinstance(modeles, list):
                raise TypeError
            return [modele["id"] for modele in modeles if isinstance(modele, dict) and isinstance(modele.get("id"), str)]
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("Réponse Meterless Nightshift invalide.") from None

    def tester_connexion(self):
        jeton = self.configuration.meterless_runtime_token
        if not jeton or not jeton.strip() or jeton == "rt-local":
            return {
                "statut": "non_autorise",
                "modeles": [],
                "message": "Jeton Runtime Nightshift manquant ou placeholder.",
            }
        try:
            modeles = self.lister_modeles()
        except MeterlessNonAutorise as erreur:
            return {"statut": "non_autorise", "modeles": [], "message": str(erreur)}
        except MeterlessInjoignable as erreur:
            return {"statut": "injoignable", "modeles": [], "message": str(erreur)}
        except ValueError as erreur:
            return {"statut": "erreur", "modeles": [], "message": str(erreur)}
        except RuntimeError as erreur:
            return {"statut": "erreur", "modeles": [], "message": str(erreur)}
        return {"statut": "ok", "modeles": modeles, "message": "Connexion Runtime Meterless opérationnelle."}

    def modele_pour_role(self, role):
        if role in ("detective", "outil") + ROLES_ENQUETE_RAPIDES:
            lane = self.configuration.meterless_model_rapide
        elif role in ("sceptique",) + ROLES_ENQUETE_PROFONDS:
            lane = self.configuration.meterless_model_profond
        else:
            lane = ""
        return lane or self.configuration.meterless_model

    def completer(self, prompt, role, tools=None):
        configuration = self.configuration
        modele = self.modele_pour_role(role)
        self.dernier_modele_repondu = None
        corps = {"model": modele, "messages": [{"role": "user", "content": prompt}]}
        if tools is not None:
            corps["tools"] = tools
        entetes = self.entetes()
        url = normaliser_url_base(configuration.meterless_base_url) + "/chat/completions"
        try:
            reponse = self._requete(
                requests.post,
                url,
                headers=entetes,
                json=corps,
                timeout=180,
            )
            donnees = reponse.json()
            message = donnees["choices"][0]["message"]
            # Valeur réellement renvoyée par le service, sans repli sur le modèle demandé.
            self.dernier_modele_repondu = donnees.get("model")
            return {
                "texte": message.get("content") or "",
                "tool_calls": message.get("tool_calls") or [],
                "modele": donnees.get("model") or modele,
                "usage": donnees.get("usage") or {},
            }
        except (ValueError, KeyError, IndexError, TypeError):
            raise RuntimeError("Réponse Meterless Nightshift invalide.") from None


def completer(prompt, role, tools=None):
    configuration = charger()
    fournisseur = (
        FakeLLM() if configuration.llm_provider == "fake" else MeterlessLLM(configuration)
    )
    return fournisseur.completer(prompt, role, tools)
