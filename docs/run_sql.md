# run_sql : contrat pour le backend Zetaris

Source unique : [run_sql.py](../src/rappel/interfaces/run_sql.py). Le module Enquete
n'envoie au backend que des requetes deja validees par `valider_sql` ; chaque
backend doit **revalider** lui-meme (defense en profondeur) en appelant la meme fonction.

## 1. Tables et colonnes (`TABLES`)

Tous les noms sont en minuscules, sans guillemets. Les types sont ceux de la
version simulee SQLite ; un equivalent Zetaris est suffisant tant que les noms
et la semantique sont identiques.

| Table                | Colonne    | Type SQL  | Contenu (un incident)                                        |
| -------------------- | ---------- | --------- | ------------------------------------------------------------ |
| `logs`               | `template` | `TEXT`    | Template de log (ex. `ERROR ...: column <*> ...`)            |
|                      | `compte`   | `INTEGER` | Nombre d'occurrences du template                             |
| `commits`            | `hash`     | `TEXT`    | Identifiant du commit                                        |
|                      | `message`  | `TEXT`    | Message du commit                                            |
|                      | `date`     | `TEXT`    | Date ISO 8601                                                |
| `colonnes_attendues` | `nom`      | `TEXT`    | Colonne du schema attendu                                    |
|                      | `type`     | `TEXT`    | Type declare, `?` s'il est absent                            |
| `colonnes_observees` | `nom`      | `TEXT`    | Colonne observee dans les donnees                            |
|                      | `type`     | `TEXT`    | Type observe, `?` s'il est absent                            |
| `metriques`          | `nom`      | `TEXT`    | Nom de la metrique                                           |
|                      | `valeur`   | `REAL`    | Valeur numerique                                             |

Les requetes ne contiennent **pas** d'`incident_id` : le backend doit exposer ces
tables (vues) limitees a l'incident en cours (`contexte.incident_id`), comme le fait
`construire_base(contexte)` en memoire. Les colonnes `nom:type` du contexte sont
decoupees au premier `:`, noms vides ignores, premiere occurrence gardee.

## 2. Interface a implementer

```python
class RunSqlZetaris:                     # src/rappel/interfaces/run_sql_zetaris.py
    mode = "zetaris"                     # affiche dans l'API et la demo

    def __init__(self, contexte: ContexteIncident) -> None: ...

    def executer(self, sql: str) -> ResultatSql: ...
```

`ResultatSql` (Pydantic, `extra="forbid"`) :

| Champ       | Sens                                                                         |
| ----------- | ---------------------------------------------------------------------------- |
| `requete`   | SQL **reellement execute** apres normalisation (celui de `valider_sql`)      |
| `colonnes`  | Noms des colonnes du resultat                                                |
| `lignes`    | `list[list]`, au plus 50 lignes, valeurs JSON (bytes -> hexadecimal)         |
| `nb_lignes` | `len(lignes)`                                                                |
| `tronque`   | `True` si des lignes ont ete (ou ont pu etre) omises, voir section 4         |
| `erreur`    | `None` si succes ; sinon message explicite, **sans exception levee**         |
| `tables`    | Tables de la liste blanche referencees (sert a compter les sources)          |

Une requete refusee ou en erreur renvoie un `ResultatSql` avec `erreur`, jamais
une exception. Prefixes utilises : `requete refusee : ...` (garde-fou, rien
execute) et `erreur SQL : ...` (execute mais echoue).

## 3. Garde-fous (`valider_sql(sql, tables_autorisees)`)

1. Commentaires (`--`, `/* */`) et chaines litterales retires avant analyse ;
   commentaire ou chaine non termine : refus.
2. Une seule instruction (`;` final tolere puis retire).
3. Uniquement `SELECT` ou `WITH ... SELECT`.
4. Mots-cles interdits n'importe ou : `INSERT UPDATE DELETE DROP ALTER CREATE ATTACH
   DETACH PRAGMA REPLACE TRUNCATE GRANT REVOKE VACUUM REINDEX ANALYZE MERGE UPSERT
   EXEC EXECUTE CALL COPY LOAD INTO SET COMMIT ROLLBACK BEGIN SAVEPOINT RELEASE LOCK
   UNLOAD IMPORT EXPORT LOAD_EXTENSION` (`replace()` est donc refuse aussi).
5. Tables apres `FROM`/`JOIN` (listes a virgule et sous-requetes comprises) limitees
   a `TABLES` plus les noms de CTE ; noms qualifies (`schema.table`), fonctions-tables
   (`pragma_table_info(...)`) et identifiants entre guillemets/crochets/backticks refuses.
6. `LIMIT 50` ajoute en fin de requete si aucun `LIMIT` de premier niveau.
7. 4000 caracteres maximum.

La version SQLite ajoute un autorisateur moteur (lecture seule, liste blanche),
`PRAGMA query_only = ON` et une interruption apres 2 s.

## 4. Lignes et troncature

`LIMITE_LIGNES = 50`. Le backend lit au plus 51 lignes : au-dela de 50,
`tronque=True` et seules 50 sont renvoyees. Si le `LIMIT 50` a ete ajoute par le
garde-fou et que 50 lignes reviennent, `tronque=True` (des lignes ont pu etre omises).

## 5. Selection du backend

`RUN_SQL_BACKEND` : vide ou `simule` -> `RunSqlSQLite` (mode `simule-sqlite`) ;
`zetaris` -> import de `RunSqlZetaris` ; tant que la classe n'existe pas, erreur
claire `RunSqlNonBranche` (« backend Zetaris non branche ») au demarrage de l'API
et a la creation. Toute autre valeur : `ValueError`.

## 6. Requetes types (dialecte utilise par le LLM faux)

```sql
SELECT a.nom, a.type FROM colonnes_attendues a
WHERE a.nom NOT IN (SELECT nom FROM colonnes_observees);
SELECT template, compte FROM logs WHERE compte > 5 ORDER BY compte DESC;
SELECT hash, message, date FROM commits ORDER BY date DESC;
SELECT nom, valeur FROM metriques ORDER BY nom;
```

Fonctions a supporter cote Zetaris : `lower()`, `LIKE`, `UNION ALL`, `NOT IN`
avec sous-requete, `JOIN ... ON`, `ORDER BY`, `count(*)`, CTE `WITH`.

## 7. Reste a faire cote Zetaris

- Ecrire `RunSqlZetaris` (vues par incident, client REST, delais, erreurs assainies,
  aucun jeton dans les messages ni le `repr`).
- Verifier le dialecte (types `TEXT/INTEGER/REAL`, `LIKE` sensible a la casse).
- Rejouer `tests/test_run_sql.py` en remplacant `RunSqlSQLite` par la classe Zetaris.
