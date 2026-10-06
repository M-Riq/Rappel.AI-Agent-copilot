from dataclasses import dataclass
import json
import os
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from ..contrats import Hypothese
from ..interfaces.llm import LLMBudgetEpuise, LLMIndisponible, limiter_appels


MAX_TOURS_PAR_DEFAUT = 2
MAX_APPELS_PAR_DEFAUT = 6
# Une objection sans citation verifiee diminue la certitude, sans effacer les preuves.
PENALITE_OBJECTION_SANS_REPONSE = 0.1
TexteNonVide = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@dataclass(frozen=True)
class ConfigurationWarRoom:
    active: bool = False
    max_tours: int = MAX_TOURS_PAR_DEFAUT
    max_appels_llm: int = MAX_APPELS_PAR_DEFAUT


def configuration(active=None, max_tours=None, max_appels_llm=None) -> ConfigurationWarRoom:
    if active is None:
        valeur = os.getenv("WAR_ROOM_ACTIVE", "0").strip() or "0"
        if valeur not in {"0", "1"}:
            raise ValueError("WAR_ROOM_ACTIVE doit valoir 0 ou 1")
        active = valeur == "1"
    limites = []
    for nom, surcharge, defaut in (
        ("WAR_ROOM_MAX_TOURS", max_tours, MAX_TOURS_PAR_DEFAUT),
        ("WAR_ROOM_MAX_APPELS_LLM", max_appels_llm, MAX_APPELS_PAR_DEFAUT),
    ):
        try:
            valeur = surcharge if surcharge is not None else int(os.getenv(nom, "").strip() or defaut)
        except ValueError:
            raise ValueError(f"{nom} doit etre un entier positif ou nul") from None
        if isinstance(valeur, bool) or not isinstance(valeur, int) or valeur < 0:
            raise ValueError(f"{nom} doit etre un entier positif ou nul")
        limites.append(valeur)
    return ConfigurationWarRoom(active, *limites)


class Objection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objection: TexteNonVide | None
    preuve_manquante: TexteNonVide | None


class ReponseDetective(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reponse: TexteNonVide
    preuves_citees: list[str]


class ArretDebat(Exception):
    pass


def extraire_objet(texte: str) -> str:
    if not isinstance(texte, str):
        raise ValueError("Reponse non textuelle")
    decodeur = json.JSONDecoder()
    for indice, caractere in enumerate(texte):
        if caractere == "{":
            try:
                objet, _ = decodeur.raw_decode(texte[indice:])
            except json.JSONDecodeError:
                continue
            if isinstance(objet, dict):
                return json.dumps(objet)
    raise ValueError("Aucun objet JSON")


def executer_debat(etat: dict, llm, config: ConfigurationWarRoom, emettre: Callable) -> dict:
    if not config.active or etat.get("retenue") is None:
        return etat
    retenue = etat["retenue"]
    confiance_avant = retenue.confiance
    compteurs = {
        "nb_tours_war_room": 0, "nb_objections": 0,
        "nb_objections_sans_reponse": 0, "nb_appels_llm_war_room": 0,
    }
    historique = {
        "incidents_passes": [souvenir.model_dump() for souvenir in etat["souvenirs"]],
        "hypotheses_refutees": etat["refutees_connues"],
    }
    emettre(etat, "debat", "historien", "Memoire de cette signature : " + json.dumps(historique))

    def appeler(role, donnees, modele):
        prompt = json.dumps({
            "mode": "war_room", "role": role,
            "instruction": (
                'Retourne UNIQUEMENT {"objection": texte ou null, "preuve_manquante": texte ou null}. '
                'Une objection null termine le debat.' if role == "sceptique" else
                'Retourne UNIQUEMENT {"reponse": texte, "preuves_citees": [textes exacts]}. '
                'Ne cite que les preuves fournies par les outils, sans en inventer.'
            ),
            **donnees,
        }, ensure_ascii=True)
        for tentative in range(2):
            restant = config.max_appels_llm - compteurs["nb_appels_llm_war_room"]
            if restant <= 0:
                raise ArretDebat("plafond d'appels war room atteint")
            usage = getattr(llm, "usage", None)
            avant = getattr(usage, "nb_appels", None)
            reponse = None
            raison = None
            try:
                with limiter_appels(llm, restant):
                    reponse = llm.completer(prompt, role=role)
            except LLMBudgetEpuise:
                raison = "plafond d'appels global ou war room atteint"
            except LLMIndisponible:
                raison = "fournisseur LLM indisponible pendant le debat"
            finally:
                apres = getattr(getattr(llm, "usage", None), "nb_appels", None)
                appels = max(0, apres - avant) if avant is not None and apres is not None else 1
                compteurs["nb_appels_llm_war_room"] += appels
                etat["nb_appels_llm"] += appels
            if raison is not None:
                raise ArretDebat(raison)
            try:
                return modele.model_validate_json(extraire_objet(reponse))
            except (ValueError, ValidationError):
                if tentative == 0:
                    emettre(etat, "decision", "systeme", f"JSON invalide du {role} : une relance")
                    prompt += "\nReponse invalide : respecte exactement le format JSON demande."
        raise ArretDebat(f"JSON invalide du {role} apres une relance, aucune penalite pour cet echange")

    motif = "plafond de tours atteint"
    try:
        for tour in range(1, config.max_tours + 1):
            if compteurs["nb_appels_llm_war_room"] >= config.max_appels_llm:
                raise ArretDebat("plafond d'appels war room atteint")
            compteurs["nb_tours_war_room"] += 1
            donnees = {
                "tour": tour, "hypothese_retenue": retenue.model_dump(),
                "alternatives": [hypothese.model_dump() for hypothese in etat["hypotheses"]
                                 if hypothese.id != retenue.id],
                "refutations_connues": etat["refutees_connues"],
            }
            objection = appeler("sceptique", donnees, Objection)
            emettre(etat, "debat", "sceptique", objection.model_dump_json())
            if objection.objection is None:
                motif = "aucune objection restante"
                break
            compteurs["nb_objections"] += 1
            reponse = appeler("detective", {
                **donnees, "objection": objection.model_dump(), "preuves": retenue.preuves,
            }, ReponseDetective)
            emettre(etat, "debat", "detective", reponse.model_dump_json())
            repondue = any(preuve in retenue.preuves for preuve in reponse.preuves_citees)
            if not repondue:
                compteurs["nb_objections_sans_reponse"] += 1
            verdict = "repondue" if repondue else "sans reponse"
            emettre(etat, "preuve", "systeme", f"Objection du tour {tour} {verdict} : verification des citations exactes")
    except ArretDebat as arret:
        motif = str(arret)
    confiance_apres = max(
        0.0, confiance_avant - PENALITE_OBJECTION_SANS_REPONSE * compteurs["nb_objections_sans_reponse"]
    )
    nouvelle = Hypothese.model_validate({**retenue.model_dump(), "confiance": confiance_apres})
    etat["retenue"] = nouvelle
    etat["hypotheses"] = [nouvelle if hypothese.id == nouvelle.id else hypothese
                          for hypothese in etat["hypotheses"]]
    etat.update(compteurs)
    emettre(etat, "decision", "systeme", (
        f"War room terminee : {motif}; tours={compteurs['nb_tours_war_room']}; "
        f"objections={compteurs['nb_objections']}; sans reponse={compteurs['nb_objections_sans_reponse']}; "
        f"appels={compteurs['nb_appels_llm_war_room']}; confiance {confiance_avant:.2f} -> {confiance_apres:.2f}"
    ))
    return etat