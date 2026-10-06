from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from ..contrats import (
    ContexteIncident, CorrectifPropose, EnregistrementMemoire, EvenementEnquete, Hypothese,
)
from ..interfaces.abstention import Abstention, DecisionAbstention
from ..interfaces.llm import LLM
from ..interfaces.memoire import Memoire
from . import outils


@dataclass
class MetriquesEnquete:
    nb_evenements: int = 0
    nb_hypotheses_testees: int = 0
    nb_ecartees_par_memoire: int = 0
    nb_appels_llm: int = 0
    duree_ms: float = 0.0


@dataclass
class ResultatEnquete:
    hypotheses: list[Hypothese]
    evenements: list[EvenementEnquete]
    correctif: CorrectifPropose | None
    decision_abstention: DecisionAbstention
    enregistrement: EnregistrementMemoire | None
    metriques: MetriquesEnquete


class EtatEnquete(TypedDict, total=False):
    contexte: ContexteIncident | dict
    hypotheses: list[Hypothese]
    categories: dict[str, str]
    evenements: list[EvenementEnquete]
    souvenirs: list[EnregistrementMemoire]
    refutees_connues: list[str]
    retenue: Hypothese | None
    decision_abstention: DecisionAbstention
    correctif: CorrectifPropose | None
    enregistrement: EnregistrementMemoire | None
    resultat_action: Literal["reussi", "echoue"]
    nb_hypotheses_testees: int
    nb_ecartees_par_memoire: int
    nb_appels_llm: int


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat()


def evenement(etat: EtatEnquete, type_evenement: str, acteur: str, texte: str) -> None:
    etat["evenements"].append(EvenementEnquete(
        incident_id=etat["contexte"].incident_id,
        etape=len(etat["evenements"]) + 1,
        type=type_evenement, acteur=acteur, texte=texte, horodatage=maintenant(),
    ))


def preparer(etat: EtatEnquete) -> EtatEnquete:
    return {**etat, "evenements": list(etat.get("evenements", []))}


def decrire_souvenir(souvenir: EnregistrementMemoire) -> str:
    if souvenir.resultat == "reussi":
        return f"incident similaire résolu par : {souvenir.correctif_essaye} (date : {souvenir.date})"
    return f"échec du correctif déjà essayé : {souvenir.correctif_essaye} (date : {souvenir.date})"


def creer_graphe(memoire: Memoire, llm: LLM, abstention: Abstention):
    def charger_contexte(etat: EtatEnquete):
        etat = preparer(etat)
        valeur = etat["contexte"]
        if isinstance(valeur, ContexteIncident):
            valeur = valeur.model_dump()
        etat["contexte"] = ContexteIncident.model_validate(valeur)
        evenement(etat, "decision", "systeme", "Contexte valide, enquete ouverte")
        return etat

    def consulter_memoire(etat: EtatEnquete):
        etat = preparer(etat)
        signature = etat["contexte"].signature
        souvenirs = memoire.chercher(signature)
        refutees = memoire.hypotheses_refutees(signature)
        etat.update(souvenirs=souvenirs, refutees_connues=refutees)
        texte = "aucun incident similaire"
        if souvenirs:
            texte = "; ".join(decrire_souvenir(souvenir) for souvenir in souvenirs)
            texte += "; hypotheses refutees connues : " + json.dumps(refutees, ensure_ascii=True)
        evenement(etat, "preuve", "historien", texte)
        return etat

    def generer_hypotheses(etat: EtatEnquete):
        etat = preparer(etat)
        prompt = (
            'Propose au moins trois candidats generiques independants du contexte. '
            'Retourne une liste JSON de {"texte": str, "categorie": str}. '
            'Categories autorisees: schema, ressources, reseau, configuration.'
        )
        etat["nb_appels_llm"] = etat.get("nb_appels_llm", 0) + 1
        candidats = json.loads(llm.completer(prompt, role="detective"))
        categories_autorisees = {"schema", "ressources", "reseau", "configuration"}
        if not isinstance(candidats, list) or len(candidats) < 3:
            raise ValueError("Le LLM doit proposer une liste d'au moins trois candidats")
        hypotheses = []
        categories = {}
        for indice, candidat in enumerate(candidats, start=1):
            if (
                not isinstance(candidat, dict) or set(candidat) != {"texte", "categorie"}
                or not isinstance(candidat["texte"], str) or not candidat["texte"].strip()
                or candidat["categorie"] not in categories_autorisees
            ):
                raise ValueError("Candidat LLM invalide: texte et categorie autorisee requis")
            if candidat["texte"] in etat["refutees_connues"]:
                etat["nb_ecartees_par_memoire"] = etat.get("nb_ecartees_par_memoire", 0) + 1
                evenement(etat, "rejet", "historien", f"Deja refutee en memoire: {candidat['texte']}")
                continue
            hypothese = Hypothese(
                id=f"{etat['contexte'].incident_id}:hyp-{indice}",
                incident_id=etat["contexte"].incident_id, texte=candidat["texte"],
                statut="ouverte", preuves=[], confiance=0.0, raison_rejet=None,
            )
            hypotheses.append(hypothese)
            categories[hypothese.id] = candidat["categorie"]
            evenement(etat, "hypothese", "detective", hypothese.texte)
        etat.update(hypotheses=hypotheses, categories=categories)
        return etat

    def tester_hypotheses(etat: EtatEnquete):
        etat = preparer(etat)
        fonctions = {
            "schema": outils.tester_schema, "ressources": outils.tester_ressources,
            "reseau": outils.tester_reseau, "configuration": outils.tester_configuration,
        }
        preuves = {}
        for hypothese in etat["hypotheses"]:
            preuves[hypothese.id] = fonctions[etat["categories"][hypothese.id]](etat["contexte"])
            etat["nb_hypotheses_testees"] = etat.get("nb_hypotheses_testees", 0) + 1
        candidates = [hypothese for hypothese in etat["hypotheses"] if preuves[hypothese.id]]
        gagnante = max(candidates, key=lambda hypothese: len(preuves[hypothese.id]), default=None)
        hypotheses = []
        retenue = None
        for hypothese in etat["hypotheses"]:
            indices = preuves[hypothese.id]
            choisie = gagnante is not None and hypothese.id == gagnante.id
            raison = None if choisie else (
                f"ecartee au profit de {gagnante.texte}" if indices else "Aucune preuve fournie par l'outil"
            )
            nouvelle = Hypothese.model_validate({
                **hypothese.model_dump(), "preuves": indices,
                "statut": "retenue" if choisie else "refutee",
                "confiance": min(0.95, 0.3 * len(indices)), "raison_rejet": raison,
            })
            hypotheses.append(nouvelle)
            if choisie:
                retenue = nouvelle
            evenement(etat, "preuve" if choisie else "rejet", "systeme",
                      f"{nouvelle.texte}: {json.dumps(indices)}; {raison or 'retenue'}")
        if not hypotheses:
            evenement(etat, "rejet", "systeme", "Aucune hypothese disponible apres filtrage")
        etat.update(hypotheses=hypotheses, retenue=retenue)
        return etat

    def evaluer_confiance(etat: EtatEnquete):
        etat = preparer(etat)
        retenue = etat["retenue"]
        if retenue is None:
            decision = DecisionAbstention(True, 0.0, ["hypothese etayee par une preuve"])
        else:
            sources = {preuve.partition(":")[0] for preuve in retenue.preuves}
            decision = abstention.decider(
                retenue, nb_sources=len(sources & {"logs", "commits", "schema"}),
                incident_similaire=bool(etat["souvenirs"]),
            )
            if etat["categories"][retenue.id] == "schema" and not outils.ecarts_schema(etat["contexte"]):
                decision = DecisionAbstention(
                    True, decision.confiance, [*decision.manque, "ecart de schema confirme"]
                )
        etat["decision_abstention"] = decision
        evenement(etat, "decision", "systeme",
                  f"Confiance {decision.confiance:.2f}; abstention={decision.abstient}")
        return etat

    def terminer_abstention(etat: EtatEnquete):
        etat = preparer(etat)
        evenement(etat, "abstention", "systeme", "; ".join(etat["decision_abstention"].manque)
                  or "Confiance insuffisante")
        return etat

    def conclure(etat: EtatEnquete):
        etat = preparer(etat)
        contexte = etat["contexte"]
        retenue = etat["retenue"]
        categorie = etat["categories"][retenue.id]
        composant = contexte.signature.partition(":")[2] or contexte.signature
        if categorie == "schema":
            attendu = outils.colonnes(contexte.schema.attendu)
            observe = outils.colonnes(contexte.schema.observe)
            manquantes = set(attendu) - set(observe)
            nouvelles = set(observe) - set(attendu)
            cause = "colonne renommee probable" if manquantes and nouvelles else "ecart de schema"
            if not manquantes and not nouvelles:
                cause = "type de colonne modifie"
            description = f"Restaurer le schema attendu de {composant}: {', '.join(contexte.schema.attendu)}"
            patch = json.dumps({
                "composant": composant, "avant": contexte.schema.observe,
                "apres": contexte.schema.attendu,
            }, indent=2)
            justification = f"{cause}: {'; '.join(outils.ecarts_schema(contexte))}"
        else:
            description = f"Examiner et corriger {categorie} pour {composant} apres validation"
            patch = json.dumps({"composant": composant, "categorie": categorie, "preuves": retenue.preuves})
            justification = f"{retenue.texte}: {'; '.join(retenue.preuves)}"
        if etat["souvenirs"]:
            justification += "; " + "; ".join(
                decrire_souvenir(souvenir) for souvenir in etat["souvenirs"]
            )
        correctif = CorrectifPropose(
            description=description, patch=patch, niveau_risque="validation", reversible=True,
            elements_touches=[composant], justification=justification,
            confiance=etat["decision_abstention"].confiance,
        )
        etat["correctif"] = correctif
        evenement(etat, "decision", "systeme", f"Correctif propose pour validation: {description}")
        return etat

    def ecrire_memoire(etat: EtatEnquete):
        etat = preparer(etat)
        enregistrement = EnregistrementMemoire(
            signature=etat["contexte"].signature,
            cause_confirmee=etat["correctif"].justification,
            hypotheses_refutees=[hypothese.texte for hypothese in etat["hypotheses"] if hypothese.statut == "refutee"],
            correctif_essaye=etat["correctif"].description,
            resultat=etat["resultat_action"], date=maintenant(),
        )
        memoire.ecrire(enregistrement)
        etat["enregistrement"] = enregistrement
        evenement(etat, "decision", "historien", "Resultat provisoire enregistre en memoire")
        return etat

    def orienter(etat: EtatEnquete) -> str:
        return "abstention" if etat["decision_abstention"].abstient else "conclure"

    graphe = StateGraph(EtatEnquete)
    noeuds = {
        "charger_contexte": charger_contexte, "consulter_memoire": consulter_memoire,
        "generer_hypotheses": generer_hypotheses, "tester_hypotheses": tester_hypotheses,
        "evaluer_confiance": evaluer_confiance, "abstention": terminer_abstention,
        "conclure": conclure, "ecrire_memoire": ecrire_memoire,
    }
    for nom, fonction in noeuds.items():
        graphe.add_node(nom, fonction)
    sequence = [START, "charger_contexte", "consulter_memoire", "generer_hypotheses",
                "tester_hypotheses", "evaluer_confiance"]
    for depart, arrivee in zip(sequence, sequence[1:]):
        graphe.add_edge(depart, arrivee)
    graphe.add_conditional_edges("evaluer_confiance", orienter,
                                {"abstention": "abstention", "conclure": "conclure"})
    graphe.add_edge("abstention", END)
    graphe.add_edge("conclure", "ecrire_memoire")
    graphe.add_edge("ecrire_memoire", END)
    return graphe.compile()


def enqueter(
    contexte: ContexteIncident | dict, memoire: Memoire, llm: LLM, abstention: Abstention,
    resultat: Literal["reussi", "echoue"] = "reussi",
) -> ResultatEnquete:
    # Resultat provisoire jusqu'a l'integration avec le module Action.
    if resultat not in {"reussi", "echoue"}:
        raise ValueError("resultat doit etre reussi ou echoue")
    debut = perf_counter()
    etat = creer_graphe(memoire, llm, abstention).invoke({
        "contexte": contexte, "evenements": [], "hypotheses": [],
        "correctif": None, "enregistrement": None, "resultat_action": resultat,
        "nb_hypotheses_testees": 0, "nb_ecartees_par_memoire": 0, "nb_appels_llm": 0,
    })
    return ResultatEnquete(
        hypotheses=etat["hypotheses"], evenements=etat["evenements"],
        correctif=etat["correctif"], decision_abstention=etat["decision_abstention"],
        enregistrement=etat["enregistrement"],
        metriques=MetriquesEnquete(
            nb_evenements=len(etat["evenements"]),
            nb_hypotheses_testees=etat["nb_hypotheses_testees"],
            nb_ecartees_par_memoire=etat["nb_ecartees_par_memoire"],
            nb_appels_llm=etat["nb_appels_llm"], duree_ms=(perf_counter() - debut) * 1000,
        ),
    )


def ecrire_evenements_jsonl(evenements: list[EvenementEnquete], chemin: str | Path) -> None:
    destination = Path(chemin)
    destination.parent.mkdir(parents=True, exist_ok=True)
    contenu = "".join(
        EvenementEnquete.model_validate(evenement.model_dump()).model_dump_json() + "\n"
        for evenement in evenements
    )
    destination.write_text(contenu, encoding="utf-8")