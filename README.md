# Rappel — copilote d'investigation d'incidents DevOps / MLOps

> « Un incident, une leçon. Jamais deux fois la même erreur. »

Rappel est un copilote intelligent qui enquête sur les pannes de pipelines de données et de machine learning. Il rassemble les indices **dans les données de l'organisation via Zetaris**, compare plusieurs causes possibles, explique son diagnostic en citant les requêtes qui le prouvent, propose un correctif prudent et **retient ce qu'il a appris**, y compris les pistes qui se sont révélées fausses.

## Le problème

Les ingénieurs qui gèrent des pipelines de données ou de ML doivent consulter plusieurs sources dispersées : journaux d'erreurs, changements de code, schémas de données, métriques et incidents précédents. En astreinte, la nuit, reconstituer la cause prend du temps, et l'on reteste souvent des pistes déjà écartées lors d'un incident précédent.

**Exemple :** une boutique en ligne importe ses commandes. Le code attend la colonne `amount` ; un développeur la renomme en `total` sans mettre à jour le pipeline. Le lendemain, le pipeline tombe avec 214 erreurs `KeyError: 'amount'`. L'ingénieur doit trouver la cause, la prouver, corriger sans rien casser d'autre, et ne pas refaire toute l'enquête la prochaine fois.

## Ce que fait Rappel

1. **Il enquête** : il interroge les données (logs, commits, schémas, métriques) **via Zetaris** pour tester plusieurs causes possibles.
2. **Il apprend des incidents précédents** : il retrouve les causes confirmées et les **hypothèses réfutées** pour ne pas refaire l'enquête.
3. **Il propose une action prudente** : correctif, rayon d'impact, validation humaine si le risque le justifie. Si les preuves sont insuffisantes, il **s'abstient**.

Le différenciateur : **preuves vérifiables (requêtes Zetaris) + mémoire qui influence réellement les enquêtes suivantes + action contrôlée**.

## Fonctionnalités MVP

| Priorité | Fonctionnalité | Ce que le jury doit constater |
|---|---|---|
| 1 | Détection et collecte des indices | La panne produit des logs et un contexte d'incident construit à partir de requêtes Zetaris |
| 2 | Enquête fondée sur des preuves | Nemotron compare plusieurs hypothèses ; chaque conclusion cite les requêtes Zetaris qui la prouvent |
| 3 | Mémoire des incidents et des hypothèses réfutées | Un incident similaire est retrouvé, ses conclusions réutilisées et les pistes réfutées sautées |
| 4 | Proposition de correctif | Correctif expliqué, avec niveau de risque et rayon d'impact |
| 5 | Validation et vérification | Action contrôlée ; un échec déclenche un retour arrière |
| 6 | Abstention | Preuves insuffisantes : aucune modification, informations manquantes listées |
| 7 | Interface de démonstration | Preuves, requêtes SQL, modèle utilisé, décision et résultat lisibles sans lire le code |

## Architecture

```
                       ┌──────────────────────────────┐
[Pipeline de démo] ──▶ │ ZETARIS (API REST, port 8888) │ ◀── tables : orders, logs, commits, métriques
                       └──────────────┬───────────────┘
                                      │ run_sql()  (une requête = une preuve)
                                      ▼
[Dossier d'incident] ──▶ [Boucle d'enquête LangGraph] ◀──▶ [Mémoire (Postgres) · secours JSON]
                                      │
                                      │ llm.completer()
                                      ▼
                    [METERLESS NIGHTSHIFT] ──▶ [NVIDIA NEMOTRON]
                     (fournisseur d'IA)         lane rapide / lane profonde
                                      │
                                      ▼
                 [Abstention] ──▶ [Correctif + rayon d'impact]
                                      ▼
            [Validation humaine] ──▶ [Exécution + vérification + rollback]
                                      ▼
                            [Interface de démo (Streamlit)]
```

**Boucle de l'agent :** Rappel envoie la situation à Nightshift avec la description de l'outil `run_sql`. Nemotron répond par un appel d'outil (une requête SQL) ; Rappel l'exécute sur Zetaris et renvoie le résultat via Nightshift. La boucle se répète (plafond de tours) jusqu'à une conclusion **fondée sur les résultats des requêtes**.

La mémoire des incidents et des **hypothèses réfutées** est stockée dans **Postgres** (tables `hypotheses` et `memoire_incidents`), avec un fichier JSON comme secours. L'accès passe uniquement par `memoire.chercher(signature)` et `memoire.ecrire(enregistrement)`.

## Technologies sponsors

| Technologie | Rôle dans Rappel | Preuve à montrer | Code (cible) |
|---|---|---|---|
| **Zetaris** | **Source de données centrale et obligatoire.** Accès via l'**API REST** ; l'outil `run_sql` exécute une requête SQL (Lightning SQL) sur les tables du pipeline enregistrées dans Zetaris. Chaque conclusion de l'agent remonte à ces requêtes. | Zetaris tourne pendant la démo ; les requêtes exécutées sont affichées sous chaque preuve | `src/rappel/interfaces/tools_zetaris.py` |
| **Meterless Nightshift** | **Fournisseur d'IA principal.** Toutes les requêtes LLM passent par le Runtime Meterless (API compatible OpenAI : `POST /v1/chat/completions`, `GET /v1/models`), avec le jeton Runtime en `Authorization: Bearer` et les en-têtes `meterless-app: rappel` et `meterless-scope: user`. | Connexion Meterless visible dans l'application (clés masquées) ; une vraie requête passe par Nightshift | `src/rappel/interfaces/llm.py` |
| **NVIDIA Nemotron** | Modèles exécutés **via les lanes de Nightshift** : lane rapide (ex. `nvidia/nemotron-3.5-lightning-30b-a3b`) pour les hypothèses, les appels d'outils et le rôle détective ; lane profonde (ex. `nvidia/nemotron-3-super-120b-a12b`) pour la conclusion, le sceptique et le correctif. Les noms exacts dépendent du catalogue NVIDIA et de Nightshift. | Les run details / receipts de Nightshift montrent un modèle Nemotron ; badge du modèle affiché dans le fil d'enquête | configuration des lanes dans Nightshift |

### État d'intégration (transparence)

Aucune intégration n'est encore implémentée. La cible est la suivante :

- **Zetaris** : `POST {ZETARIS_BASE_URL}/sql-editor/sqls/run` avec les en-têtes `Authorization: Bearer {ZETARIS_API_KEY}`, `X-Org-ID: 1` (installation locale) et `X-Request-ID` (un nouvel UUID à chaque requête). **Une seule instruction SQL par appel.** Le format exact du corps est à confirmer dans la référence de l'installation locale (`http://localhost:8888/redoc/index.html`). Ne pas utiliser le port 8889 (interne). La lecture directe des fichiers n'est qu'un secours de développement, **jamais utilisé en démo**.
- **Nightshift → Nemotron** : aucun appel direct à NVIDIA depuis Rappel. La **clé NVIDIA va uniquement dans Nightshift (Setup → Providers)**, jamais dans le code ni dans le `.env`. Pour la démo, une lane Nemotron est **fixée**, car `meterless/auto` peut router vers un autre modèle.
- **Mémoire** : Postgres, avec secours JSON, indépendante du fournisseur d'IA.

Configuration cible du `.env` (aucune valeur secrète dans le dépôt) :

```dotenv
# Meterless Nightshift (fournisseur d'IA) — valeurs copiées depuis la Console de Nightshift
METERLESS_BASE_URL=http://127.0.0.1:8088/v1
METERLESS_RUNTIME_TOKEN=
METERLESS_MODEL=meterless/auto
METERLESS_MODEL_RAPIDE=
METERLESS_MODEL_PROFOND=

# Zetaris (données) — clé créée soi-même dans l'interface web Zetaris
ZETARIS_BASE_URL=http://localhost:8888/api/v1.0
ZETARIS_API_KEY=
ZETARIS_ORG_ID=1

# Mémoire
DATABASE_URL=postgresql://rappel:rappel@localhost:5432/rappel
```

`127.0.0.1` ne fonctionne que si Rappel et Nightshift sont sur la même machine. Ne jamais mettre la clé NVIDIA ni un texte factice à la place du jeton Runtime (erreur 401).

## Démarrage rapide — cible, à venir

> **🚧 Statut : en construction, Phase 0**
>
> Le dépôt contient uniquement ce README : aucun code n'existe encore. Les commandes ci-dessous sont la cible et ne fonctionnent pas encore.

### Prérequis

- Git, Python 3.11 ou 3.12.
- Docker Desktop.
- **Zetaris installé en local** : Docker avec 8 Go de mémoire, 4 CPU et 15 Go de disque libre (donc une machine avec 16 Go de RAM ou plus ; sinon, version AWS de Zetaris). Installation validée par `./verify.sh`, `SELECT 1 AS one` dans le SQL Editor (`http://localhost:3000`) et le test REST.
- **Meterless Nightshift installé** (édition Windows/Mac/DGX, pas la version navigateur), lancé, avec NVIDIA connecté dans *Setup → Providers* et au moins une **lane Nemotron active**.
- Postgres (lancé par le `docker-compose.yml` du projet).

### Commandes cibles (à venir)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                           # renseigner Runtime Meterless et clé Zetaris
docker compose up -d                           # Postgres
python scripts/check_zetaris.py                # vérifie Zetaris (SELECT 1)
python scripts/check_meterless.py              # vérifie qu'une lane Nemotron répond
uvicorn rappel.api:app --app-dir src --reload  # API FastAPI
streamlit run ui/app.py                        # interface, autre terminal
pytest                                         # tests
```

## Scénarios de démonstration

- **P1** : `KeyError: 'amount'` → les requêtes Zetaris montrent le commit `a1b2c3` (« rename amount->total ») suivi de 214 erreurs → hypothèse « colonne renommée » confirmée, correctif, validation.
- **P2** : incident de la même famille (type de colonne modifié) → la mémoire est réutilisée, les pistes réfutées sont sautées, moins d'étapes et moins d'appels à Nemotron.
- **Échec** : le test de non-régression échoue → retour arrière automatique.
- **Abstention (P3)** : cause externe ou ambiguë → aucune modification, informations manquantes listées.

Le compteur de démo compare P1 et P2 : étapes, durée, tokens et nombre d'appels au modèle.

## Structure cible

Seul `README.md` existe aujourd'hui ; le reste de cette arborescence est à venir.

```text
.
├── README.md
├── .gitignore
├── .env.example
├── docker-compose.yml
├── sql/init.sql
├── contrats/
├── fixtures/
├── pipeline_demo/
├── scripts/            # check_zetaris.py, check_meterless.py
├── ui/
├── .github/
│   └── copilot-instructions.md
├── src/
│   └── rappel/
│       ├── enquete/
│       ├── memoire/
│       ├── ingestion/
│       ├── action/
│       └── interfaces/  # llm.py (Meterless), tools_zetaris.py (Zetaris), memoire.py, abstention.py
└── tests/
```

## Équipe

- **Naomy** : Enquête (boucle Nemotron + `run_sql`), Mémoire, Abstention, War room.
- **Juan** : Ingestion et données dans Zetaris, Action, Rayon d'impact.
- **Riq** : Interface, tests de bout en bout, vidéo et pitch.

## Sécurité

- Aucune clé dans le dépôt : `.env` est listé dans `.gitignore` ; seul `.env.example` (sans valeurs) est versionné.
- La **clé NVIDIA** reste uniquement dans Nightshift.
- Le **jeton Runtime Meterless** et la **clé API Zetaris** restent dans `.env`, jamais dans le code, les logs ou l'outil de codage.
- La clé Zetaris est créée soi-même dans l'interface web, jamais par un agent IA.

## Licence

À définir.
