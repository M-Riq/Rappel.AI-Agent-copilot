"""Preuves SQL du moteur : garde-fous communs, base simulée (fichier) et adaptateur Zetaris."""

from dataclasses import dataclass, field
import re
import sqlite3
from time import monotonic
from typing import Any

from pydantic import BaseModel, ConfigDict

from rappel.config import Configuration, charger
from rappel.enquete.modeles import ContexteIncident
from rappel.enquete.protocoles import RunSql
from rappel.interfaces import tools_zetaris


# Contrat de donnees partage avec le backend Zetaris : noms de tables, colonnes et types SQL.
TABLES: dict[str, dict[str, str]] = {
    "logs": {"template": "TEXT", "compte": "INTEGER"},
    "commits": {"hash": "TEXT", "message": "TEXT", "date": "TEXT"},
    "colonnes_attendues": {"nom": "TEXT", "type": "TEXT"},
    "colonnes_observees": {"nom": "TEXT", "type": "TEXT"},
    "metriques": {"nom": "TEXT", "valeur": "REAL"},
}
# Tables de données exposées par Zetaris (base Lightning « rappel », fichiers S3) : mode réel uniquement.
TABLES_DONNEES_ZETARIS: tuple[str, ...] = ("rappel.transactions", "rappel.schema_contract")

LIMITE_LIGNES = 50
LONGUEUR_MAX_SQL = 4000
DELAI_MAX_EXECUTION_S = 2.0
TYPE_INCONNU = "?"
MOTS_INTERDITS = frozenset({
    "insert", "update", "delete", "drop", "alter", "create", "attach", "detach", "pragma",
    "replace", "truncate", "grant", "revoke", "vacuum", "reindex", "analyze", "merge", "upsert",
    "exec", "execute", "call", "copy", "load", "into", "set", "commit", "rollback", "begin",
    "savepoint", "release", "lock", "unload", "import", "export", "load_extension",
    # Fonctions dangereuses selon les moteurs (lecture de fichiers, réseau, attente, commandes).
    "sleep", "pg_sleep", "benchmark", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "lo_import",
    "lo_export", "dblink", "load_file", "readfile", "writefile", "outfile", "dumpfile", "xp_cmdshell",
    "openrowset", "opendatasource", "openquery", "fts3_tokenizer", "edit",
})
MOTS_APRES_TABLE = frozenset({
    "where", "join", "inner", "left", "right", "full", "outer", "cross", "natural", "on",
    "using", "group", "order", "limit", "union", "except", "intersect", "having", "window",
    "offset", "as",
})


class ResultatSql(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requete: str
    colonnes: list[str]
    lignes: list[list[Any]]
    nb_lignes: int
    tronque: bool
    erreur: str | None = None
    tables: list[str] = []


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
        if ord(caractere) > 127 or (ord(caractere) < 32 and caractere not in "\t\n\r"):
            # Hors chaînes littérales : ASCII imprimable seulement (homoglyphes, espaces et
            # points-virgules Unicode, octets nuls et caractères de contrôle refusés).
            raise _RequeteRefusee("caractere non ASCII ou de controle hors chaine litterale")
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
                nom = courant
                if indice + 1 < len(jetons) and jetons[indice + 1] == ".":
                    # schema.table seulement (deux niveaux) ; la liste blanche tranche ensuite.
                    if (indice + 2 >= len(jetons) or not _est_identifiant(jetons[indice + 2])
                            or (indice + 3 < len(jetons) and jetons[indice + 3] == ".")):
                        raise _RequeteRefusee("noms de tables qualifies interdits")
                    nom = f"{courant}.{jetons[indice + 2]}"
                    indice += 2
                tables.add(nom)
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


def retirer_bloc_markdown(sql: str) -> str:
    """Retire un bloc ```sql ... ``` qu'un modele place souvent autour de sa requete."""
    texte = sql.strip()
    bloc = re.fullmatch(r"```[A-Za-z]*\s*(.*?)\s*```", texte, re.DOTALL)
    return bloc.group(1).strip() if bloc else texte


# Constructions propres a SQLite (ou hors norme ISO) et equivalent standard, pour un dialecte
# Zetaris encore inconnu. LIMIT est garde (ajoute par le garde-fou) mais reste a confirmer.
CONSTRUCTIONS_NON_PORTABLES = (
    ("IFNULL", r"\bifnull\s*\(", "COALESCE(x, y)"),
    ("GROUP_CONCAT", r"\bgroup_concat\s*\(", "STRING_AGG / LISTAGG selon le moteur (a eviter)"),
    ("STRFTIME", r"\bstrftime\s*\(", "EXTRACT(... FROM x) ou CAST"),
    ("JULIANDAY", r"\bjulianday\s*\(", "differences de dates via CAST / INTERVAL"),
    ("DATETIME()", r"\bdatetime\s*\(", "CAST(x AS TIMESTAMP)"),
    ("DATE()", r"\bdate\s*\(", "CAST(x AS DATE)"),
    ("SUBSTR", r"\bsubstr\s*\(", "SUBSTRING(x FROM debut FOR longueur)"),
    ("INSTR", r"\binstr\s*\(", "POSITION(motif IN x)"),
    ("PRINTF", r"\bprintf\s*\(", "concatenation || et CAST"),
    ("TYPEOF", r"\btypeof\s*\(", "aucun equivalent : ne pas utiliser"),
    ("GLOB", r"\bglob\b", "LIKE"),
    ("RANDOM()", r"\brandom\s*\(", "aucun equivalent portable : ne pas utiliser"),
    ("ROWID", r"\browid\b", "cle explicite de la table"),
    ("SQLITE_*", r"\bsqlite_\w+", "aucun equivalent : ne pas utiliser"),
    ("!=", r"!=", "<>"),
    ("LIMIT", r"\blimit\b", None),
)


def lister_constructions_non_portables(sql: str) -> list[dict]:
    """Constructions propres a SQLite hors chaines et commentaires ; remplacement None = a confirmer."""
    try:
        _, squelette = _retirer_commentaires_et_chaines(retirer_bloc_markdown(sql))
    except _RequeteRefusee:
        squelette = sql
    return [
        {"construction": nom, "remplacement": remplacement}
        for nom, motif, remplacement in CONSTRUCTIONS_NON_PORTABLES
        if re.search(motif, squelette, re.IGNORECASE)
    ]


def valider_sql(sql: str, tables_autorisees) -> ValidationSql:
    """Garde-fous communs a tous les backends : lecture seule, une instruction, liste blanche."""
    soumis = retirer_bloc_markdown(sql) if isinstance(sql, str) else ""
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
        # Nom qualifie (schema.table) hors liste blanche : meme refus qu'avant.
        if any("." in nom for nom in tables - autorisees):
            raise _RequeteRefusee("noms de tables qualifies interdits")
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


def _lignes_tabulaires(lignes: list) -> tuple[list[str], list[list[Any]]]:
    colonnes: list[str] = []
    for ligne in lignes:
        for nom in (ligne if isinstance(ligne, dict) else {}):
            if nom not in colonnes:
                colonnes.append(nom)
    tableau = [
        [_valeur_json(ligne.get(nom)) for nom in colonnes] if isinstance(ligne, dict) else list(ligne)
        for ligne in lignes
    ]
    return colonnes, tableau


class RunSqlZetaris:
    """Preuves SQL via Zetaris REST (tools_zetaris.run_sql), memes garde-fous que la simulation.

    Les tables de TABLES doivent etre exposees par Zetaris (vues limitees a l'incident).
    """

    mode = "zetaris"

    def __init__(self, contexte: ContexteIncident | dict, configuration: Configuration | None = None) -> None:
        self.contexte = contexte
        self.configuration = configuration or charger()
        self.tables_autorisees = tuple(TABLES) + TABLES_DONNEES_ZETARIS

    def __repr__(self) -> str:
        return f"RunSqlZetaris(mode={self.mode!r})"

    @staticmethod
    def _erreur(requete, erreur, validation, prefixe="") -> ResultatSql:
        message = str(erreur)
        if any(mot in message.lower() for mot in ("bearer", "authorization", "x-org-id", "api_key")):
            message = "Zetaris indisponible"
        return ResultatSql(requete=requete, colonnes=[], lignes=[], nb_lignes=0, tronque=False,
                           erreur=f"erreur SQL : {prefixe}{message}", tables=validation.tables)

    def executer(self, sql: str) -> ResultatSql:
        validation = valider_sql(sql, self.tables_autorisees)
        if validation.erreur is not None:
            return resultat_refuse(validation)
        requete = validation.requete
        try:
            reponse = tools_zetaris.run_sql(requete, configuration=self.configuration)
        except (ValueError, RuntimeError) as erreur:
            reformulee = reformuler_dialecte(requete, erreur, self.tables_autorisees)
            if reformulee is None:
                return self._erreur(requete, erreur, validation)
            # Une seule reformulation guidée par le message (assaini) du moteur, puis abandon propre.
            requete = reformulee
            try:
                reponse = tools_zetaris.run_sql(requete, configuration=self.configuration)
            except (ValueError, RuntimeError) as seconde:
                return self._erreur(requete, seconde, validation, "apres une reformulation : ")
        colonnes, lignes = _lignes_tabulaires(reponse.get("lignes", []))
        tronque = len(lignes) > LIMITE_LIGNES or (validation.limite_ajoutee and len(lignes) >= LIMITE_LIGNES)
        lignes = lignes[:LIMITE_LIGNES]
        # requete : le SQL réellement exécuté (éventuellement reformulé une fois).
        return ResultatSql(requete=requete, colonnes=colonnes, lignes=lignes,
                           nb_lignes=len(lignes), tronque=tronque, tables=validation.tables)


# Réécritures standard, appliquées seulement si le message du moteur désigne la construction.
REECRITURES_DIALECTE = (
    ("limit", re.compile(r"\s+LIMIT\s+(\d+)\s*$", re.IGNORECASE), r" FETCH FIRST \1 ROWS ONLY"),
    ("ifnull", re.compile(r"\bIFNULL\s*\(", re.IGNORECASE), "COALESCE("),
    ("!=", re.compile(r"!="), "<>"),
    ("substr", re.compile(r"\bSUBSTR\s*\(", re.IGNORECASE), "SUBSTRING("),
)


def reformuler_dialecte(requete: str, erreur: Exception, tables_autorisees) -> str | None:
    """Requête réécrite en SQL standard si l'erreur est un refus de dialecte qui cite la construction.

    La requête réécrite repasse les garde-fous ; None si aucune réécriture ne s'applique.
    """
    if getattr(erreur, "categorie", None) != "syntaxe_sql":
        return None
    message = str(getattr(erreur, "detail", None) or erreur).lower()
    reecrite = requete
    for mot, motif, remplacement in REECRITURES_DIALECTE:
        if mot in message:
            reecrite = motif.sub(remplacement, reecrite)
    if reecrite == requete or valider_sql(reecrite, tables_autorisees).erreur is not None:
        return None
    return reecrite


def creer_run_sql(contexte: ContexteIncident | dict, configuration: Configuration | None = None) -> RunSql:
    """Backend des preuves selon SOURCE_PROVIDER : fichier (SQLite simule) ou zetaris."""
    configuration = configuration or charger()
    if configuration.source_provider == "zetaris":
        return RunSqlZetaris(contexte, configuration)
    return RunSqlSQLite(contexte)
