"""Adaptateurs : le moteur d'enquête au-dessus des interfaces du squelette.

- LLMEnquete expose le protocole LLM du moteur (texte, voies, usage, plafond d'appels)
  au-dessus d'un fournisseur du squelette (FakeLLM, MeterlessLLM) qui renvoie un dict.
- MemoireEnquete expose le protocole Memoire (objets Pydantic) au-dessus du backend
  JSON ou Postgres choisi par MEMOIRE_PROVIDER.
"""

from contextlib import contextmanager
import json
from pathlib import Path
from threading import Lock

from rappel.config import RACINE, Configuration, charger
from rappel.enquete.modeles import EnregistrementMemoire, Hypothese
from rappel.enquete.protocoles import UsageLLM, usage_par_voie, voie_pour_role
from rappel.interfaces import llm as llm_squelette
from rappel.interfaces import memoire as memoire_squelette


_VERROU_NON_CONFIRMES = Lock()


class LLMIndisponible(RuntimeError):
    """Fournisseur LLM inaccessible ou réponse non conforme."""


class LLMBudgetEpuise(RuntimeError):
    """Plafond d'appels LLM de l'exécution atteint."""


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


class _BudgetSession:
    """Appels facturés du processus (toutes enquêtes, war room comprise) : LLM_MAX_APPELS_SESSION."""

    def __init__(self) -> None:
        self.appels = 0
        self.verrou = Lock()

    def consommer(self, maximum: int) -> None:
        with self.verrou:
            if self.appels >= maximum:
                raise LLMBudgetEpuise("Plafond LLM_MAX_APPELS_SESSION atteint pour cette session : "
                                      "relancer le processus après accord de l'équipe")
            self.appels += 1


BUDGET_SESSION = _BudgetSession()


def reinitialiser_budget_session() -> None:
    with BUDGET_SESSION.verrou:
        BUDGET_SESSION.appels = 0


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


def _ajouter_usage(usage: UsageLLM, entree: int | None, sortie: int | None) -> None:
    if usage.nb_appels == 1:
        usage.tokens_entree = entree
        usage.tokens_sortie = sortie
        return
    usage.tokens_entree = None if usage.tokens_entree is None or entree is None else usage.tokens_entree + entree
    usage.tokens_sortie = None if usage.tokens_sortie is None or sortie is None else usage.tokens_sortie + sortie


def message_sans_secret(erreur: Exception, defaut: str) -> str:
    """Relaye un message d'erreur seulement s'il ne contient aucun element d'authentification."""
    message = str(erreur)
    if not message or any(mot in message.lower() for mot in ("bearer", "authorization", "api-key", "api_key")):
        return defaut
    return message


def _entier(valeur) -> int | None:
    return valeur if isinstance(valeur, int) and not isinstance(valeur, bool) else None


class LLMEnquete:
    """Protocole LLM du moteur au-dessus d'un fournisseur du squelette."""

    def __init__(self, fournisseur, configuration: Configuration | None = None, budget=None) -> None:
        configuration = configuration or charger()
        self.fournisseur = fournisseur
        self._max_session = configuration.llm_max_appels_session
        self._budget = budget or _BudgetAppels(configuration.llm_max_appels)
        self.usage = UsageLLM(par_voie=usage_par_voie())
        self.derniere_reponse_info: dict | None = None
        # tool_calls de la derniere reponse (appels d'outils natifs, liste vide sinon).
        self.derniers_appels_outils: list = []

    def __repr__(self) -> str:
        return f"{type(self).__name__}(fournisseur={type(self.fournisseur).__name__}, usage={self.usage!r})"

    def debut_execution(self) -> None:
        self._budget.commencer()

    def fermer(self) -> None:
        pass

    def _appeler_fournisseur(self, prompt: str, role: str, tools) -> dict:
        return self.fournisseur.completer(prompt, role, tools)

    def _modele_demande(self, role: str) -> str | None:
        choisir = getattr(self.fournisseur, "modele_pour_role", None)
        return choisir(role) if callable(choisir) else None

    def completer(self, prompt: str, role: str = "detective", tools: list | None = None) -> str:
        # Plafonds vérifiés AVANT tout appel : par exécution, puis par session si l'appel est facturé.
        self._budget.consommer()
        if getattr(self.fournisseur, "facture", False):
            BUDGET_SESSION.consommer(self._max_session)
        voie = voie_pour_role(role)
        compteur = self.usage.par_voie[voie]
        self.usage.nb_appels += 1
        compteur.nb_appels += 1
        self.derniere_reponse_info = None
        self.derniers_appels_outils = []
        try:
            reponse = self._appeler_fournisseur(prompt, role, tools)
            if not isinstance(reponse, dict) or not isinstance(reponse.get("texte"), str):
                raise LLMIndisponible("Réponse du fournisseur LLM invalide.")
        except RuntimeError as erreur:
            for usage in (self.usage, compteur):
                usage.tokens_entree = usage.tokens_sortie = None
            if isinstance(erreur, LLMIndisponible):
                raise
            raise LLMIndisponible(message_sans_secret(erreur, "Fournisseur LLM indisponible.")) from None
        texte = reponse["texte"]
        if isinstance(reponse.get("tool_calls"), list):
            self.derniers_appels_outils = list(reponse["tool_calls"])
        if not texte and reponse.get("tool_calls"):
            texte = json.dumps({"tool_calls": reponse["tool_calls"]}, ensure_ascii=False)
        usage = reponse.get("usage") if isinstance(reponse.get("usage"), dict) else {}
        entree, sortie = _entier(usage.get("prompt_tokens")), _entier(usage.get("completion_tokens"))
        _ajouter_usage(self.usage, entree, sortie)
        _ajouter_usage(compteur, entree, sortie)
        # MeterlessLLM conserve le champ "model" réellement renvoyé ; sinon, celui du dict.
        modele = getattr(self.fournisseur, "dernier_modele_repondu", reponse.get("modele"))
        self.derniere_reponse_info = {
            "voie": voie, "modele_demande": self._modele_demande(role),
            "modele_repondu": modele if isinstance(modele, str) and modele else None,
            "tokens_entree": entree, "tokens_sortie": sortie,
        }
        return texte


ROLES_SQL = frozenset(llm_squelette.ROLES_ENQUETE_RAPIDES + llm_squelette.ROLES_ENQUETE_PROFONDS)
CANDIDATS_DETECTIVE = (
    {"texte": "Le schema des donnees a change", "categorie": "schema"},
    {"texte": "Saturation des ressources", "categorie": "ressources"},
    {"texte": "Probleme reseau ou timeout", "categorie": "reseau"},
    {"texte": "Changement de configuration", "categorie": "configuration"},
)


class LLMFaux(LLMEnquete):
    """Double déterministe hors réseau pour les deux modes du moteur.

    Rôles SQL : délégués à FakeLLM du squelette (JSON de _faux_sql, modèles faux-rapide /
    faux-profond). Rôles du mode outils et de la war room (detective, sceptique,
    historien) : réponses JSON historiques produites localement.
    """

    def __init__(self, budget=None, configuration: Configuration | None = None) -> None:
        super().__init__(llm_squelette.FakeLLM(), configuration, budget)

    def _appeler_fournisseur(self, prompt: str, role: str, tools) -> dict:
        if role in ROLES_SQL:
            return super()._appeler_fournisseur(prompt, role, tools)
        return {"texte": self._reponse_historique(prompt, role), "tool_calls": [],
                "modele": self.fournisseur.modele_pour_role(role),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

    @staticmethod
    def _reponse_historique(prompt: str, role: str) -> str:
        try:
            donnees = json.loads(prompt.split("\nReponse invalide", 1)[0])
        except (ValueError, TypeError):
            donnees = None
        if isinstance(donnees, dict) and donnees.get("mode") == "war_room":
            if role == "sceptique":
                objection = "Quelle preuve des outils confirme cette hypothese ?" if donnees["tour"] == 1 else None
                return json.dumps({"objection": objection, "preuve_manquante": None})
            if role == "detective":
                return json.dumps({"reponse": "Voici une preuve fournie par les outils",
                                   "preuves_citees": donnees.get("preuves", [])[:1]})
            raise ValueError("Role de debat inconnu")
        if role == "detective":
            return json.dumps(list(CANDIDATS_DETECTIVE))
        textes = {
            "sceptique": "Verifier si les preuves distinguent plusieurs causes possibles",
            "historien": "Comparer l'incident aux causes confirmees en memoire",
        }
        if role not in textes:
            raise ValueError(f"Role LLM inconnu : {role}")
        return Hypothese(
            id=f"hyp-faux-{role}", incident_id="inc-faux", texte=textes[role],
            statut="ouverte", preuves=[], confiance=0.3, raison_rejet=None,
        ).model_dump_json()


def creer_llm_enquete(configuration: Configuration | None = None) -> LLMEnquete:
    """LLM du moteur selon LLM_PROVIDER : fake (hors réseau) ou meterless (Nightshift)."""
    configuration = configuration or charger()
    if configuration.llm_provider == "fake":
        return LLMFaux(configuration=configuration)
    return LLMEnquete(llm_squelette.MeterlessLLM(configuration), configuration)


class MemoireEnquete:
    """Protocole Memoire du moteur (objets Pydantic) au-dessus d'un backend du squelette."""

    def __init__(self, backend=None) -> None:
        self.backend = memoire_squelette.backend() if backend is None else backend

    @classmethod
    def json(cls, chemin) -> "MemoireEnquete":
        return cls(memoire_squelette.MemoireJSON(chemin))

    def __repr__(self) -> str:
        return f"MemoireEnquete(backend={type(self.backend).__name__})"

    @property
    def chemin(self):
        return getattr(self.backend, "chemin", None)

    @property
    def chemin_hypotheses(self):
        return getattr(self.backend, "chemin_hypotheses", None)

    def chercher(self, signature: str) -> list[EnregistrementMemoire]:
        return [EnregistrementMemoire.model_validate(element) for element in self.backend.chercher(signature)]

    def lister(self) -> list[EnregistrementMemoire]:
        return [EnregistrementMemoire.model_validate(element) for element in self.backend.lister()]

    def vider(self) -> int:
        nombre = self.backend.vider()
        with _VERROU_NON_CONFIRMES:
            self._ecrire_non_confirmes([])
        return nombre

    def ecrire(self, enregistrement: EnregistrementMemoire) -> None:
        enregistrement = EnregistrementMemoire.model_validate(enregistrement.model_dump())
        self.backend.ecrire(enregistrement.model_dump(mode="json"))

    def hypotheses_refutees(self, signature: str) -> list[str]:
        return self.backend.hypotheses_refutees(signature)

    # Registre des résultats non confirmés : le contrat n'a que « reussi » / « echoue », un souvenir
    # écrit avant l'action est donc marqué à part, jusqu'au retour du résultat de l'action.
    @property
    def chemin_non_confirmes(self) -> Path:
        chemin = self.chemin
        if not isinstance(chemin, Path):
            chemin = Path(charger().memoire_json_path)
            chemin = chemin if chemin.is_absolute() else RACINE / chemin
        return chemin.with_name(chemin.stem + ".non_confirmes.json")

    @staticmethod
    def _cle(souvenir: EnregistrementMemoire) -> list[str]:
        return [souvenir.signature, souvenir.correctif_essaye, souvenir.date, souvenir.cause_confirmee]

    def _lire_non_confirmes(self) -> list[list[str]]:
        try:
            donnees = json.loads(self.chemin_non_confirmes.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [cle for cle in donnees if isinstance(cle, list)] if isinstance(donnees, list) else []

    def _ecrire_non_confirmes(self, cles: list[list[str]]) -> None:
        chemin = self.chemin_non_confirmes
        if not cles and not chemin.exists():
            return
        # Écriture atomique (fichier temporaire puis remplacement) : jamais de fichier tronqué.
        memoire_squelette.MemoireJSON._ecrire_liste(chemin, cles)

    def marquer_non_confirme(self, enregistrement: EnregistrementMemoire) -> None:
        with _VERROU_NON_CONFIRMES:
            existantes = {tuple(self._cle(souvenir)) for souvenir in self.lister()}
            # Les clés orphelines (souvenir effacé ou mémoire remplacée) sont purgées au passage.
            cles = [cle for cle in self._lire_non_confirmes() if tuple(cle) in existantes]
            cle = self._cle(enregistrement)
            if cle not in cles:
                cles.append(cle)
            self._ecrire_non_confirmes(cles)

    def statut_resultat(self, souvenir: EnregistrementMemoire) -> str:
        """reussi, echoue ou non_confirme (souvenir provisoire en attente du résultat de l'action)."""
        return "non_confirme" if self._cle(souvenir) in self._lire_non_confirmes() else souvenir.resultat

    def mettre_a_jour_resultat(self, signature: str, resultat: str) -> bool:
        souvenirs = self.chercher(signature)
        mis_a_jour = self.backend.mettre_a_jour_resultat(signature, resultat)
        if mis_a_jour and souvenirs:
            cle = self._cle(souvenirs[-1])
            with _VERROU_NON_CONFIRMES:
                cles = self._lire_non_confirmes()
                if cle in cles:
                    self._ecrire_non_confirmes([element for element in cles if element != cle])
        return mis_a_jour

    def ecrire_hypotheses(self, incident_id: str, signature: str, hypotheses: list[Hypothese]) -> None:
        self.backend.ecrire_hypotheses(
            incident_id, signature,
            [Hypothese.model_validate(hypothese.model_dump()).model_dump(mode="json") for hypothese in hypotheses],
        )

    def lister_hypotheses(self, signature: str | None = None) -> list[dict]:
        return self.backend.lister_hypotheses(signature)
