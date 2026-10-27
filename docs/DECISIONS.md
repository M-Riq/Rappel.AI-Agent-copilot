# Décisions

Journal des décisions d'équipe. Une décision = date, contexte, choix, critère de retour.

## J4 (25 octobre 2026) — Go / repli pour la démo réelle

À prendre le 25 octobre à 18:00 UTC, après `python scripts/verifier_machine_demo.py --confirmer`
sur la machine de démo. Chaque service est décidé séparément ; un repli n'empêche pas les autres.

| Service | GO si… | Sinon : repli | Affichage du repli |
| --- | --- | --- | --- |
| Postgres (mémoire) | `check_postgres.py` REUSSI (SELECT 1, tables, aller-retour) | mémoire JSON (`MEMOIRE_PROVIDER=json`) ; le secours JSON automatique reste actif en cours de démo | `memoire_mode` : `json` ou `fallback-json` |
| Zetaris (preuves) | `SELECT 1 AS one` et requête sur deux sources REUSSI | base SQLite simulée (`SOURCE_PROVIDER=fichier`) | `preuves_simulees: true`, source `simule-sqlite` |
| Meterless Nightshift / Nemotron | `check_meterless.py` répond et A14 REUSSI (modèle contient « nemotron ») | LLM simulé (`LLM_PROVIDER=fake`) | `enquete_simulee: true`, badge « simulé » |

Règles :
- un repli est annoncé à l'oral et visible à l'écran ; jamais présenté comme réel ;
- après le gel (26 octobre), aucune nouvelle tentative d'intégration : seulement la configuration ;
- si Nightshift route vers un autre modèle que Nemotron : fixer la voie dans `.env`
  (`METERLESS_MODEL_RAPIDE` / `METERLESS_MODEL_PROFOND`), relancer A14 ; sinon repli.

| Service | Décision | Par | Heure (UTC) | Preuve |
| --- | --- | --- | --- | --- |
| Postgres | | | | |
| Zetaris | | | | |
| Meterless / Nemotron | | | | |

## Décisions déjà prises

| Date | Décision | Raison |
| --- | --- | --- |
| 23 oct. 2026 | Zetaris est appelé via son API REST (outil `run_sql`), pas via MCP | une requête par preuve, contrôlable et journalisée |
| 23 oct. 2026 | Mode JSON dans le texte par défaut ; appels d'outils natifs en option (`LLM_APPEL_OUTILS=1`) | compatibilité avec les modèles qui ne renvoient pas de `tool_calls` |
| 23 oct. 2026 | Action réelle limitée au pipeline de démo isolé, désactivée par défaut (`ACTION_REELLE_DEMO=0`) | aucune action irréversible pendant les essais |
| 23 oct. 2026 | Plafond de 3 enquêtes par session dans l'interface | limiter les appels au modèle pendant la démo |
| 23 oct. 2026 | Langfuse optionnel, désactivé sans clés | aucun paquet supplémentaire imposé |
| 24 oct. 2026 | Fusion de `naomy/complement-modules` dans `main` sans relecture de Juan (pipeline_demo, ingestion, action) ni d'Yvan (ui, Langfuse) ; relecture à faire après la fusion | 626 tests passent en mode simulé ; débloquer l'intégration Meterless. Décision : Riq |
| 25 oct. 2026 | Exception au gel pour H-MEM ; fusion seulement si tous les tests et la conformité sont verts | Intégration locale Python/SQLite ; aucun déploiement ni service cloud |
| 25 oct. 2026 | PostgreSQL conservé en backend optionnel | H-MEM remplace la mémoire pour les nouveaux essais, mais la migration Postgres n'est pas encore validée contre un vrai Postgres et ses références/tests restent actifs |
| 26 oct. 2026 | Zetaris : Riq reprend la responsabilité (au lieu de Juan) | réorganisation de l'équipe |
| 26 oct. 2026 | API REST directe non exposée sur le cluster AWS (404 HTML) ; Rappel passe par la passerelle UI `/api/proxy/sql-editor/sqls/run-query` (`ZETARIS_CHEMIN_SQL`) | testé avec preflight.py (échec) et query_zetaris.py (succès) |
| 26 oct. 2026 | `run_sql` de Rappel validé en réel : `SELECT 1 AS one` donne `[{"one": "1"}]` | scripts/check_zetaris.py |
| 27 oct. 2026 | Données Rappel dans Zetaris : option B (CSV dans WorkspaceS3Bucket, us-west-2). Source postgres laissée en l'état (Not exist), en attente de Juan | aucune nouvelle ressource AWS ; Juan indisponible | 
| 27 oct. 2026 | Pipeline de démo : orders_etl remplacé par transactions_etl (214 premières transactions synthétiques) ; P2 = amount en texte ("7.53 EUR") | même famille que P1 ; 934 tests verts |
| 27 oct. 2026 | CSV déposés dans WorkspaceS3Bucket : `rappel/transactions/` et `rappel/schema_contract/` (ETag = md5 local) | source de données Zetaris, aucune nouvelle ressource AWS |
| 27 oct. 2026 | Cluster sans rôle IAM S3 : utilisateur IAM `rappel-zetaris-lecture` (lecture seule sur `rappel/*`) ; clé dans OPTIONS des tables Zetaris, jamais dans Git | à supprimer après le hackathon |
| 27 oct. 2026 | `run_sql` : noms `schema.table` acceptés seulement s'ils sont en liste blanche ; `rappel.transactions` et `rappel.schema_contract` ajoutés au mode Zetaris | réel : `COUNT(*)` = 500 via RunSqlZetaris |
| 27 oct. 2026 | Mode zetaris : schéma attendu lu dans `rappel.schema_contract` (types traduits vers integer/decimal/text, timestamp → text) ; pas de repli silencieux sur la config locale | réel : P1 détecté (amount manquante, total nouvelle) ; mode fichier inchangé |
| 27 oct. 2026 | Protection de `main` retirée : fusion sans approbation obligatoire pendant le hackathon | relecteurs peu disponibles ; tests complets avant chaque fusion |