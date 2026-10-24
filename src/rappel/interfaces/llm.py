"""Fournisseurs LLM : simulation locale ou Runtime Meterless Nightshift."""

# requests : module partagé avec client_http (les tests remplacent requests.get / requests.post).
import requests  # noqa: F401

from rappel.config import Configuration, charger
from rappel.interfaces import _faux_sql
from rappel.interfaces.client_http import ErreurService
from rappel.securite import masquer_secrets, url_service

# Suffixes d'endpoint parfois collés par erreur à METERLESS_BASE_URL.
SUFFIXES_METERLESS = ("/chat/completions", "/models", "/healthz")


JETON_DEMO_REFUSE = "rt-local"


def normaliser_url_base(url) -> str:
    """Racine OpenAI-compatible validée : http(s), hôte, aucun identifiant, « /v1 » une seule fois.

    Définition unique (la plus stricte) : url_meterless et les diagnostics passent par elle.
    """
    if not isinstance(url, str):
        raise ValueError("METERLESS_BASE_URL doit contenir une URL.")
    return url_service(url, "METERLESS_BASE_URL", SUFFIXES_METERLESS, "/v1")


def url_meterless(configuration: Configuration) -> str:
    return normaliser_url_base(configuration.meterless_base_url)

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


class MeterlessNonAutorise(ErreurService):
    """Le Runtime refuse le jeton Meterless (401 ou 403) : jamais confondu avec « hors ligne »."""


class MeterlessInjoignable(ErreurService):
    """Le Runtime Meterless ne peut pas être joint (hors ligne ou délai dépassé)."""


CATEGORIES_NON_AUTORISE = ("non_autorise", "interdit")
CATEGORIES_INJOIGNABLE = ("hors_ligne", "delai")


def _traduire(erreur: ErreurService) -> ErreurService:
    """Erreur du client réseau traduite en exceptions de diagnostic (même message, sans secret)."""
    classe = (MeterlessNonAutorise if erreur.categorie in CATEGORIES_NON_AUTORISE else
              MeterlessInjoignable if erreur.categorie in CATEGORIES_INJOIGNABLE else None)
    if classe is None:
        return erreur
    return classe(erreur.service, erreur.categorie, erreur.conseil, erreur.statut, erreur.detail)


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


DELAI_DECOUVERTE_S = 10.0


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
        self.dernier_modele_repondu = None
        self._decouverte = None
        self.client = client or ClientService(
            "Meterless Nightshift", politique_reseau(self.configuration, self.configuration.meterless_delai_s),
            CONSEILS_METERLESS, secrets=(self.configuration.meterless_runtime_token,),
        )

    def __repr__(self):
        return f"MeterlessLLM(base={self.configuration.meterless_base_url!r})"

    def jeton_utilisable(self) -> bool:
        jeton = self.configuration.meterless_runtime_token
        return bool(jeton and jeton.strip()) and jeton.strip() != JETON_DEMO_REFUSE

    def entetes(self):
        # Le jeton de démonstration rt-local est refusé localement : aucune requête ne part avec lui.
        if not self.jeton_utilisable():
            raise ValueError("METERLESS_RUNTIME_TOKEN est requis pour Nightshift.")
        return {
            "Authorization": "Bearer " + self.configuration.meterless_runtime_token,
            "meterless-app": "rappel",
            "meterless-scope": "user",
        }

    def _client_decouverte(self):
        """Client des diagnostics : même politique (relances, coupe-circuit, pas de redirection), lecture 10 s."""
        from rappel.interfaces.client_http import ClientService

        if self._decouverte is None:
            self._decouverte = ClientService(
                "Meterless Nightshift", politique_reseau(self.configuration, DELAI_DECOUVERTE_S), CONSEILS_METERLESS,
                secrets=(self.configuration.meterless_runtime_token,),
            )
        return self._decouverte

    def lister_modeles(self):
        """Identifiants de GET /v1/models (tous conservés, dans l'ordre du service)."""
        entetes = self.entetes()
        try:
            reponse = self._client_decouverte().requete("get", f"{url_meterless(self.configuration)}/models",
                                                        headers=entetes)
        except ErreurService as erreur:
            raise _traduire(erreur) from None
        try:
            modeles = reponse.json()["data"]
            if not isinstance(modeles, list):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("Réponse Meterless Nightshift invalide.") from None
        return [modele["id"] for modele in modeles if isinstance(modele, dict) and isinstance(modele.get("id"), str)]

    def tester_connexion(self):
        """Statut ok / non_autorise / injoignable / erreur et modèles découverts ; aucun appel facturé."""
        if not self.jeton_utilisable():
            return {"statut": "non_autorise", "modeles": [],
                    "message": "Jeton Runtime Nightshift manquant ou placeholder."}
        try:
            modeles = self.lister_modeles()
        except MeterlessNonAutorise as erreur:
            return {"statut": "non_autorise", "modeles": [], "message": str(erreur)}
        except MeterlessInjoignable as erreur:
            return {"statut": "injoignable", "modeles": [], "message": str(erreur)}
        except (ValueError, RuntimeError) as erreur:
            return {"statut": "erreur", "modeles": [], "message": masquer_secrets(str(erreur))}
        return {"statut": "ok", "modeles": modeles, "message": "Connexion Runtime Meterless opérationnelle."}

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
        try:
            reponse = self.client.requete("post", f"{racine}/chat/completions", headers=entetes, json=corps)
        except ErreurService as erreur:
            raise _traduire(erreur) from None
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
