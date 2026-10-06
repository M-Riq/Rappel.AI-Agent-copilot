import os
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv

from ..contrats import Hypothese


class LLM(Protocol):
    def completer(self, prompt: str, role: str = "detective") -> str: ...


class LLMFaux:
    def completer(self, prompt: str, role: str = "detective") -> str:
        textes = {
            "detective": "Un changement de schema pourrait expliquer l'incident",
            "sceptique": "Verifier si les preuves distinguent plusieurs causes possibles",
            "historien": "Comparer l'incident aux causes confirmees en memoire",
        }
        if role not in textes:
            raise ValueError(f"Role LLM inconnu : {role}")
        return Hypothese(
            id=f"hyp-faux-{role}",
            incident_id="inc-faux",
            texte=textes[role],
            statut="ouverte",
            preuves=[],
            confiance=0.3,
            raison_rejet=None,
        ).model_dump_json()


def creer_llm() -> LLM:
    load_dotenv(dotenv_path=Path(".env"), override=False)
    fournisseur = os.getenv("LLM_PROVIDER", "faux").strip().lower() or "faux"
    if fournisseur == "faux":
        return LLMFaux()
    if fournisseur in {"nvidia", "claude"}:
        raise NotImplementedError("à brancher plus tard")
    raise ValueError(f"LLM_PROVIDER inconnu : {fournisseur}")