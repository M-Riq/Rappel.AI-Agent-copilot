import os

from rappel.config import charger

ETIQUETTE_FAUX_SERVICES = "FAUX SERVICE LOCAL, NON une preuve sponsor"


def faux_services_actifs() -> bool:
    """Vrai quand les variables pointent vers les faux services locaux des tests (RAPPEL_FAUX_SERVICES=1)."""
    return os.environ.get("RAPPEL_FAUX_SERVICES", "").strip() == "1"


def annoncer_faux_services() -> bool:
    if faux_services_actifs():
        print(ETIQUETTE_FAUX_SERVICES + " : aucun résultat ci-dessous ne prouve l'usage d'un sponsor.")
        return True
    return False
from rappel.securite import masquer_secrets


def nettoyer(texte: str) -> str:
    # Valeurs sensibles de l'environnement, identifiants dans les URL et jetons « Bearer ».
    return masquer_secrets(texte)


def afficher(texte: str) -> None:
    print(nettoyer(texte))


def autoriser(confirmer: bool, appels_prevus: int, executions: int = 1) -> bool:
    configuration = charger()
    afficher(f"Appels prevus au maximum (relances incluses) : {appels_prevus}")
    afficher(f"Limite de depense : LLM_MAX_APPELS={configuration.llm_max_appels} par execution "
             f"({executions} execution(s)). Ce plafond d'appels n'est pas un budget monetaire ; "
             "verifier les tarifs et quotas.")
    if configuration.llm_provider != "fake" and not confirmer:
        afficher("REFUS : fournisseur reel configure. Relancer avec --confirmer pour autoriser la depense.")
        return False
    return True


def fermer(llm) -> None:
    methode = getattr(llm, "fermer", None)
    if callable(methode):
        methode()


def instant_usage(llm) -> tuple[int | None, int | None, int]:
    usage = llm.usage
    return usage.tokens_entree, usage.tokens_sortie, usage.nb_appels


def difference(avant, apres):
    return None if avant is None or apres is None else apres - avant


class LLMCapture:
    def __init__(self, llm, brut=False):
        self.llm = llm
        self.brut = brut

    def __getattr__(self, nom):
        return getattr(self.llm, nom)

    def completer(self, prompt, role="detective"):
        reponse = self.llm.completer(prompt, role=role)
        if self.brut:
            afficher(f"Brut ({role}, 400 caracteres max) : {nettoyer(reponse)[:400]}")
        return reponse