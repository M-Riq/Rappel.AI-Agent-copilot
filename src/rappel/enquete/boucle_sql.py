"""Boucle d'enquete en mode "sql" : le LLM propose, le code verifie par run_sql.

Un tour = un appel LLM. Les hypotheses ne sont retenues que si le verdict cite une
requete reellement executee dont le resultat contient des lignes.
"""
import json
from typing import Annotated, Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

from rappel.enquete.modeles import (
    ContexteIncident, CorrectifPropose, EnregistrementMemoire, EvenementEnquete, Hypothese,
)
from rappel.enquete.protocoles import (
    LLM, MARQUEUR_DONNEES, Abstention, DecisionAbstention, Memoire, RunSql, voie_pour_role,
)
from rappel.interfaces.run_sql import LIMITE_LIGNES, TABLES, creer_run_sql, valider_sql
from . import graphe


# Valeur par defaut de MAX_TOURS_ENQUETE ; enqueter() lit rappel.config.Configuration.
MAX_TOURS = 6
MAX_REQUETES_SUIVI = 2
NB_HYPOTHESES_MIN = 2
# Confiance initiale = CONFIANCE_PAR_REQUETE_NON_VIDE x nombre de requetes citees a resultat
# non vide (plafonnee a CONFIANCE_MAX). Calibree sur P1/P2/P3 (voir docs/module-enquete.md) :
# une requete seule, sans memoire, reste sous le seuil d'abstention de 0,6.
CONFIANCE_PAR_REQUETE_NON_VIDE = 0.45
CONFIANCE_MAX = 0.95
LIGNES_DANS_PROMPT = 10
APERCU_LIGNES_EVENEMENT = 3
RAISON_SANS_PREUVE = "aucune preuve SQL"
REQUETES_TYPES = {
    "ecart de schema": "SELECT a.nom, a.type FROM colonnes_attendues a "
                       "WHERE a.nom NOT IN (SELECT nom FROM colonnes_observees)",
    "logs au compte eleve": "SELECT template, compte FROM logs WHERE compte > 5 ORDER BY compte DESC",
    "commits recents": "SELECT hash, message, date FROM commits ORDER BY date DESC",
    "metriques": "SELECT nom, valeur FROM metriques ORDER BY nom",
}
NOTE_INVALIDE = "La reponse precedente etait invalide. Reponds uniquement avec le JSON demande.\n"


Texte = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CandidatSql(BaseModel):
    model_config = ConfigDict(extra="ignore")
    texte: Texte
    sql: Texte
    categorie: str | None = None


class VerdictSql(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Texte
    statut: Literal["retenue", "refutee"]
    requetes_citees: list[str] = []
    raison: str = ""


class SuiviSql(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hypothese_id: Texte
    sql: Texte


class ReponseVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")
    verdicts: list[VerdictSql] = []
    requetes_suivi: list[SuiviSql] = []


class ReponseConclusion(BaseModel):
    model_config = ConfigDict(extra="ignore")
    cause_confirmee: Texte
    requetes_citees: list[str] = []


class ReponseCorrectif(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: Texte
    patch: str | dict | list
    elements_touches: list[str]
    reversible: bool
    justification: Texte
    confiance: float = Field(default=0.0, ge=0, le=1)
    cause_confirmee: str | None = None


ADAPTATEUR_CANDIDATS_SQL = TypeAdapter(Annotated[list[CandidatSql], Field(min_length=NB_HYPOTHESES_MIN)])


# Mode appels d'outils natifs (LLM_APPEL_OUTILS=1) : la requete d'une hypothese ou d'un suivi peut
# venir d'un tool_call run_sql ; elle passe ensuite par valider_sql comme les autres.
NOM_OUTIL_SQL = "run_sql"
ROLES_AVEC_OUTILS = ("hypotheses", "verdict")


class CandidatOutil(BaseModel):
    model_config = ConfigDict(extra="ignore")
    texte: Texte
    sql: str | None = None
    categorie: str | None = None


class SuiviOutil(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hypothese_id: Texte
    sql: str | None = None


class ReponseVerdictOutil(BaseModel):
    model_config = ConfigDict(extra="ignore")
    verdicts: list[VerdictSql] = []
    requetes_suivi: list[SuiviOutil] = []


ADAPTATEUR_CANDIDATS_OUTIL = TypeAdapter(list[CandidatOutil])


def analyser_appels_outils(appels) -> tuple[list[str], list[str]]:
    """Extrait le SQL des tool_calls run_sql ; chaque appel illisible devient un motif de rejet."""
    requetes, rejets = [], []
    for appel in appels or []:
        fonction = appel.get("function") if isinstance(appel, dict) else None
        if not isinstance(fonction, dict):
            rejets.append("appel d'outil illisible")
            continue
        if fonction.get("name") != NOM_OUTIL_SQL:
            rejets.append(f"outil inconnu : {apercu(fonction.get('name'), 60)}")
            continue
        arguments = fonction.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                rejets.append("arguments JSON invalides")
                continue
        sql = arguments.get("sql") if isinstance(arguments, dict) else None
        if not isinstance(sql, str) or not sql.strip():
            rejets.append("requete vide")
            continue
        requetes.append(sql)
    return requetes, rejets


def candidats_avec_outils(texte: str, appels) -> tuple[list[CandidatOutil], list[str]]:
    """Hypotheses du texte JSON completees, dans l'ordre, par le SQL des tool_calls."""
    try:
        candidats = ADAPTATEUR_CANDIDATS_OUTIL.validate_json(extraire_valeur_json(texte, list))
    except (ValueError, ValidationError):
        candidats = []
    requetes, rejets = analyser_appels_outils(appels)
    for candidat in candidats:
        if candidat.sql is None or not candidat.sql.strip():
            candidat.sql = requetes.pop(0) if requetes else None
    for numero, sql in enumerate(requetes, start=len(candidats) + 1):
        candidats.append(CandidatOutil(texte=f"Hypothese du modele n{numero} (appel run_sql)", sql=sql))
    if len(candidats) < NB_HYPOTHESES_MIN:
        raise ValueError("Au moins deux hypotheses attendues (texte JSON ou appels d'outil)")
    return candidats, rejets


def verdict_avec_outils(texte: str, appels) -> tuple[ReponseVerdict, list[str]]:
    """Verdict JSON obligatoire ; le SQL des tool_calls complete les suivis sans requete."""
    reponse = ReponseVerdictOutil.model_validate_json(extraire_valeur_json(texte, dict))
    requetes, rejets = analyser_appels_outils(appels)
    suivis = []
    for suivi in reponse.requetes_suivi:
        sql = suivi.sql if suivi.sql and suivi.sql.strip() else (requetes.pop(0) if requetes else None)
        if sql is None:
            rejets.append(f"requete de suivi sans SQL pour {suivi.hypothese_id}")
            continue
        suivis.append(SuiviSql(hypothese_id=suivi.hypothese_id, sql=sql))
    rejets.extend("appel d'outil sans hypothese designee" for _ in requetes)
    return ReponseVerdict(verdicts=reponse.verdicts, requetes_suivi=suivis), rejets


class EtatSql(TypedDict, total=False):
    contexte: ContexteIncident | dict
    evenements: list[EvenementEnquete]
    souvenirs: list[EnregistrementMemoire]
    refutees_connues: list[str]
    hypotheses: list[Hypothese]
    categories: dict[str, str | None]
    requetes: dict[str, dict]
    retenue: Hypothese | None
    preuves_retenue: list[str]
    decision_abstention: DecisionAbstention
    correctif: CorrectifPropose | None
    enregistrement: EnregistrementMemoire | None
    resultat_action: Literal["reussi", "echoue"]
    nb_hypotheses_testees: int
    nb_ecartees_par_memoire: int
    nb_appels_llm: int
    erreur_llm: str | None
    nb_tours: int
    nb_requetes_sql: int
    nb_appels_rapide: int
    nb_appels_profonde: int
    plafond: bool
    nb_tours_war_room: int
    nb_objections: int
    nb_objections_sans_reponse: int
    nb_appels_llm_war_room: int


def cle_texte(texte: str) -> str:
    return " ".join(texte.split()).casefold()


def extraire_valeur_json(texte: str, type_attendu: type) -> str:
    contenu = texte.strip()
    if contenu.startswith("```"):
        lignes = contenu.splitlines()
        if lignes and lignes[-1].strip().startswith("```"):
            contenu = "\n".join(lignes[1:-1])
    ouvrant = "[" if type_attendu is list else "{"
    decodeur = json.JSONDecoder()
    for index, caractere in enumerate(contenu):
        if caractere == ouvrant:
            try:
                valeur, _ = decodeur.raw_decode(contenu[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(valeur, type_attendu):
                return json.dumps(valeur, ensure_ascii=True)
    raise ValueError("Aucune valeur JSON attendue dans la reponse")


def composant(contexte: ContexteIncident) -> str:
    return contexte.signature.partition(":")[2] or contexte.signature


def resume_contexte(contexte: ContexteIncident) -> dict:
    logs = sorted(contexte.logs, key=lambda log: log.compte, reverse=True)
    return {
        "signature": contexte.signature,
        "composant": composant(contexte),
        "logs_principaux": [{"template": log.template, "compte": log.compte} for log in logs[:10]],
        "nb_logs": len(contexte.logs),
        "commits_recents": [{"hash": commit.hash, "message": commit.message, "date": commit.date}
                            for commit in contexte.commits_recents[:5]],
        "schema_attendu": contexte.schema.attendu,
        "schema_observe": contexte.schema.observe,
        "metriques": sorted(contexte.metriques),
    }


def description_outil() -> str:
    tables = "\n".join(f"- {table}({', '.join(colonnes)})" for table, colonnes in TABLES.items())
    exemples = "\n".join(f"- {nom} : {sql}" for nom, sql in REQUETES_TYPES.items())
    return (
        "Outil run_sql : le code execute pour toi une requete SQL en lecture seule (une seule "
        f"instruction SELECT ou WITH ... SELECT, {LIMITE_LIGNES} lignes au plus) sur les tables "
        f"suivantes, et uniquement celles-ci :\n{tables}\nRequetes types :\n{exemples}\n"
    )


def construire_prompt(role: str, consignes: str, donnees: dict, note: str = "") -> str:
    return f"Role: {role}. {consignes}\n{note}{MARQUEUR_DONNEES}{json.dumps(donnees, ensure_ascii=False)}"


def apercu(texte: str, longueur: int = 160) -> str:
    texte = " ".join(str(texte).split())
    return texte if len(texte) <= longueur else texte[:longueur - 3] + "..."


def creer_graphe_sql(
    memoire: Memoire, llm: LLM, abstention: Abstention,
    sur_evenement: Callable[[EvenementEnquete], None] | None = None,
    run_sql: RunSql | None = None,
    fabrique_run_sql: Callable[[ContexteIncident], RunSql] | None = None,
    max_tours: int = MAX_TOURS,
    appel_outils: bool = False,
):
    ressources: dict[str, RunSql | None] = {"run_sql": run_sql}
    dernier = {"appels": []}

    def emettre(etat: EtatSql, type_evenement: str, acteur: str, texte: str,
                modele: str | None = None, requete_sql: str | None = None) -> None:
        etat["evenements"].append(EvenementEnquete(
            incident_id=etat["contexte"].incident_id, etape=len(etat["evenements"]) + 1,
            type=type_evenement, acteur=acteur, texte=texte, horodatage=graphe.maintenant(),
            modele=modele, requete_sql=requete_sql,
        ))
        if sur_evenement is not None:
            sur_evenement(etat["evenements"][-1].model_copy(deep=True))

    def preparer(etat: EtatSql) -> EtatSql:
        return {**etat, "evenements": list(etat.get("evenements", [])),
                "requetes": dict(etat.get("requetes", {}))}

    def appeler(etat: EtatSql, role: str, prompt: str) -> tuple[str, str | None] | None:
        if etat["nb_tours"] >= max_tours:
            if not etat.get("plafond"):
                etat["plafond"] = True
                emettre(etat, "decision", "systeme",
                        f"Plafond de {max_tours} tours atteint : appel {role} non effectue, "
                        "abstention sans correctif")
            return None
        etat["nb_tours"] += 1
        etat["nb_appels_llm"] += 1
        cle = "nb_appels_profonde" if voie_pour_role(role) == "profonde" else "nb_appels_rapide"
        etat[cle] += 1
        dernier["appels"] = []
        if appel_outils and role in ROLES_AVEC_OUTILS:
            from rappel.interfaces.tools_zetaris import RUN_SQL_TOOL

            reponse = llm.completer(prompt, role=role, tools=[RUN_SQL_TOOL])
            appels = getattr(llm, "derniers_appels_outils", None)
            dernier["appels"] = list(appels) if isinstance(appels, list) else []
        else:
            reponse = llm.completer(prompt, role=role)
        info = getattr(llm, "derniere_reponse_info", None)
        modele = info.get("modele_repondu") if isinstance(info, dict) else None
        return reponse, modele if isinstance(modele, str) and modele.strip() else None

    def demander(etat: EtatSql, role: str, consignes: str, donnees: dict, analyser):
        note = ""
        modele = None
        for _ in range(2):
            appel = appeler(etat, role, construire_prompt(role, consignes, donnees, note))
            if appel is None:
                return None, modele
            reponse, modele = appel
            try:
                if not isinstance(reponse, str):
                    raise ValueError("La reponse doit etre du texte")
                return analyser(reponse), modele
            except (ValueError, ValidationError, TypeError, AttributeError, KeyError):
                note = NOTE_INVALIDE
        etat["erreur_llm"] = f"reponse {role} invalide apres une nouvelle tentative"
        emettre(etat, "decision", "systeme", f"Reponse {role} invalide apres une nouvelle tentative", modele)
        return None, modele

    def executer_requete(etat: EtatSql, hypothese: Hypothese, sql: str, origine: str,
                         modele: str | None) -> None:
        identifiant = f"q{len(etat['requetes']) + 1}"
        validation = valider_sql(sql, TABLES)
        requete = {"id": identifiant, "hypothese_id": hypothese.id, "origine": origine,
                   "sql": validation.requete, "executee": False, "colonnes": [], "lignes": [],
                   "nb_lignes": 0, "tronque": False, "erreur": validation.erreur, "tables": []}
        etat["requetes"][identifiant] = requete
        if validation.erreur is not None:
            # Refus avant tout appel au backend : aucune execution.
            emettre(etat, "rejet", "systeme",
                    f"{identifiant} non executee pour '{hypothese.texte}' : {validation.erreur} ; "
                    f"SQL propose : {apercu(sql)}")
            return
        outil = ressources["run_sql"]
        resultat = outil.executer(sql)
        etat["nb_requetes_sql"] += 1
        requete.update(sql=resultat.requete, executee=True, colonnes=resultat.colonnes,
                       lignes=resultat.lignes, nb_lignes=resultat.nb_lignes,
                       tronque=resultat.tronque, erreur=resultat.erreur,
                       tables=resultat.tables or validation.tables)
        if resultat.erreur is not None:
            emettre(etat, "rejet", "systeme",
                    f"{identifiant} [{outil.mode}] en erreur pour '{hypothese.texte}' : {resultat.erreur}",
                    requete_sql=resultat.requete)
            return
        suffixe = " (tronque)" if resultat.tronque else ""
        emettre(etat, "preuve", "detective",
                f"{identifiant} [{outil.mode}] {hypothese.texte} : {resultat.nb_lignes} ligne(s){suffixe} ; "
                f"colonnes {resultat.colonnes} ; apercu {apercu(json.dumps(resultat.lignes[:APERCU_LIGNES_EVENEMENT], ensure_ascii=False), 240)}",
                modele=modele, requete_sql=resultat.requete)

    def resume_hypothese(etat: EtatSql, hypothese: Hypothese, lignes: int = LIGNES_DANS_PROMPT) -> dict:
        return {
            "id": hypothese.id, "texte": hypothese.texte,
            "categorie": etat["categories"].get(hypothese.id),
            "requetes": [
                {cle: requete[cle] for cle in ("id", "origine", "sql", "colonnes", "nb_lignes",
                                                 "tronque", "erreur", "tables")}
                | {"lignes": requete["lignes"][:lignes]}
                for requete in etat["requetes"].values() if requete["hypothese_id"] == hypothese.id
            ],
        }

    def charger_contexte(etat: EtatSql):
        etat = preparer(etat)
        valeur = etat["contexte"]
        if isinstance(valeur, ContexteIncident):
            valeur = valeur.model_dump()
        etat["contexte"] = ContexteIncident.model_validate(valeur)
        if ressources["run_sql"] is None:
            ressources["run_sql"] = (fabrique_run_sql or creer_run_sql)(etat["contexte"])
        emettre(etat, "decision", "systeme",
                f"Contexte valide, enquete ouverte (preuves SQL : {ressources['run_sql'].mode})")
        return etat

    def consulter_memoire(etat: EtatSql):
        etat = preparer(etat)
        signature = etat["contexte"].signature
        souvenirs = memoire.chercher(signature)
        refutees = memoire.hypotheses_refutees(signature)
        etat.update(souvenirs=souvenirs, refutees_connues=refutees)
        texte = "aucun incident similaire"
        if souvenirs:
            texte = "; ".join(graphe.decrire_souvenir(souvenir) for souvenir in souvenirs)
            texte += "; hypotheses refutees connues : " + json.dumps(refutees, ensure_ascii=True)
        emettre(etat, "decision", "systeme", texte)
        return etat

    def proposer_hypotheses(etat: EtatSql):
        etat = preparer(etat)
        contexte = etat["contexte"]
        refutees = etat["refutees_connues"]
        consignes = (
            "Propose au moins deux hypotheses de cause, chacune avec UNE requete SQL qui la mettrait "
            "a l'epreuve. Tu ne conclus pas : le code execute les requetes. "
            + description_outil()
            + "Hypotheses deja refutees pour cette signature, a NE PAS reproposer : "
            + json.dumps(refutees, ensure_ascii=False)
            + "\nRetourne UNIQUEMENT un tableau JSON d'objets "
            "{\"texte\": string, \"sql\": string, \"categorie\": string}."
            + (" Tu peux aussi appeler l'outil run_sql : chaque appel fournit, dans l'ordre, la requete "
               "d'une hypothese du tableau sans champ sql." if appel_outils else "")
        )
        donnees = {"signature": contexte.signature, "resume": resume_contexte(contexte),
                   "hypotheses_refutees": refutees}

        def analyser(reponse):
            if appel_outils:
                return candidats_avec_outils(reponse, dernier["appels"])
            return ADAPTATEUR_CANDIDATS_SQL.validate_json(extraire_valeur_json(reponse, list)), []

        analyse, modele = demander(etat, "hypotheses", consignes, donnees, analyser)
        hypotheses, categories = [], {}
        etat.update(hypotheses=hypotheses, categories=categories)
        if analyse is None:
            return etat
        candidats, rejets_outils = analyse
        for raison in rejets_outils:
            emettre(etat, "rejet", "systeme", f"Appel d'outil run_sql rejete : {raison}", modele)
        connues = {cle_texte(texte) for texte in refutees}
        for indice, candidat in enumerate(candidats, start=1):
            if cle_texte(candidat.texte) in connues:
                etat["nb_ecartees_par_memoire"] += 1
                emettre(etat, "rejet", "systeme", f"Deja refutee en memoire: {candidat.texte}")
                continue
            hypothese = Hypothese(
                id=f"{contexte.incident_id}:hyp-{indice}", incident_id=contexte.incident_id,
                texte=candidat.texte, statut="ouverte", preuves=[], confiance=0.0, raison_rejet=None,
            )
            hypotheses.append(hypothese)
            categories[hypothese.id] = candidat.categorie
            emettre(etat, "hypothese", "detective", hypothese.texte, modele)
            if not candidat.sql:
                emettre(etat, "rejet", "systeme",
                        f"Aucune requete SQL fournie pour '{hypothese.texte}' (ni texte ni appel d'outil)", modele)
                continue
            executer_requete(etat, hypothese, candidat.sql, "hypothese", modele)
            etat["nb_hypotheses_testees"] += 1
        if not hypotheses:
            emettre(etat, "rejet", "systeme", "Aucune hypothese disponible apres filtrage par la memoire")
        return etat

    def juger(etat: EtatSql):
        etat = preparer(etat)
        hypotheses = etat["hypotheses"]
        etat.update(retenue=None, preuves_retenue=[])
        if not hypotheses or etat.get("plafond"):
            return etat
        par_id = {hypothese.id: hypothese for hypothese in hypotheses}
        consignes = (
            "Juge chaque hypothese UNIQUEMENT d'apres les resultats SQL fournis. Une hypothese "
            "retenue doit citer les identifiants de requetes (q1, q2...) dont le resultat la soutient. "
            f"Tu peux demander au plus {MAX_REQUETES_SUIVI} requetes de suivi en tout "
            "(champ requetes_suivi), memes regles que run_sql. " + description_outil()
            + "Retourne UNIQUEMENT un objet JSON {\"verdicts\": [{\"id\": string, \"statut\": "
            "\"retenue\"|\"refutee\", \"requetes_citees\": [string], \"raison\": string}], "
            "\"requetes_suivi\": [{\"hypothese_id\": string, \"sql\": string}]}."
        )
        suivis_utilises = 0
        verdicts = None
        while True:
            donnees = {
                "signature": etat["contexte"].signature,
                "hypotheses": [resume_hypothese(etat, hypothese) for hypothese in hypotheses],
                "incident_similaire": bool(etat["souvenirs"]),
                "souvenirs": [graphe.decrire_souvenir(souvenir) for souvenir in etat["souvenirs"]],
                "suivis_restants": MAX_REQUETES_SUIVI - suivis_utilises,
            }
            def analyser_verdict(texte):
                if appel_outils:
                    return verdict_avec_outils(texte, dernier["appels"])
                return ReponseVerdict.model_validate_json(extraire_valeur_json(texte, dict)), []

            analyse, modele = demander(etat, "verdict", consignes, donnees, analyser_verdict)
            if analyse is None:
                break
            reponse, rejets_outils = analyse
            for raison in rejets_outils:
                emettre(etat, "rejet", "systeme", f"Appel d'outil run_sql rejete : {raison}", modele)
            suivis = [suivi for suivi in reponse.requetes_suivi if suivi.hypothese_id in par_id]
            suivis = suivis[:MAX_REQUETES_SUIVI - suivis_utilises]
            if suivis:
                emettre(etat, "decision", "systeme",
                        f"Verdict provisoire (tour {etat['nb_tours']}) : {len(suivis)} requete(s) de suivi demandee(s)",
                        modele)
                for suivi in suivis:
                    executer_requete(etat, par_id[suivi.hypothese_id], suivi.sql, "suivi", modele)
                    suivis_utilises += 1
                continue
            verdicts = {verdict.id: verdict for verdict in reponse.verdicts}
            resume = "; ".join(f"{verdict.id.rpartition(':')[2]} {verdict.statut} {verdict.requetes_citees}"
                               for verdict in reponse.verdicts) or "aucun verdict"
            emettre(etat, "decision", "systeme", f"Verdict du modele (tour {etat['nb_tours']}) : {resume}", modele)
            break

        if verdicts is None:
            if etat.get("plafond"):
                return etat
            verdicts = {}
        candidates: dict[str, list[str]] = {}
        raisons: dict[str, str] = {}
        details: dict[str, str] = {}
        for hypothese in hypotheses:
            verdict = verdicts.get(hypothese.id)
            if verdict is None:
                raisons[hypothese.id] = "aucun verdict valide du modele"
            elif verdict.statut == "retenue":
                valides = [
                    identifiant for identifiant in dict.fromkeys(verdict.requetes_citees)
                    if identifiant in etat["requetes"]
                    and etat["requetes"][identifiant]["hypothese_id"] == hypothese.id
                    and etat["requetes"][identifiant]["executee"]
                    and etat["requetes"][identifiant]["erreur"] is None
                    and etat["requetes"][identifiant]["nb_lignes"] > 0
                ]
                if valides:
                    candidates[hypothese.id] = valides
                else:
                    raisons[hypothese.id] = RAISON_SANS_PREUVE
                    details[hypothese.id] = (f"le verdict cite {verdict.requetes_citees} : requete "
                                             "inexistante, non executee ou sans ligne")
            else:
                raisons[hypothese.id] = verdict.raison.strip() or "refutee par le verdict"
        gagnante = max(candidates, key=lambda identifiant: len(candidates[identifiant]), default=None)
        nouvelles = []
        for hypothese in hypotheses:
            if hypothese.id == gagnante:
                citees = candidates[gagnante]
                preuves = [
                    f"{identifiant} [{', '.join(etat['requetes'][identifiant]['tables'])}] "
                    f"{etat['requetes'][identifiant]['nb_lignes']} ligne(s) : {etat['requetes'][identifiant]['sql']}"
                    for identifiant in citees
                ]
                nouvelle = Hypothese.model_validate({
                    **hypothese.model_dump(), "statut": "retenue", "preuves": preuves,
                    "confiance": min(CONFIANCE_MAX, CONFIANCE_PAR_REQUETE_NON_VIDE * len(citees)),
                    "raison_rejet": None,
                })
                etat.update(retenue=nouvelle, preuves_retenue=citees)
                emettre(etat, "decision", "systeme",
                        f"Hypothese retenue par le code : {nouvelle.texte} (preuves SQL : "
                        f"{', '.join(citees)} ; confiance initiale {nouvelle.confiance:.2f})")
            else:
                raison = raisons.get(hypothese.id)
                if raison is None:
                    raison = f"ecartee au profit de {par_id[gagnante].texte}"
                nouvelle = Hypothese.model_validate({
                    **hypothese.model_dump(), "statut": "refutee", "preuves": [],
                    "confiance": 0.0, "raison_rejet": raison,
                })
                detail = f" ; {details[hypothese.id]}" if hypothese.id in details else ""
                emettre(etat, "rejet", "systeme", f"{nouvelle.texte} : refutee ({raison}){detail}")
            nouvelles.append(nouvelle)
        etat["hypotheses"] = nouvelles
        return etat

    def tables_preuves(etat: EtatSql) -> list[str]:
        return sorted({table for identifiant in etat.get("preuves_retenue", [])
                       for table in etat["requetes"][identifiant]["tables"]})

    def evaluer_confiance(etat: EtatSql):
        etat = preparer(etat)
        retenue = etat.get("retenue")
        if retenue is None:
            manque = []
            if etat.get("plafond"):
                manque.append(f"plafond de {max_tours} tours atteint avant la conclusion")
            if etat.get("erreur_llm"):
                manque.append(etat["erreur_llm"])
            manque.append("hypothese etayee par une requete SQL a resultat non vide")
            manque.append("deuxième source indépendante (table distincte)")
            if not etat["souvenirs"]:
                manque.append("incident similaire en mémoire")
            decision = DecisionAbstention(True, 0.0, manque)
            sources = []
        else:
            sources = tables_preuves(etat)
            decision = abstention.decider(retenue, nb_sources=len(sources),
                                          incident_similaire=bool(etat["souvenirs"]))
        etat["decision_abstention"] = decision
        emettre(etat, "decision", "systeme",
                f"Confiance {decision.confiance:.2f}; abstention={decision.abstient}; "
                f"tables a preuve non vide : {sources}")
        return etat

    def terminer_abstention(etat: EtatSql):
        etat = preparer(etat)
        emettre(etat, "abstention", "systeme",
                "; ".join(etat["decision_abstention"].manque) or "Confiance insuffisante")
        return etat

    def renoncer(etat: EtatSql, raison: str) -> EtatSql:
        if etat.get("plafond"):
            raison = f"plafond de {max_tours} tours atteint avant la conclusion"
        decision = etat["decision_abstention"]
        etat["decision_abstention"] = DecisionAbstention(True, decision.confiance, [*decision.manque, raison])
        etat["correctif"] = None
        return etat

    def conclure(etat: EtatSql):
        etat = preparer(etat)
        contexte = etat["contexte"]
        retenue = etat["retenue"]
        citees = etat["preuves_retenue"]
        decision = etat["decision_abstention"]
        # Memoire : un correctif deja reussi pour la signature permet un seul appel profond.
        fusion = any(souvenir.resultat == "reussi" for souvenir in etat["souvenirs"])
        resume = {"signature": contexte.signature, "composant": composant(contexte),
                  "schema_attendu": contexte.schema.attendu, "schema_observe": contexte.schema.observe}
        hypothese = resume_hypothese(etat, retenue)
        hypothese["requetes"] = [requete for requete in hypothese["requetes"] if requete["id"] in citees]
        cause = None
        if not fusion:
            reponse, modele = demander(
                etat, "conclusion",
                "Confirme la cause de l'incident a partir des seules preuves SQL citees. Retourne "
                "UNIQUEMENT un objet JSON {\"cause_confirmee\": string, \"requetes_citees\": [string]}.",
                {"hypothese": hypothese, "contexte": resume},
                lambda texte: ReponseConclusion.model_validate_json(extraire_valeur_json(texte, dict)),
            )
            if reponse is None:
                return renoncer(etat, "conclusion non obtenue")
            cause = reponse.cause_confirmee
            emettre(etat, "decision", "systeme", f"Cause confirmee : {cause}", modele)

        def analyser_correctif(texte: str) -> ReponseCorrectif:
            reponse = ReponseCorrectif.model_validate_json(extraire_valeur_json(texte, dict))
            if fusion and not (reponse.cause_confirmee or "").strip():
                raise ValueError("cause_confirmee obligatoire")
            return reponse

        consignes = (
            "Propose un correctif reversible pour la cause confirmee. La justification doit citer "
            "les identifiants des requetes SQL. Retourne UNIQUEMENT un objet JSON {\"description\": "
            "string, \"patch\": string, \"elements_touches\": [string], \"reversible\": bool, "
            "\"justification\": string, \"confiance\": number"
            + (", \"cause_confirmee\": string" if fusion else "") + "}."
        )
        donnees = {"hypothese": hypothese, "contexte": resume, "cause_confirmee": cause,
                   "inclure_conclusion": fusion, "confiance": decision.confiance,
                   "souvenirs": [graphe.decrire_souvenir(souvenir) for souvenir in etat["souvenirs"]]}
        reponse, modele = demander(etat, "correctif", consignes, donnees, analyser_correctif)
        if reponse is None:
            return renoncer(etat, "correctif non obtenu")
        justification = reponse.justification
        if not any(identifiant in justification for identifiant in citees):
            justification += f" (requetes SQL : {', '.join(citees)})"
        if etat["souvenirs"]:
            justification += "; " + "; ".join(graphe.decrire_souvenir(souvenir) for souvenir in etat["souvenirs"])
        try:
            correctif = CorrectifPropose(
                description=reponse.description,
                patch=reponse.patch if isinstance(reponse.patch, str) else json.dumps(reponse.patch, indent=2),
                niveau_risque="validation", elements_touches=reponse.elements_touches,
                reversible=reponse.reversible, justification=justification,
                confiance=min(reponse.confiance, decision.confiance),
            )
        except ValidationError:
            return renoncer(etat, "correctif invalide")
        etat["correctif"] = correctif
        prefixe = f"Cause confirmee (reutilisation memoire) : {reponse.cause_confirmee} ; " if fusion else ""
        emettre(etat, "decision", "systeme", f"{prefixe}Correctif propose pour validation: {correctif.description}",
                modele)
        return etat

    def ecrire_memoire(etat: EtatSql):
        etat = preparer(etat)
        enregistrement = EnregistrementMemoire(
            signature=etat["contexte"].signature,
            cause_confirmee=etat["correctif"].justification,
            hypotheses_refutees=[hypothese.texte for hypothese in etat["hypotheses"] if hypothese.statut == "refutee"],
            correctif_essaye=etat["correctif"].description,
            resultat=etat["resultat_action"], date=graphe.aujourdhui(),
        )
        memoire.ecrire(enregistrement)
        etat["enregistrement"] = enregistrement
        emettre(etat, "decision", "systeme", "Resultat provisoire enregistre en memoire")
        return etat

    def orienter(etat: EtatSql) -> str:
        return "abstention" if etat["decision_abstention"].abstient else "conclure"

    def orienter_apres_conclusion(etat: EtatSql) -> str:
        return "ecrire_memoire" if etat.get("correctif") is not None else "abstention"

    graphe_sql = StateGraph(EtatSql)
    noeuds = {
        "charger_contexte": charger_contexte, "consulter_memoire": consulter_memoire,
        "proposer_hypotheses": proposer_hypotheses, "juger": juger,
        "evaluer_confiance": evaluer_confiance, "abstention": terminer_abstention,
        "conclure": conclure, "ecrire_memoire": ecrire_memoire,
    }
    for nom, fonction in noeuds.items():
        graphe_sql.add_node(nom, fonction)
    sequence = [START, "charger_contexte", "consulter_memoire", "proposer_hypotheses", "juger",
                "evaluer_confiance"]
    for depart, arrivee in zip(sequence, sequence[1:]):
        graphe_sql.add_edge(depart, arrivee)
    graphe_sql.add_conditional_edges("evaluer_confiance", orienter,
                                     {"abstention": "abstention", "conclure": "conclure"})
    graphe_sql.add_conditional_edges("conclure", orienter_apres_conclusion,
                                     {"ecrire_memoire": "ecrire_memoire", "abstention": "abstention"})
    graphe_sql.add_edge("abstention", END)
    graphe_sql.add_edge("ecrire_memoire", END)
    return graphe_sql.compile(), ressources


def etat_initial_sql(contexte, resultat: str) -> EtatSql:
    return {
        "contexte": contexte, "evenements": [], "hypotheses": [], "categories": {}, "requetes": {},
        "retenue": None, "preuves_retenue": [], "correctif": None, "enregistrement": None,
        "resultat_action": resultat, "nb_hypotheses_testees": 0, "nb_ecartees_par_memoire": 0,
        "nb_appels_llm": 0, "erreur_llm": None, "nb_tours": 0, "nb_requetes_sql": 0,
        "nb_appels_rapide": 0, "nb_appels_profonde": 0, "plafond": False,
        "nb_tours_war_room": 0, "nb_objections": 0, "nb_objections_sans_reponse": 0,
        "nb_appels_llm_war_room": 0,
    }
