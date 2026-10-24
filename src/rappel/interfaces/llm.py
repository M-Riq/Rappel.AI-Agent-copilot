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


CONSEILS_METERLESS = {
    "non_autorise": "jeton Runtime Nightshift incorrect (ne pas utiliser la clé NVIDIA) : vérifier "
                    "METERLESS_RUNTIME_TOKEN ; le jeton de démonstration rt-local est refusé",
    "interdit": "jeton reconnu mais refusé pour cette application : vérifier ses droits dans la console Nightshift",
    "introuvable": "vérifier METERLESS_BASE_URL (racine compatible OpenAI en /v1) et les identifiants "
                   "METERLESS_MODEL_RAPIDE / METERLESS_MODEL_PROFOND (scripts/detecter_nightshift.py)",
    "requete": "requête refusée par Nightshift : vérifier l'identifiant de voie ou de modèle demandé",
    "quota": "quota ou crédits insuffisants côté Nightshift / NVIDIA : vérifier la console, ne pas relancer "
             "sans l'accord de l'équipe",
    "serveur": "Nightshift a échoué : vérifier la clé NVIDIA configurée côté Nightshift, puis réessayer",
    "hors_ligne": "Nightshift ne répond pas : vérifier qu'il est démarré, le port et METERLESS_BASE_URL",
    "delai": "délai METERLESS_DELAI_S dépassé : modèle lent ou service surchargé",
    "redirection": "le jeton n'est envoyé qu'à l'hôte configuré : corriger METERLESS_BASE_URL",
    "coupe_circuit": "trop d'échecs consécutifs : attendre une minute puis lancer scripts/check_meterless.py",
    "trop_volumineux": "réponse anormalement volumineuse refusée",
}


def politique_reseau(configuration: Configuration, delai_lecture_s: float):
    from rappel.interfaces.client_http import PolitiqueReseau

    return PolitiqueReseau(
        delai_connexion_s=configuration.reseau_delai_connexion_s, delai_lecture_s=delai_lecture_s,
        relances=configuration.reseau_relances, attente_base_s=configuration.reseau_attente_base_s,
        seuil_coupe_circuit=configuration.reseau_seuil_coupe_circuit,
    )


def _texte_message(contenu) -> str:
    """Contenu OpenAI : chaîne, None, ou liste de parties {type: text, text: ...}."""
    if contenu is None:
        return ""
    if isinstance(contenu, str):
        return contenu
    if isinstance(contenu, list):
        return "".join(partie.get("text", "") for partie in contenu if isinstance(partie, dict)
                       and isinstance(partie.get("text"), str))
    raise TypeError("contenu de message inattendu")


class MeterlessLLM:
    facture = True  # appels comptés dans LLM_MAX_APPELS_SESSION

    def __init__(self, configuration: Configuration | None = None, client=None):
        from rappel.interfaces.client_http import ClientService

        self.configuration = configuration or charger()
        self.client = client or ClientService(
            "Meterless Nightshift", politique_reseau(self.configuration, self.configuration.meterless_delai_s),
            CONSEILS_METERLESS, secrets=(self.configuration.meterless_runtime_token,),
        )

    def __repr__(self):
        return f"MeterlessLLM(base={self.configuration.meterless_base_url!r})"

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
        modele = self.modele_pour_role(role)
        self.dernier_modele_repondu = None
        corps = {"model": modele, "messages": [{"role": "user", "content": prompt}]}
        if tools is not None:
            corps["tools"] = tools
        entetes = self.entetes()
        racine = url_meterless(self.configuration)
        # Pas de redirection, relances limitées et coupe-circuit : voir client_http.
        reponse = self.client.requete("post", f"{racine}/chat/completions", headers=entetes, json=corps)
        try:
            donnees = reponse.json()
            message = donnees["choices"][0]["message"]
            texte = _texte_message(message.get("content"))
            appels = message.get("tool_calls") or []
            if not isinstance(appels, list):
                raise TypeError("tool_calls inattendu")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise RuntimeError("Réponse Meterless Nightshift invalide.") from None
        # Valeur réellement renvoyée par le service, sans repli sur le modèle demandé.
        modele_repondu = donnees.get("model")
        self.dernier_modele_repondu = modele_repondu if isinstance(modele_repondu, str) else None
        return {
            "texte": texte,
            "tool_calls": appels,
            "modele": self.dernier_modele_repondu or modele,
            "usage": donnees.get("usage") if isinstance(donnees.get("usage"), dict) else {},
        }


def completer(prompt, role, tools=None):
    configuration = charger()
    fournisseur = (
        FakeLLM() if configuration.llm_provider == "fake" else MeterlessLLM(configuration)
    )
    return fournisseur.completer(prompt, role, tools)
