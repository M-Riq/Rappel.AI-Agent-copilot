import os

from dotenv import load_dotenv


def nettoyer(texte: str) -> str:
    for nom, valeur in os.environ.items():
        if valeur and any(mot in nom.upper() for mot in ("KEY", "SECRET", "TOKEN", "PASSWORD")):
            texte = texte.replace(valeur, "[MASQUE]")
    return texte


def afficher(texte: str) -> None:
    print(nettoyer(texte))


def autoriser(confirmer: bool, appels_prevus: int, executions: int = 1) -> bool:
    load_dotenv(override=False)
    fournisseur = os.getenv("LLM_PROVIDER", "").strip().lower() or "faux"
    secours = os.getenv("LLM_FALLBACK", "").strip().lower()
    maximum = os.getenv("LLM_MAX_APPELS", "").strip() or "20"
    afficher(f"Appels prevus au maximum (relances/secours inclus) : {appels_prevus}")
    afficher(f"Limite de depense : LLM_MAX_APPELS={maximum} par execution ({executions} execution(s)). "
             "Ce plafond d'appels n'est pas un budget monetaire ; verifier les tarifs et quotas.")
    if (fournisseur != "faux" or secours not in {"", "faux"}) and not confirmer:
        afficher("REFUS : fournisseur reel configure. Relancer avec --confirmer pour autoriser la depense.")
        return False
    return True


def fermer(llm) -> None:
    fournisseurs = (llm.principal, llm.secours) if hasattr(llm, "principal") else (llm,)
    for fournisseur in fournisseurs:
        methode = getattr(fournisseur, "fermer", None)
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