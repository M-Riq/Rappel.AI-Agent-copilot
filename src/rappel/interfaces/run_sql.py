from dataclasses import dataclass, field
import os
import re
import sqlite3
from time import monotonic
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from ..contrats import ContexteIncident


# Contrat de donnees partage avec le backend Zetaris : noms de tables, colonnes et types SQL.
TABLES: dict[str, dict[str, str]] = {
    "logs": {"template": "TEXT", "compte": "INTEGER"},
    "commits": {"hash": "TEXT", "message": "TEXT", "date": "TEXT"},
    "colonnes_attendues": {"nom": "TEXT", "type": "TEXT"},
    "colonnes_observees": {"nom": "TEXT", "type": "TEXT"},
    "metriques": {"nom": "TEXT", "valeur": "REAL"},
}
LIMITE_LIGNES = 50
LONGUEUR_MAX_SQL = 4000
DELAI_MAX_EXECUTION_S = 2.0
TYPE_INCONNU = "?"
MOTS_INTERDITS = frozenset({
    "insert", "update", "delete", "drop", "alter", "create", "attach", "detach", "pragma",
    "replace", "truncate", "grant", "revoke", "vacuum", "reindex", "analyze", "merge", "upsert",
    "exec", "execute", "call", "copy", "load", "into", "set", "commit", "rollback", "begin",
    "savepoint", "release", "lock", "unload", "import", "export", "load_extension",
})
MOTS_APRES_TABLE = frozenset({
    "where", "join", "inner", "left", "right", "full", "outer", "cross", "natural", "on",
    "using", "group", "order", "limit", "union", "except", "intersect", "having", "window",
    "offset", "as",
})
BACKENDS = ("simule", "zetaris")


class ResultatSql(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requete: str
    colonnes: list[str]
    lignes: list[list[Any]]
    nb_lignes: int
    tronque: bool
    erreur: str | None = None
    tables: list[str] = []


class RunSql(Protocol):
    mode: str

    def executer(self, sql: str) -> ResultatSql: ...


class RunSqlNonBranche(RuntimeError):
    """Backend run_sql demande mais pas encore disponible."""


@dataclass
class ValidationSql:
    requete: str
    erreur: str | None = None
    tables: list[str] = field(default_factory=list)
    # LIMIT ajoute par le garde-fou : s'il est atteint, des lignes ont pu etre omises.
    limite_ajoutee: bool = False


class _RequeteRefusee(ValueError):
    pass


def _retirer_commentaires_et_chaines(sql: str) -> tuple[str, str]:
    """Retourne (requete normalisee, squelette sans chaines ni commentaires)."""
    normalise: list[str] = []
    squelette: list[str] = []

    def ajouter(texte: str, masque: str | None = None) -> None:
        normalise.append(texte)
        squelette.append(texte if masque is None else masque)

    def espace() -> None:
        if normalise and normalise[-1] != " ":
            ajouter(" ")

    indice, taille = 0, len(sql)
    while indice < taille:
        caractere = sql[indice]
        if sql.startswith("--", indice):
            fin = sql.find("\n", indice)
            indice = taille if fin == -1 else fin
            espace()
        elif sql.startswith("/*", indice):
            fin = sql.find("*/", indice + 2)
            if fin == -1:
                raise _RequeteRefusee("commentaire non termine")
            indice = fin + 2
            espace()
        elif caractere == "'":
            fin = indice + 1
            while True:
                fin = sql.find("'", fin)
                if fin == -1:
                    raise _RequeteRefusee("chaine litterale non terminee")
                if sql.startswith("''", fin):
                    fin += 2
                    continue
                break
            ajouter(sql[indice:fin + 1], "''")
            indice = fin + 1
        elif caractere in "\"`[]$\\":
            raise _RequeteRefusee("identifiants entre guillemets et caracteres speciaux interdits")
        elif caractere.isspace():
            espace()
            indice += 1
        else:
            ajouter(caractere)
            indice += 1
    return "".join(normalise).strip(), "".join(squelette).strip()


def _jetons(squelette: str) -> list[str]:
    return [jeton.lower() for jeton in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d*)?|''|\S", squelette)]


def _est_identifiant(jeton: str) -> bool:
    return bool(re.fullmatch(r"[a-z_][a-z0-9_]*", jeton))


def _fin_parenthese(jetons: list[str], ouverture: int) -> int:
    profondeur = 0
    for indice in range(ouverture, len(jetons)):
        if jetons[indice] == "(":
            profondeur += 1
        elif jetons[indice] == ")":
            profondeur -= 1
            if profondeur == 0:
                return indice
    raise _RequeteRefusee("parentheses non equilibrees")


def _noms_cte(jetons: list[str]) -> set[str]:
    if not jetons or jetons[0] != "with":
        return set()
    noms = set()
    indice = 2 if len(jetons) > 1 and jetons[1] == "recursive" else 1
    while indice < len(jetons):
        nom = jetons[indice]
        if not _est_identifiant(nom):
            raise _RequeteRefusee("clause WITH invalide")
        indice += 1
        if indice < len(jetons) and jetons[indice] == "(":
            indice = _fin_parenthese(jetons, indice) + 1
        if indice + 1 >= len(jetons) or jetons[indice] != "as" or jetons[indice + 1] != "(":
            raise _RequeteRefusee("clause WITH invalide")
        noms.add(nom)
        indice = _fin_parenthese(jetons, indice + 1) + 1
        if indice < len(jetons) and jetons[indice] == ",":
            indice += 1
            continue
        break
    return noms


def _tables_referencees(jetons: list[str]) -> set[str]:
    tables = set()
    for position, jeton in enumerate(jetons):
        if jeton not in {"from", "join"}:
            continue
        indice = position + 1
        while indice < len(jetons):
            courant = jetons[indice]
            if courant == "(":
                indice = _fin_parenthese(jetons, indice) + 1
            elif _est_identifiant(courant):
                if indice + 1 < len(jetons) and jetons[indice + 1] == ".":
                    raise _RequeteRefusee("noms de tables qualifies interdits")
                tables.add(courant)
                indice += 1
                if indice < len(jetons) and jetons[indice] == "(":
                    indice = _fin_parenthese(jetons, indice) + 1
            else:
                break
            if indice < len(jetons) and jetons[indice] == "as":
                indice += 2
            elif indice < len(jetons) and _est_identifiant(jetons[indice]) and jetons[indice] not in MOTS_APRES_TABLE:
                indice += 1
            if indice < len(jetons) and jetons[indice] == ",":
                indice += 1
                continue
            break
    return tables


def _limite_au_premier_niveau(jetons: list[str]) -> bool:
    profondeur = 0
    for jeton in jetons:
        if jeton == "(":
            profondeur += 1
        elif jeton == ")":
            profondeur -= 1
        elif jeton == "limit" and profondeur == 0:
            return True
    return False


def valider_sql(sql: str, tables_autorisees) -> ValidationSql:
    """Garde-fous communs a tous les backends : lecture seule, une instruction, liste blanche."""
    soumis = sql.strip() if isinstance(sql, str) else ""
    try:
        if not soumis:
            raise _RequeteRefusee("requete vide")
        if len(soumis) > LONGUEUR_MAX_SQL:
            raise _RequeteRefusee(f"requete trop longue (plus de {LONGUEUR_MAX_SQL} caracteres)")
        normalise, squelette = _retirer_commentaires_et_chaines(soumis)
        while squelette.endswith(";"):
            squelette = squelette[:-1].rstrip()
            normalise = normalise.rstrip()[:-1].rstrip()
        if ";" in squelette:
            raise _RequeteRefusee("une seule instruction autorisee")
        jetons = _jetons(squelette)
        if not jetons:
            raise _RequeteRefusee("requete vide")
        interdits = sorted(set(jetons) & MOTS_INTERDITS)
        if interdits:
            raise _RequeteRefusee("mot-cle interdit : " + ", ".join(mot.upper() for mot in interdits))
        if jetons[0] not in {"select", "with"} or "select" not in jetons:
            raise _RequeteRefusee("seules les requetes SELECT ou WITH ... SELECT sont autorisees")
        autorisees = {nom.lower() for nom in tables_autorisees}
        tables = _tables_referencees(jetons)
        hors_liste = sorted(tables - autorisees - _noms_cte(jetons))
        if hors_liste:
            raise _RequeteRefusee("table hors liste blanche : " + ", ".join(hors_liste))
        limite_ajoutee = not _limite_au_premier_niveau(jetons)
        if limite_ajoutee:
            normalise = f"{normalise} LIMIT {LIMITE_LIGNES}"
    except _RequeteRefusee as erreur:
        return ValidationSql(requete=soumis, erreur=f"requete refusee : {erreur}")
    return ValidationSql(requete=normalise, tables=sorted(tables & autorisees), limite_ajoutee=limite_ajoutee)


def resultat_refuse(validation: ValidationSql) -> ResultatSql:
    return ResultatSql(requete=validation.requete, colonnes=[], lignes=[], nb_lignes=0,
                       tronque=False, erreur=validation.erreur)


def lire_colonnes(schema: list[str]) -> list[tuple[str, str]]:
    colonnes: dict[str, str] = {}
    for colonne in schema:
        nom, _, type_colonne = colonne.partition(":")
        nom = nom.strip()
        if nom and nom not in colonnes:
            colonnes[nom] = type_colonne.strip() or TYPE_INCONNU
    return list(colonnes.items())


def construire_base(contexte: ContexteIncident | dict) -> sqlite3.Connection:
    """Base SQLite en memoire alimentee uniquement par le contexte d'incident."""
    contexte = ContexteIncident.model_validate(
        contexte.model_dump() if isinstance(contexte, ContexteIncident) else contexte
    )
    connexion = sqlite3.connect(":memory:", check_same_thread=False)
    # DDL construit uniquement depuis la constante TABLES, jamais depuis des donnees.
    for table, colonnes in TABLES.items():
        definition = ", ".join(f"{nom} {type_sql}" for nom, type_sql in colonnes.items())
        connexion.execute(f"CREATE TABLE {table} ({definition})")
    donnees = {
        "logs": [(log.template, log.compte) for log in contexte.logs],
        "commits": [(commit.hash, commit.message, commit.date) for commit in contexte.commits_recents],
        "colonnes_attendues": lire_colonnes(contexte.schema.attendu),
        "colonnes_observees": lire_colonnes(contexte.schema.observe),
        "metriques": list(contexte.metriques.items()),
    }
    for table, lignes in donnees.items():
        marqueurs = ", ".join("?" for _ in TABLES[table])
        connexion.executemany(f"INSERT INTO {table} VALUES ({marqueurs})", lignes)
    connexion.commit()
    return connexion


def _valeur_json(valeur: Any) -> Any:
    return valeur.hex() if isinstance(valeur, bytes) else valeur


class RunSqlSQLite:
    """Simulation hors reseau de run_sql : SQLite en memoire construit depuis le contexte."""

    mode = "simule-sqlite"

    def __init__(self, contexte: ContexteIncident | dict) -> None:
        self.tables_autorisees = tuple(TABLES)
        self._connexion = construire_base(contexte)
        self._echeance = 0.0
        self._connexion.execute("PRAGMA query_only = ON")
        self._connexion.set_authorizer(self._autoriser)
        self._connexion.set_progress_handler(self._interrompre, 1000)

    def __repr__(self) -> str:
        return f"RunSqlSQLite(mode={self.mode!r})"

    def _interrompre(self) -> int:
        return int(monotonic() > self._echeance)

    def _autoriser(self, action, argument1, argument2, base, declencheur) -> int:
        # Defense en profondeur : meme apres valider_sql, le moteur refuse toute ecriture.
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if (argument1 or "").lower() in self.tables_autorisees else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if (argument2 or "").lower() in MOTS_INTERDITS else sqlite3.SQLITE_OK
        if action == getattr(sqlite3, "SQLITE_RECURSIVE", 33):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def executer(self, sql: str) -> ResultatSql:
        validation = valider_sql(sql, self.tables_autorisees)
        if validation.erreur is not None:
            return resultat_refuse(validation)
        self._echeance = monotonic() + DELAI_MAX_EXECUTION_S
        try:
            curseur = self._connexion.execute(validation.requete)
            lignes = curseur.fetchmany(LIMITE_LIGNES + 1)
            colonnes = [description[0] for description in curseur.description or []]
            curseur.close()
        except sqlite3.Error as erreur:
            return ResultatSql(requete=validation.requete, colonnes=[], lignes=[], nb_lignes=0,
                               tronque=False, erreur=f"erreur SQL : {erreur}", tables=validation.tables)
        tronque = len(lignes) > LIMITE_LIGNES or (validation.limite_ajoutee and len(lignes) == LIMITE_LIGNES)
        lignes = [[_valeur_json(valeur) for valeur in ligne] for ligne in lignes[:LIMITE_LIGNES]]
        return ResultatSql(requete=validation.requete, colonnes=colonnes, lignes=lignes,
                           nb_lignes=len(lignes), tronque=tronque, tables=validation.tables)

    def fermer(self) -> None:
        self._connexion.close()


def creer_run_sql(contexte: ContexteIncident | dict) -> RunSql:
    backend = os.getenv("RUN_SQL_BACKEND", "").strip().lower() or "simule"
    if backend == "simule":
        return RunSqlSQLite(contexte)
    if backend == "zetaris":
        try:
            from .run_sql_zetaris import RunSqlZetaris  # type: ignore[import-not-found]
        except ImportError:
            raise RunSqlNonBranche(
                "RUN_SQL_BACKEND=zetaris : backend Zetaris non branche "
                "(classe RunSqlZetaris absente de src/rappel/interfaces/run_sql_zetaris.py)"
            ) from None
        return RunSqlZetaris(contexte)
    raise ValueError(f"RUN_SQL_BACKEND inconnu : attendu {' ou '.join(BACKENDS)}")


def verifier_backend() -> str:
    """Echoue tot (au demarrage) si le backend demande n'est pas utilisable."""
    backend = os.getenv("RUN_SQL_BACKEND", "").strip().lower() or "simule"
    if backend not in BACKENDS:
        raise ValueError(f"RUN_SQL_BACKEND inconnu : attendu {' ou '.join(BACKENDS)}")
    if backend == "zetaris":
        try:
            from .run_sql_zetaris import RunSqlZetaris  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            raise RunSqlNonBranche(
                "RUN_SQL_BACKEND=zetaris : backend Zetaris non branche "
                "(classe RunSqlZetaris absente de src/rappel/interfaces/run_sql_zetaris.py)"
            ) from None
    return backend
