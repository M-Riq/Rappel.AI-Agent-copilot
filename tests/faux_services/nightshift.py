"""Faux Meterless Nightshift (OpenAI-compatible) — faux service local, NON une preuve sponsor."""

import json
import re
import threading
import time

from tests.faux_services.serveur import ServeurLocal

JETON_DEMO_REFUSE = "rt-local"
IDS_MODELES = ("meterless/auto", "meterless/fast", "meterless/deep", "meterless/reasoning")
MODELES_NEMOTRON = {"meterless/fast": "nvidia/nemotron-faux-local-rapide",
                    "meterless/deep": "nvidia/nemotron-faux-local-profond"}
SCENARIOS = ("json", "texte", "markdown", "tool_calls", "tronque", "vide", "429", "500", "401", "lent", "html")


def _contenu_deterministe(prompt: str) -> str:
    """Réponse du double déterministe du moteur selon le rôle lu dans le prompt."""
    from rappel.enquete.adaptateurs import LLMFaux
    from rappel.interfaces.llm import FakeLLM, ROLES_ENQUETE_PROFONDS, ROLES_ENQUETE_RAPIDES

    try:
        donnees = json.loads(prompt)
    except ValueError:
        donnees = None
    if isinstance(donnees, dict) and donnees.get("mode") == "war_room":
        return LLMFaux._reponse_historique(prompt, donnees.get("role", ""))
    trouve = re.match(r"Role: (\w+)", prompt)
    role = trouve.group(1) if trouve else ""
    if role in ROLES_ENQUETE_RAPIDES + ROLES_ENQUETE_PROFONDS:
        return FakeLLM().completer(prompt, role)["texte"]
    return "Connexion operationnelle."


class FauxNightshift(ServeurLocal):
    def __init__(self, jeton="jeton-" + "faux-nightshift-local", modeles_repondus=None, delai_lent_s=2.0,
                 jetons_interdits=(JETON_DEMO_REFUSE,)):
        self.jeton = jeton
        # Jetons reconnus mais refusés (403), dont le jeton de démonstration rt-local.
        self.jetons_interdits = set(jetons_interdits)
        self.modeles_repondus = dict(MODELES_NEMOTRON if modeles_repondus is None else modeles_repondus)
        self.scenarios: list[str] = []
        self.delai_lent_s = delai_lent_s
        self.requetes: list[dict] = []
        self._verrou = threading.Lock()

    def modele_repondu(self, demande: str) -> str:
        return self.modeles_repondus.get(demande) or self.modeles_repondus.get("*") or "nvidia/nemotron-faux-local-auto"

    def _auth(self, entetes):
        autorisation = entetes.get("Authorization", "")
        if not autorisation.startswith("Bearer "):
            return 401, {"error": {"message": "Authorization Bearer requis"}}
        jeton = autorisation[len("Bearer "):]
        if jeton in self.jetons_interdits:
            return 403, {"error": {"message": "jeton de demonstration refuse"}}
        if jeton != self.jeton:
            return 401, {"error": {"message": "jeton inconnu"}}
        return None

    def traiter(self, methode, chemin, entetes, corps):
        with self._verrou:
            self.requetes.append({"methode": methode, "chemin": chemin, "entetes": entetes, "corps": corps})
        if methode == "GET" and chemin == "/healthz":
            return 200, {"statut": "ok"}, None
        if methode == "GET" and chemin == "/v1/models":
            refus = self._auth(entetes)
            if refus:
                return refus[0], refus[1], None
            return 200, {"object": "list", "data": [{"id": ident, "object": "model"} for ident in IDS_MODELES]}, None
        if methode == "POST" and chemin == "/v1/chat/completions":
            return self._chat(entetes, corps)
        return 404, {"error": {"message": f"chemin inconnu : {chemin}"}}, None

    def _chat(self, entetes, corps):
        refus = self._auth(entetes)
        if refus:
            return refus[0], refus[1], None
        if entetes.get("meterless-app") != "rappel" or entetes.get("meterless-scope") != "user":
            return 400, {"error": {"message": "en-tetes meterless-app et meterless-scope: user requis"}}, None
        if not isinstance(corps, dict) or not isinstance(corps.get("messages"), list) or not corps.get("model"):
            return 400, {"error": {"message": "corps invalide"}}, None
        with self._verrou:
            scenario = self.scenarios.pop(0) if self.scenarios else "json"
        if scenario in ("429", "500", "401"):
            return int(scenario), {"error": {"message": f"erreur simulee {scenario}"}}, None
        if scenario == "lent":
            time.sleep(self.delai_lent_s)
        if scenario == "html":
            return 200, b"<html>passerelle</html>", {"Content-Type": "text/html"}
        prompt = corps["messages"][-1].get("content", "")
        contenu = _contenu_deterministe(prompt)
        message = {"role": "assistant", "content": contenu}
        if scenario == "texte":
            message["content"] = f"Voici ma reponse :\n{contenu}\nBonne enquete."
        elif scenario == "markdown":
            message["content"] = f"```json\n{contenu}\n```"
        elif scenario == "tronque":
            message["content"] = contenu[: max(1, len(contenu) // 2)]
        elif scenario == "vide":
            message["content"] = ""
        elif scenario == "tool_calls":
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "appel-1", "type": "function",
                "function": {"name": "run_sql", "arguments": json.dumps({"sql": "SELECT template, compte FROM logs"})},
            }]}
        modele = self.modele_repondu(corps["model"])
        jetons_entree = max(1, len(prompt) // 4)
        jetons_sortie = max(1, len(message.get("content") or "") // 4)
        return 200, {
            "id": "faux-local", "object": "chat.completion", "model": modele,
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": jetons_entree, "completion_tokens": jetons_sortie,
                      "total_tokens": jetons_entree + jetons_sortie},
        }, None
