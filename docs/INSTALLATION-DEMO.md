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

## 3. Détecter les services et proposer `.env` (aucun secret affiché)

```powershell
python scripts\detecter_nightshift.py --fichier-jeton <chemin du proxy-token>
python scripts\decouvrir_zetaris.py
python scripts\preparer_env.py --fichier-jeton <chemin du proxy-token> --simuler
python scripts\preparer_env.py --fichier-jeton <chemin du proxy-token>
```

`detecter_nightshift.py` sonde `GET /v1/models` (127.0.0.1, localhost, [::1] ; ports 8088, 8090,
8787, 11434) et classe « en ligne », « non autorisé » ou « hors ligne ». `decouvrir_zetaris.py` lit
l'OpenAPI publié (endpoint et clé du SQL), teste `SELECT 1 AS one`, les tables et le dialecte.
`preparer_env.py` n'écrase jamais un `.env` existant sans `--forcer`.

## 3 bis. Ou renseigner `.env` à la main (jamais dans git)

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

Avec `--confirmer` : Zetaris `SELECT 1 AS one` et sondes `SELECT 1 ... LIMIT 1`
sur les cinq tables Rappel, Meterless (`check_meterless.py` : modèles et modèle effectif)
et Postgres (`check_postgres.py` : SELECT 1, tables, aller-retour annulé) sont appelés.
Les sondes Zetaris sont en lecture seule et ne lisent ni n'inventent de données d'incident.
Résultat dans `data/verification_machine.json`. Aucune clé ni mot de passe n'est affiché
(valeurs masquées).

## 4 bis. Go / no-go

```powershell
python scripts\pret_reel.py              # lecture seule : machine, Nightshift, voies, Zetaris, Postgres, secrets, tests
python scripts\pret_reel.py --confirmer  # + un appel facturé par voie (modèle renvoyé contenant « nemotron »)
```

VERT / ORANGE / ROUGE avec, pour chaque ROUGE, la cause probable et la commande exacte ; code 0
seulement si tout ce qui est requis est VERT ; rapport `data/pret_reel.json` sans secret. La
procédure complète (rôles, minutes, dépannage, repli, preuves) est dans
[RUNBOOK-BRANCHEMENT.md](RUNBOOK-BRANCHEMENT.md).

## 5. Recette réelle en une commande

```powershell
python scripts\recette_reelle.py --confirmer
```

Ordre fixe : **Nightshift** (`check_meterless`), puis **Zetaris** (`check_zetaris`), puis
**Postgres** (`check_postgres`), puis A14 (modèle renvoyé contenant « nemotron » sur la voie
rapide ET la voie profonde), A15 (P1 rejouée 5 fois, même conclusion au moins 4 fois), la boucle
P1 -> P2 -> P3 complète (`scripts/boucle_agents.py`), la war room réelle (plafond de tours et
d'appels affiché avant lancement, arrêt au plafond) et la collecte des preuves sponsors.
`--confirmer` est obligatoire. Une étape dont le service n'a pas de clé (ou n'a pas été vérifié)
est NON_MESURE avec la raison, jamais ECHOUE. Rapport : `data/recette_reelle.json`, sans clé.

Détail, étape par étape, si besoin :

```powershell
python scripts\recette_module_a.py --confirmer
python scripts\recette_globale.py --confirmer
python scripts\boucle_agents.py --scenario p1,p2,p3 --confirmer
python scripts\collecter_preuves_sponsors.py --confirmer
```

## Repli

Si un service réel manque le jour J, revenir au mode simulé (`LLM_PROVIDER=fake`,
`SOURCE_PROVIDER=fichier`, `MEMOIRE_PROVIDER=json`) : l'interface et l'API affichent alors
« simulé ». Voir `docs/DECISIONS.md`.
