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

## 2. Interface et adaptateur Zetaris

```python
class RunSqlZetaris:                     # src/rappel/interfaces/run_sql.py
    mode = "zetaris"                     # affiche dans les evenements et la demo

    def __init__(self, contexte, configuration=None) -> None: ...

    def executer(self, sql: str) -> ResultatSql: ...
```

`RunSqlZetaris` applique `valider_sql`, puis appelle `tools_zetaris.run_sql` du squelette
(une instruction, `X-Request-ID` neuf, Authorization Bearer, `X-Org-ID`) et convertit
les lignes (dictionnaires) en `colonnes` / `lignes`.

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
execute) et `erreur SQL : ...` (execute mais echoue ; message sans element
d'authentification).

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

`SOURCE_PROVIDER` (configuration unique `rappel.config.Configuration`) :
`fichier` -> `RunSqlSQLite` (mode `simule-sqlite`, base construite depuis le contexte) ;
`zetaris` -> `RunSqlZetaris`.

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

- Exposer les tables de `TABLES` (vues limitees a l'incident) et confirmer le corps REST
  (`CLE_SQL_CORPS` dans `tools_zetaris.py`).
- Verifier le dialecte (types `TEXT/INTEGER/REAL`, `LIKE` sensible a la casse).
- Rejouer `tests/test_run_sql.py` contre une instance reelle.

## 8. Portabilite SQL (dialecte Zetaris inconnu)

`lister_constructions_non_portables(sql)` ([run_sql.py](../src/rappel/interfaces/run_sql.py))
signale, hors chaines et commentaires, les constructions propres a SQLite ou hors norme ISO,
avec leur equivalent standard :

| Construction | Remplacement standard |
| --- | --- |
| `IFNULL` | COALESCE(x, y) |
| `GROUP_CONCAT` | STRING_AGG / LISTAGG selon le moteur (a eviter) |
| `STRFTIME` | EXTRACT(... FROM x) ou CAST |
| `JULIANDAY` | differences de dates via CAST / INTERVAL |
| `DATETIME()` | CAST(x AS TIMESTAMP) |
| `DATE()` | CAST(x AS DATE) |
| `SUBSTR` | SUBSTRING(x FROM debut FOR longueur) |
| `INSTR` | POSITION(motif IN x) |
| `PRINTF` | concatenation || et CAST |
| `TYPEOF` | aucun equivalent : ne pas utiliser |
| `GLOB` | LIKE |
| `RANDOM()` | aucun equivalent portable : ne pas utiliser |
| `ROWID` | cle explicite de la table |
| `SQLITE_*` | aucun equivalent : ne pas utiliser |
| `!=` | <> |
| `LIMIT` | garde : a confirmer avec Zetaris |

Controle des requetes du depot (test `test_requetes_types_et_du_faux_portables`) :

| Requete | Constructions non portables |
| --- | --- |
| requete type « ecart de schema » | aucune |
| requete type « logs au compte eleve » | aucune |
| requete type « commits recents » | aucune |
| requete type « metriques » | aucune |
| LLM faux : Le schema des donnees a change | aucune |
| LLM faux : Saturation des ressources | aucune |
| LLM faux : Probleme reseau ou timeout | aucune |
| LLM faux : Changement de configuration | aucune |

Aucune construction propre a SQLite n'est utilisee : rien n'a du etre remplace. Elles
n'emploient que `SELECT`, `WHERE`, `JOIN ... ON`, `UNION ALL`, `NOT IN (sous-requete)`, `LIKE`,
`LOWER`, `<>`, `ORDER BY` et `NULL AS colonne`. Restent a confirmer avec Zetaris :
`LIMIT` (ajoute par le garde-fou ; la norme ISO ecrit `FETCH FIRST n ROWS ONLY`), les noms de
colonnes `date` et `type` (mots reserves dans certains moteurs : a citer ou renommer cote vues
Zetaris), le typage de `NULL AS colonne` dans un `UNION ALL` et la sensibilite de `LIKE` a la casse.

Un bloc Markdown ```sql ... ``` autour d'une requete du modele est retire avant validation ;
le SQL execute (et renvoye dans `requete_sql`) est la requete nettoyee.
