from dataclasses import dataclass
from contextlib import contextmanager
import json
import os
from pathlib import Path
from threading import Lock
from typing import Protocol

import httpx
from dotenv import load_dotenv

from ..contrats import Hypothese


NVIDIA_BASE_URL_PAR_DEFAUT = "https://integrate.api.nvidia.com/v1"
VERSION_ANTHROPIC = "2023-06-01"


class LLMIndisponible(RuntimeError):
    """Fournisseur LLM inaccessible ou réponse non conforme."""


class LLMBudgetEpuise(RuntimeError):
    """Plafond d'appels LLM de l'exécution atteint."""


@contextmanager
def limiter_appels(llm, restants: int):
    budget = getattr(llm, "_budget", None)
    if budget is None:
        yield
        return
    maximum = budget.maximum
    budget.maximum = min(maximum, budget.appels + restants)
    try:
        yield
    finally:
        budget.maximum = maximum


@dataclass
class UsageLLM:
    tokens_entree: int | None = 0
    tokens_sortie: int | None = 0
    nb_appels: int = 0
    # Compteurs separes par voie ("rapide" / "profonde") pour les fournisseurs a deux voies.
    par_voie: dict[str, "UsageLLM"] | None = None


VOIE_RAPIDE = "rapide"
VOIE_PROFONDE = "profonde"
VOIE_UNIQUE = "unique"
ROLES_VOIE_PROFONDE = frozenset({"conclusion", "correctif"})
# Les prompts structures se terminent par ce marqueur suivi d'un objet JSON de donnees.
MARQUEUR_DONNEES = "\nDONNEES:\n"


def voie_pour_role(role: str) -> str:
    return VOIE_PROFONDE if role in ROLES_VOIE_PROFONDE else VOIE_RAPIDE


def usage_par_voie() -> dict[str, UsageLLM]:
    return {VOIE_RAPIDE: UsageLLM(), VOIE_PROFONDE: UsageLLM()}


class LLM(Protocol):
    usage: UsageLLM

    def completer(self, prompt: str, role: str = "detective") -> str: ...


class _BudgetAppels:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.appels = 0
        self.verrou = Lock()

    def commencer(self) -> None:
        with self.verrou:
            self.appels = 0

    def consommer(self) -> None:
        with self.verrou:
            if self.appels >= self.maximum:
                raise LLMBudgetEpuise("Plafond LLM_MAX_APPELS atteint pour cette exécution")
            self.appels += 1


def _entier_env(nom: str, defaut: int) -> int:
    valeur = os.getenv(nom, "").strip()
    if not valeur:
        return defaut
    try:
        nombre = int(valeur)
    except ValueError:
        raise ValueError(f"{nom} doit être un entier") from None
    if nombre < 1:
        raise ValueError(f"{nom} doit être supérieur ou égal à 1")
    return nombre


def _flottant_env(nom: str, defaut: float) -> float:
    valeur = os.getenv(nom, "").strip()
    if not valeur:
        return defaut
    try:
        return float(valeur)
    except ValueError:
        raise ValueError(f"{nom} doit être un nombre") from None


def _configurer_http() -> tuple[float, int, _BudgetAppels]:
    delai = _flottant_env("LLM_TIMEOUT_S", 60.0)
    if delai <= 0:
        raise ValueError("LLM_TIMEOUT_S doit être supérieur à 0")
    temperature = _flottant_env("LLM_TEMPERATURE", 0.0)
    maximum_tokens = _entier_env("LLM_MAX_TOKENS", 1024)
    budget = _BudgetAppels(_entier_env("LLM_MAX_APPELS", 20))
    return delai, maximum_tokens, budget


def _cle_obligatoire(nom: str) -> str:
    valeur = os.getenv(nom, "").strip()
    if not valeur:
        raise ValueError(f"Variable d'environnement {nom} manquante")
    return valeur


def _modele_obligatoire(nom: str) -> str:
    valeur = os.getenv(nom, "").strip()
    if not valeur:
        raise ValueError(f"Variable d'environnement {nom} manquante")
    return valeur


def _ajouter_usage(usage: UsageLLM, entree: int | None, sortie: int | None) -> None:
    if usage.nb_appels == 1:
        usage.tokens_entree = entree
        usage.tokens_sortie = sortie
    else:
        usage.tokens_entree = (
            None if usage.tokens_entree is None or entree is None
            else usage.tokens_entree + entree
        )
        usage.tokens_sortie = (
            None if usage.tokens_sortie is None or sortie is None
            else usage.tokens_sortie + sortie
        )


def _extraire_usage(valeur: object, cle_entree: str, cle_sortie: str) -> tuple[int | None, int | None]:
    if not isinstance(valeur, dict):
        return None, None
    entree = valeur.get(cle_entree)
    sortie = valeur.get(cle_sortie)
    return (
        entree if isinstance(entree, int) and not isinstance(entree, bool) else None,
        sortie if isinstance(sortie, int) and not isinstance(sortie, bool) else None,
    )


def _info_reponse(
    voie: str, modele_demande: str, modele_repondu: str | None,
    entree: int | None, sortie: int | None,
) -> dict:
    # modele_repondu est la valeur renvoyee par le service (champ "model"), jamais devinee.
    return {"voie": voie, "modele_demande": modele_demande, "modele_repondu": modele_repondu,
            "tokens_entree": entree, "tokens_sortie": sortie}


class _AdaptateurHTTP:
    fournisseur = "fournisseur"

    def __init__(
        self, modele: str, cle: str, timeout: float, maximum_tokens: int,
        temperature: float, client: httpx.Client | None, budget: _BudgetAppels,
    ) -> None:
        self.modele = modele
        self._cle = cle
        self.timeout = timeout
        self.maximum_tokens = maximum_tokens
        self.temperature = temperature
        self._client = client or httpx.Client()
        self._possede_client = client is None
        self._budget = budget
        self.usage = UsageLLM()
        self.derniere_reponse_info: dict | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(modele={self.modele!r}, usage={self.usage!r})"

    def debut_execution(self) -> None:
        self._budget.commencer()

    def fermer(self) -> None:
        if self._possede_client:
            self._client.close()

    def _usage_inconnu(self) -> None:
        self.usage.tokens_entree = None
        self.usage.tokens_sortie = None

    def _poster(self, url: str, en_tetes: dict[str, str], corps: dict) -> httpx.Response:
        self._budget.consommer()
        self.usage.nb_appels += 1
        try:
            response = self._client.post(url, headers=en_tetes, json=corps, timeout=self.timeout)
        except httpx.TimeoutException:
            self._usage_inconnu()
            raise LLMIndisponible(f"{self.fournisseur}: délai de réponse dépassé") from None
        except httpx.RequestError:
            self._usage_inconnu()
            raise LLMIndisponible(f"{self.fournisseur}: erreur réseau") from None
        if response.status_code >= 400:
            self._usage_inconnu()
            raise LLMIndisponible(f"{self.fournisseur}: erreur HTTP {response.status_code}")
        return response


class _AdaptateurOpenAI(_AdaptateurHTTP):
    """Chemin HTTP commun aux API compatibles OpenAI (/chat/completions)."""

    base_url = ""

    def _requete_chat_completions(
        self, prompt: str, role: str, modele: str | None = None, tools: list | None = None,
    ) -> tuple[str, object, str | None]:
        corps = {
            "model": modele or self.modele,
            "messages": [
                {"role": "system", "content": f"Role: {role}"},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.maximum_tokens,
        }
        if tools is not None:
            corps["tools"] = tools
        response = self._poster(
            f"{self.base_url}/chat/completions",
            {"Authorization": "Bearer " + self._cle, "Content-Type": "application/json"},
            corps,
        )
        try:
            contenu = response.json()
            message = contenu["choices"][0]["message"]
            texte = message.get("content")
            if texte is None and tools is not None and isinstance(message.get("tool_calls"), list):
                texte = json.dumps({"tool_calls": message["tool_calls"]}, ensure_ascii=False)
            usage = contenu.get("usage")
            if not isinstance(texte, str):
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            self._usage_inconnu()
            raise LLMIndisponible(f"{self.fournisseur}: réponse JSON invalide ou incomplète") from None
        modele_repondu = contenu.get("model")
        return texte, usage, modele_repondu if isinstance(modele_repondu, str) else None


class LLMNvidia(_AdaptateurOpenAI):
    fournisseur = "nvidia"

    def __init__(self, client: httpx.Client | None = None) -> None:
        load_dotenv(dotenv_path=Path(".env"), override=False)
        cle = _cle_obligatoire("NVIDIA_API_KEY")
        modele = _modele_obligatoire("NVIDIA_MODEL")
        self.base_url = os.getenv("NVIDIA_BASE_URL", "").strip().rstrip("/") or NVIDIA_BASE_URL_PAR_DEFAUT
        self.temperature = _flottant_env("LLM_TEMPERATURE", 0.0)
        timeout, maximum_tokens, budget = _configurer_http()
        super().__init__(modele, cle, timeout, maximum_tokens, self.temperature, client, budget)

    def completer(self, prompt: str, role: str = "detective") -> str:
        self.derniere_reponse_info = None
        texte, usage, modele_repondu = self._requete_chat_completions(prompt, role)
        entree, sortie = _extraire_usage(usage, "prompt_tokens", "completion_tokens")
        _ajouter_usage(self.usage, entree, sortie)
        self.derniere_reponse_info = _info_reponse(VOIE_UNIQUE, self.modele, modele_repondu, entree, sortie)
        return texte


NIGHTSHIFT_VARIABLES = ("NIGHTSHIFT_URL", "NIGHTSHIFT_TOKEN", "NIGHTSHIFT_VOIE_RAPIDE", "NIGHTSHIFT_VOIE_PROFONDE")


class LLMNightshift(_AdaptateurOpenAI):
    """Meterless Nightshift : API compatible OpenAI, voie rapide et voie profonde."""

    fournisseur = "nightshift"

    def __init__(self, client: httpx.Client | None = None) -> None:
        load_dotenv(dotenv_path=Path(".env"), override=False)
        valeurs = {nom: os.getenv(nom, "").strip() for nom in NIGHTSHIFT_VARIABLES}
        manquantes = [nom for nom, valeur in valeurs.items() if not valeur]
        if manquantes:
            raise ValueError(
                "Variables d'environnement manquantes pour LLM_PROVIDER=nightshift : " + ", ".join(manquantes)
            )
        url = valeurs["NIGHTSHIFT_URL"].rstrip("/")
        if not url.startswith(("https://", "http://")):
            raise ValueError("NIGHTSHIFT_URL doit commencer par https:// (racine de l'API, avec /v1)")
        self.base_url = url
        self.voies = {VOIE_RAPIDE: valeurs["NIGHTSHIFT_VOIE_RAPIDE"],
                      VOIE_PROFONDE: valeurs["NIGHTSHIFT_VOIE_PROFONDE"]}
        self.temperature = _flottant_env("LLM_TEMPERATURE", 0.0)
        timeout, maximum_tokens, budget = _configurer_http()
        super().__init__(self.voies[VOIE_RAPIDE], valeurs["NIGHTSHIFT_TOKEN"], timeout,
                         maximum_tokens, self.temperature, client, budget)
        self.usage = UsageLLM(par_voie=usage_par_voie())

    def __repr__(self) -> str:
        return f"LLMNightshift(voies={self.voies!r}, usage={self.usage!r})"

    def lister_modeles(self) -> list[str]:
        """GET {NIGHTSHIFT_URL}/models ; ne consomme pas le budget d'appels LLM."""
        try:
            response = self._client.get(
                f"{self.base_url}/models", headers={"Authorization": "Bearer " + self._cle},
                timeout=self.timeout,
            )
        except httpx.TimeoutException:
            raise LLMIndisponible("nightshift: délai de réponse dépassé") from None
        except httpx.RequestError:
            raise LLMIndisponible("nightshift: erreur réseau") from None
        if response.status_code >= 400:
            raise LLMIndisponible(f"nightshift: erreur HTTP {response.status_code}")
        try:
            contenu = response.json()
            modeles = contenu["data"] if isinstance(contenu, dict) else contenu
            if not isinstance(modeles, list):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise LLMIndisponible("nightshift: liste de modèles invalide") from None
        return [modele["id"] for modele in modeles
                if isinstance(modele, dict) and isinstance(modele.get("id"), str)]

    def completer(self, prompt: str, role: str = "detective", tools: list | None = None) -> str:
        voie = voie_pour_role(role)
        modele = self.voies[voie]
        compteur = self.usage.par_voie[voie]
        self.derniere_reponse_info = None
        try:
            texte, usage, modele_repondu = self._requete_chat_completions(prompt, role, modele, tools)
        except LLMIndisponible:
            compteur.nb_appels += 1
            compteur.tokens_entree = compteur.tokens_sortie = None
            raise
        entree, sortie = _extraire_usage(usage, "prompt_tokens", "completion_tokens")
        compteur.nb_appels += 1
        _ajouter_usage(compteur, entree, sortie)
        _ajouter_usage(self.usage, entree, sortie)
        self.derniere_reponse_info = _info_reponse(voie, modele, modele_repondu, entree, sortie)
        return texte


class LLMClaude(_AdaptateurHTTP):
    fournisseur = "claude"
    base_url = "https://api.anthropic.com/v1/messages"

    def __init__(self, client: httpx.Client | None = None) -> None:
        load_dotenv(dotenv_path=Path(".env"), override=False)
        cle = _cle_obligatoire("ANTHROPIC_API_KEY")
        modele = _modele_obligatoire("ANTHROPIC_MODEL")
        self.temperature = _flottant_env("LLM_TEMPERATURE", 0.0)
        timeout, maximum_tokens, budget = _configurer_http()
        super().__init__(modele, cle, timeout, maximum_tokens, self.temperature, client, budget)

    def _requete_messages(self, prompt: str, role: str) -> tuple[str, object, str | None]:
        corps = {
            "model": self.modele,
            "max_tokens": self.maximum_tokens,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": f"Role: {role}\n\n{prompt}"}],
        }
        response = self._poster(
            self.base_url,
            {"x-api-key": self._cle, "anthropic-version": VERSION_ANTHROPIC,
             "content-type": "application/json"},
            corps,
        )
        try:
            contenu = response.json()
            texte = contenu["content"][0]["text"]
            usage = contenu.get("usage")
            if not isinstance(texte, str):
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            self._usage_inconnu()
            raise LLMIndisponible("claude: réponse JSON invalide ou incomplète") from None
        modele_repondu = contenu.get("model")
        return texte, usage, modele_repondu if isinstance(modele_repondu, str) else None

    def completer(self, prompt: str, role: str = "detective") -> str:
        self.derniere_reponse_info = None
        texte, usage, modele_repondu = self._requete_messages(prompt, role)
        entree, sortie = _extraire_usage(usage, "input_tokens", "output_tokens")
        _ajouter_usage(self.usage, entree, sortie)
        self.derniere_reponse_info = _info_reponse(VOIE_UNIQUE, self.modele, modele_repondu, entree, sortie)
        return texte


MODELES_FAUX = {VOIE_RAPIDE: "faux-rapide", VOIE_PROFONDE: "faux-profond"}
ROLES_SQL = frozenset({"hypotheses", "verdict", "conclusion", "correctif"})


def lire_donnees(prompt: str) -> dict:
    """Extrait l'objet JSON place apres MARQUEUR_DONNEES dans un prompt structure."""
    position = prompt.rfind(MARQUEUR_DONNEES)
    if position == -1:
        raise ValueError("Prompt sans bloc DONNEES")
    donnees = json.loads(prompt[position + len(MARQUEUR_DONNEES):])
    if not isinstance(donnees, dict):
        raise ValueError("Le bloc DONNEES doit etre un objet JSON")
    return donnees


class LLMFaux:
    def __init__(self, budget: _BudgetAppels | None = None) -> None:
        self.usage = UsageLLM(par_voie=usage_par_voie())
        self._budget = budget or _BudgetAppels(_entier_env("LLM_MAX_APPELS", 20))
        self.derniere_reponse_info: dict | None = None

    def __repr__(self) -> str:
        return f"LLMFaux(usage={self.usage!r})"

    def debut_execution(self) -> None:
        self._budget.commencer()

    def completer(self, prompt: str, role: str = "detective", tools: list | None = None) -> str:
        self._budget.consommer()
        self.usage.nb_appels += 1
        voie = voie_pour_role(role)
        compteur = self.usage.par_voie[voie]
        compteur.nb_appels += 1
        _ajouter_usage(compteur, 0, 0)
        self.derniere_reponse_info = _info_reponse(voie, MODELES_FAUX[voie], MODELES_FAUX[voie], 0, 0)
        if role in ROLES_SQL:
            from . import _faux_sql

            _ajouter_usage(self.usage, 0, 0)
            return _faux_sql.repondre(role, lire_donnees(prompt))
        try:
            donnees = json.loads(prompt.split("\nReponse invalide", 1)[0])
        except (ValueError, TypeError):
            donnees = None
        if isinstance(donnees, dict) and donnees.get("mode") == "war_room":
            if role == "sceptique":
                objection = "Quelle preuve des outils confirme cette hypothese ?" if donnees["tour"] == 1 else None
                texte = json.dumps({"objection": objection, "preuve_manquante": None})
            elif role == "detective":
                preuves = donnees.get("preuves", [])
                texte = json.dumps({"reponse": "Voici une preuve fournie par les outils",
                                    "preuves_citees": preuves[:1]})
            else:
                raise ValueError("Role de debat inconnu")
        elif role == "detective":
            texte = json.dumps([
                {"texte": "Le schema des donnees a change", "categorie": "schema"},
                {"texte": "Saturation des ressources", "categorie": "ressources"},
                {"texte": "Probleme reseau ou timeout", "categorie": "reseau"},
                {"texte": "Changement de configuration", "categorie": "configuration"},
            ])
        else:
            textes = {
                "sceptique": "Verifier si les preuves distinguent plusieurs causes possibles",
                "historien": "Comparer l'incident aux causes confirmees en memoire",
            }
            if role not in textes:
                raise ValueError(f"Role LLM inconnu : {role}")
            texte = Hypothese(
                id=f"hyp-faux-{role}", incident_id="inc-faux", texte=textes[role],
                statut="ouverte", preuves=[], confiance=0.3, raison_rejet=None,
            ).model_dump_json()
        _ajouter_usage(self.usage, 0, 0)
        return texte


class LLMAvecSecours:
    def __init__(self, principal: LLM, secours: LLM, maximum: int | None = None) -> None:
        self.principal = principal
        self.secours = secours
        self.basculements = 0
        self.derniere_reponse_info: dict | None = None
        limite = maximum or _entier_env("LLM_MAX_APPELS", 20)
        self._budget = _BudgetAppels(limite)
        for fournisseur in (principal, secours):
            if hasattr(fournisseur, "_budget"):
                fournisseur._budget = self._budget

    def __repr__(self) -> str:
        return f"LLMAvecSecours(principal={type(self.principal).__name__}, secours={type(self.secours).__name__}, basculements={self.basculements})"

    @staticmethod
    def _cumuler(usages: list[UsageLLM]) -> UsageLLM:
        appels = sum(usage.nb_appels for usage in usages)
        entree = None if any(usage.tokens_entree is None and usage.nb_appels for usage in usages) else sum(
            usage.tokens_entree or 0 for usage in usages
        )
        sortie = None if any(usage.tokens_sortie is None and usage.nb_appels for usage in usages) else sum(
            usage.tokens_sortie or 0 for usage in usages
        )
        return UsageLLM(entree, sortie, appels)

    @property
    def usage(self) -> UsageLLM:
        usages = [self.principal.usage, self.secours.usage]
        cumul = self._cumuler(usages)
        voies = [usage.par_voie for usage in usages if usage.par_voie]
        if voies:
            cumul.par_voie = {
                voie: self._cumuler([par_voie[voie] for par_voie in voies if voie in par_voie])
                for voie in (VOIE_RAPIDE, VOIE_PROFONDE)
            }
        return cumul

    def debut_execution(self) -> None:
        self._budget.commencer()
        for fournisseur in (self.principal, self.secours):
            if hasattr(fournisseur, "debut_execution"):
                fournisseur.debut_execution()

    def completer(self, prompt: str, role: str = "detective", tools: list | None = None) -> str:
        options = {} if tools is None else {"tools": tools}
        self.derniere_reponse_info = None
        try:
            texte = self.principal.completer(prompt, role, **options)
            fournisseur = self.principal
        except LLMIndisponible:
            self.basculements += 1
            texte = self.secours.completer(prompt, role, **options)
            fournisseur = self.secours
        self.derniere_reponse_info = getattr(fournisseur, "derniere_reponse_info", None)
        return texte


def _creer_fournisseur(nom: str, client: httpx.Client | None = None) -> LLM:
    if nom == "faux":
        return LLMFaux()
    if nom == "nvidia":
        return LLMNvidia(client)
    if nom == "claude":
        return LLMClaude(client)
    if nom == "nightshift":
        return LLMNightshift(client)
    raise ValueError(f"Fournisseur LLM inconnu : {nom}")


def creer_llm(client: httpx.Client | None = None) -> LLM:
    load_dotenv(dotenv_path=Path(".env"), override=False)
    fournisseur = os.getenv("LLM_PROVIDER", "faux").strip().lower() or "faux"
    principal = _creer_fournisseur(fournisseur, client)
    secours_nom = os.getenv("LLM_FALLBACK", "").strip().lower()
    if not secours_nom:
        return principal
    secours = _creer_fournisseur(secours_nom, client)
    return LLMAvecSecours(principal, secours)
