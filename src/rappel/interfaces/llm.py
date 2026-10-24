"""Fournisseurs LLM : simulation locale ou Runtime Meterless Nightshift."""

import requests

from rappel.config import Configuration, charger
from rappel.interfaces import _faux_sql
from rappel.securite import url_service

# Suffixes d'endpoint parfois collés par erreur à METERLESS_BASE_URL.
SUFFIXES_METERLESS = ("/chat/completions", "/models", "/healthz")


def url_meterless(configuration: Configuration) -> str:
    """Racine OpenAI-compatible validée : http(s), hôte, aucun identifiant, « /v1 » une seule fois."""
    return url_service(configuration.meterless_base_url, "METERLESS_BASE_URL", SUFFIXES_METERLESS, "/v1")

# Rôles de la boucle d'enquête SQL ; les autres rôles gardent leur comportement.
ROLES_ENQUETE_RAPIDES = ("hypotheses", "verdict")
ROLES_ENQUETE_PROFONDS = ("conclusion", "correctif")


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

    def entetes(self):
        if not self.configuration.meterless_runtime_token:
            raise ValueError("METERLESS_RUNTIME_TOKEN est requis pour Nightshift.")
        return {
            "Authorization": "Bearer " + self.configuration.meterless_runtime_token,
            "meterless-app": "rappel",
            "meterless-scope": "user",
        }

    def modele_pour_role(self, role):
        configuration = self.configuration
        if role in ("detective", "outil") + ROLES_ENQUETE_RAPIDES:
            lane = configuration.meterless_model_rapide
        elif role in ("sceptique",) + ROLES_ENQUETE_PROFONDS:
            lane = configuration.meterless_model_profond
        else:
            lane = ""
        return lane or configuration.meterless_model

    def completer(self, prompt, role, tools=None):
        configuration = self.configuration
        modele = self.modele_pour_role(role)
        self.dernier_modele_repondu = None
        corps = {"model": modele, "messages": [{"role": "user", "content": prompt}]}
        if tools is not None:
            corps["tools"] = tools
        entetes = self.entetes()
        racine = url_meterless(configuration)
        try:
            # Pas de redirection : le jeton n'est envoyé qu'à l'hôte configuré.
            reponse = requests.post(
                f"{racine}/chat/completions",
                headers=entetes,
                json=corps,
                timeout=60,
                allow_redirects=False,
            )
            if reponse.status_code == 401:
                raise RuntimeError(
                    "jeton Runtime Nightshift incorrect (ne pas utiliser la clé NVIDIA)"
                )
            reponse.raise_for_status()
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
        except requests.RequestException:
            raise RuntimeError("Meterless Nightshift est indisponible ou a refusé la requête.") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise RuntimeError("Réponse Meterless Nightshift invalide.") from None


def completer(prompt, role, tools=None):
    configuration = charger()
    fournisseur = (
        FakeLLM() if configuration.llm_provider == "fake" else MeterlessLLM(configuration)
    )
    return fournisseur.completer(prompt, role, tools)
