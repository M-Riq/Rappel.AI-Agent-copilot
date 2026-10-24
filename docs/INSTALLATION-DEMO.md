# Installation de la machine de démonstration

But : faire tourner Rappel en mode **réel** (Meterless Nightshift, Zetaris via API REST,
Postgres) sur la machine de la démo. Le mode simulé ne demande rien de tout cela (voir
`docs/PROPOSITION-README.md`, « Démarrage rapide »).

## 1. Vérifier la machine (aucune modification)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\preparer_machine_demo.ps1
```

Contrôles : RAM ≥ 16 Go (tolérance 0,5 Go pour la mémoire réservée), disque ≥ 15 Go libres,
Docker avec démon actif, Python 3.11 ou 3.12 (option `-Python C:\chemin\python.exe`), git
(`C:\Program Files\Git\cmd` dans le PATH). Code de sortie 0 si aucun ECHOUE.

## 2. Préparer (installe, sans écrire de clé)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\preparer_machine_demo.ps1 -Preparer
```

Crée `.venv`, installe `requirements-demo.txt` (dont psycopg), copie `.env.example` vers `.env`
s'il n'existe pas et lance `docker compose up -d postgres` (tables de `sql/init.sql`).

## 3. Renseigner `.env` à la main (jamais dans git)

| Service | Variables |
| --- | --- |
| Meterless Nightshift | `LLM_PROVIDER=meterless`, `METERLESS_BASE_URL`, `METERLESS_RUNTIME_TOKEN`, `METERLESS_MODEL_RAPIDE`, `METERLESS_MODEL_PROFOND` (valeurs de la console) |
| Zetaris (API REST) | `SOURCE_PROVIDER=zetaris`, `ZETARIS_BASE_URL`, `ZETARIS_API_KEY`, `ZETARIS_ORG_ID` ; tables alignées sur `docs/run_sql.md` |
| Postgres | `MEMOIRE_PROVIDER=postgres`, `DATABASE_URL` (secours JSON automatique si injoignable) |
| Action sur le pipeline de démo | `ACTION_REELLE_DEMO=1` seulement pendant la démo |

## 4. Contrôler les services réels

```powershell
python scripts\verifier_machine_demo.py              # local : services NON_MESURE
python scripts\verifier_machine_demo.py --confirmer  # appels réels
```

Avec `--confirmer` : Zetaris `SELECT 1 AS one`, requête sur deux sources
(`pipeline.commits` et `pipeline.logs` : commit `a1b2c3` et 214 `KeyError`), Meterless
(`check_meterless.py` : modèles et modèle effectif) et Postgres (`check_postgres.py` : SELECT 1,
tables, aller-retour annulé). Résultat dans `data/verification_machine.json`. Aucune clé ni mot
de passe n'est affiché (valeurs masquées).

## 5. Recettes en réel

```powershell
python scripts\recette_module_a.py --confirmer
python scripts\recette_globale.py --confirmer
python scripts\collecter_preuves_sponsors.py --confirmer
```

## Repli

Si un service réel manque le jour J, revenir au mode simulé (`LLM_PROVIDER=fake`,
`SOURCE_PROVIDER=fichier`, `MEMOIRE_PROVIDER=json`) : l'interface et l'API affichent alors
« simulé ». Voir `docs/DECISIONS.md`.
