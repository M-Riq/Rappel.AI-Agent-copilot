import re
import unicodedata

from rappel.enquete.modeles import ContexteIncident


SEUIL_LOG_RESSOURCES = 5
SEUIL_LOG_RESEAU = 5
NIVEAUX_LOG_IGNORES = ("info",)
MOTS_ETAT_SAIN = ("passed", "healthy", "recovered", "succeeded", "ok")
MOTS_RESSOURCES = ("memory", "memoire", "disk", "disque", "oom")
MOTS_RESEAU = ("timeout", "connection", "connexion", "network", "reseau")
TYPE_INCONNU = "?"
MOTS_SCHEMA_COMMITS = (r"rename(?:d|s)?", r"renaming", r"renomm\w*", r"types?", r"columns?", r"colonnes?", r"schemas?")
MOTS_SCHEMA_LOGS = (r"columns?", r"colonnes?", r"types?", "keyerror")
MOTS_CONFIGURATION = ("config", r"configurations?", r"parametres?", r"parameters?")
MOTIF_ENTETE_HTTP = r"\bcontent-type\b"


def normaliser(texte: str) -> str:
    return "".join(
        caractere for caractere in unicodedata.normalize("NFD", texte.lower())
        if not unicodedata.combining(caractere)
    )


def colonnes(schema: list[str]) -> dict[str, str]:
    resultat = {}
    for colonne in schema:
        nom, _, type_colonne = colonne.partition(":")
        nom = nom.strip()
        if not nom or nom in resultat:
            continue
        resultat[nom] = type_colonne.strip() or TYPE_INCONNU
    return resultat


def ecarts_schema(contexte: ContexteIncident) -> list[str]:
    attendu = colonnes(contexte.schema.attendu)
    observe = colonnes(contexte.schema.observe)
    preuves = []
    for nom, type_colonne in attendu.items():
        if nom not in observe:
            preuves.append(f"schema: colonne manquante {nom}:{type_colonne}")
        elif TYPE_INCONNU not in (observe[nom], type_colonne) and observe[nom] != type_colonne:
            preuves.append(
                f"schema: type different pour {nom}: attendu {type_colonne}, observe {observe[nom]}"
            )
    for nom, type_colonne in observe.items():
        if nom not in attendu:
            preuves.append(f"schema: nouvelle colonne {nom}:{type_colonne}")
    return preuves


def log_etat_sain(template: str) -> bool:
    termes = set(re.findall(r"\b\w+\b", normaliser(template)))
    return bool(termes.intersection(NIVEAUX_LOG_IGNORES + MOTS_ETAT_SAIN))


def contient_mot(texte: str, motifs: tuple[str, ...]) -> bool:
    texte = re.sub(MOTIF_ENTETE_HTTP, " ", normaliser(texte))
    return any(re.search(rf"\b(?:{motif})\b", texte) for motif in motifs)


def preuves_logs(
    contexte: ContexteIncident, mots: tuple[str, ...], seuil: int,
    exclure_etats_sains: bool = False,
) -> list[str]:
    return [
        f"logs: {log.template} (compte={log.compte})"
        for log in contexte.logs
        if log.compte > seuil and contient_mot(log.template, mots)
        and (not exclure_etats_sains or not log_etat_sain(log.template))
    ]


def preuves_commits(contexte: ContexteIncident, mots: tuple[str, ...]) -> list[str]:
    return [
        f"commits: {commit.hash}: {commit.message} ({commit.date})"
        for commit in contexte.commits_recents
        if contient_mot(commit.message, mots)
    ]


def tester_schema(contexte: ContexteIncident) -> list[str]:
    return (
        ecarts_schema(contexte)
        + preuves_commits(contexte, MOTS_SCHEMA_COMMITS)
        + preuves_logs(contexte, MOTS_SCHEMA_LOGS, 0)
    )


def tester_ressources(contexte: ContexteIncident) -> list[str]:
    return preuves_logs(
        contexte, MOTS_RESSOURCES, SEUIL_LOG_RESSOURCES, exclure_etats_sains=True
    )


def tester_reseau(contexte: ContexteIncident) -> list[str]:
    return preuves_logs(
        contexte, MOTS_RESEAU, SEUIL_LOG_RESEAU, exclure_etats_sains=True
    )


def tester_configuration(contexte: ContexteIncident) -> list[str]:
    return preuves_commits(contexte, MOTS_CONFIGURATION)