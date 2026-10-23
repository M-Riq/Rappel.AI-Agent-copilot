"""Fournisseurs LLM : simulation locale ou Runtime Meterless Nightshift."""

import requests

from rappel.config import Configuration, charger


class FakeLLM:
    def completer(self, prompt, role, tools=None):
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

    def completer(self, prompt, role, tools=None):
        configuration = self.configuration
        if role in ("detective", "outil"):
            lane = configuration.meterless_model_rapide
        elif role in ("sceptique", "conclusion", "correctif"):
            lane = configuration.meterless_model_profond
        else:
            lane = ""
        modele = lane or configuration.meterless_model
        corps = {"model": modele, "messages": [{"role": "user", "content": prompt}]}
        if tools is not None:
            corps["tools"] = tools
        entetes = self.entetes()
        try:
            reponse = requests.post(
                f"{configuration.meterless_base_url.rstrip('/')}/chat/completions",
                headers=entetes,
                json=corps,
                timeout=60,
            )
            if reponse.status_code == 401:
                raise RuntimeError(
                    "jeton Runtime Nightshift incorrect (ne pas utiliser la clé NVIDIA)"
                )
            reponse.raise_for_status()
            donnees = reponse.json()
            message = donnees["choices"][0]["message"]
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
