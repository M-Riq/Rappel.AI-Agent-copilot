import unicodedata

from ..contrats import ContexteIncident


SEUIL_LOG_RESSOURCES = 5
SEUIL_LOG_RESEAU = 5


def normaliser(texte: str) -> str:
    return "".join(
        caractere for caractere in unicodedata.normalize("NFD", texte.lower())
        if not unicodedata.combining(caractere)
    )


def colonnes(schema: list[str]) -> dict[str, str]:
    resultat = {}
    for colonne in schema:
        nom, separateur, type_colonne = colonne.partition(":")
        if not separateur or not nom or not type_colonne or nom in resultat:
            raise ValueError("Chaque colonne doit avoir un nom unique au format nom:type")
        resultat[nom] = type_colonne
    return resultat


def ecarts_schema(contexte: ContexteIncident) -> list[str]:
    attendu = colonnes(contexte.schema.attendu)
    observe = colonnes(contexte.schema.observe)
    preuves = []
    for nom, type_colonne in attendu.items():
        if nom not in observe:
            preuves.append(f"schema: colonne manquante {nom}:{type_colonne}")
        elif observe[nom] != type_colonne:
            preuves.append(
                f"schema: type different pour {nom}: attendu {type_colonne}, observe {observe[nom]}"
            )
    for nom, type_colonne in observe.items():
        if nom not in attendu:
            preuves.append(f"schema: nouvelle colonne {nom}:{type_colonne}")
    return preuves


def preuves_logs(contexte: ContexteIncident, mots: tuple[str, ...], seuil: int) -> list[str]:
    return [
        f"logs: {log.template} (compte={log.compte})"
        for log in contexte.logs
        if log.compte > seuil and any(mot in normaliser(log.template) for mot in mots)
    ]


def preuves_commits(contexte: ContexteIncident, mots: tuple[str, ...]) -> list[str]:
    return [
        f"commits: {commit.hash}: {commit.message} ({commit.date})"
        for commit in contexte.commits_recents
        if any(mot in normaliser(commit.message) for mot in mots)
    ]


def tester_schema(contexte: ContexteIncident) -> list[str]:
    return (
        ecarts_schema(contexte)
        + preuves_commits(contexte, ("rename", "renomm", "type", "column", "colonne", "schema"))
        + preuves_logs(contexte, ("column", "colonne", "type", "keyerror"), 0)
    )


def tester_ressources(contexte: ContexteIncident) -> list[str]:
    return preuves_logs(
        contexte, ("memory", "memoire", "disk", "disque", "oom"), SEUIL_LOG_RESSOURCES
    )


def tester_reseau(contexte: ContexteIncident) -> list[str]:
    return preuves_logs(
        contexte, ("timeout", "connection", "connexion", "network", "reseau"), SEUIL_LOG_RESEAU
    )


def tester_configuration(contexte: ContexteIncident) -> list[str]:
    return preuves_commits(contexte, ("config", "configuration", "parametre", "parameter"))