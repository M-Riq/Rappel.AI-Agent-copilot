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
