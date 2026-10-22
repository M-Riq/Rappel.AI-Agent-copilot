# Rappel — copilote d'investigation d'incidents DevOps / MLOps

Rappel est un copilote intelligent qui enquête sur les pannes de pipelines de données et de machine learning. Il rassemble les indices, compare plusieurs causes possibles, explique son diagnostic avec des preuves, consulte la mémoire des incidents précédents et propose une action adaptée au niveau de risque.

## Le problème

Les ingénieurs qui gèrent des pipelines de données ou de ML doivent consulter plusieurs sources : journaux d'erreurs, changements de code, schémas de données, métriques et incidents précédents. Ils refont souvent les mêmes vérifications, sous pression, alors que certaines causes ont déjà été confirmées ou réfutées.

**Exemple :** une boutique en ligne importe ses commandes. Le code attend la colonne `amount`; un développeur la renomme en `total` sans mettre à jour le pipeline. Le lendemain, le pipeline tombe. L'ingénieur doit lire les logs, identifier le changement de code, vérifier les colonnes, écarter les fausses pistes, puis corriger sans provoquer une nouvelle panne.

## Ce que fait Rappel

1. **Il enquête** : logs, changements de code et données pour trouver les causes possibles.
2. **Il apprend des incidents précédents** : il retrouve les causes confirmées et les hypothèses réfutées pour ne pas refaire l'enquête.
3. **Il propose une action prudente** : correctif, impact, validation humaine si le risque le justifie. Si les preuves sont insuffisantes, il **s'abstient**.

Le différenciateur : **preuves vérifiables + mémoire qui influence réellement les enquêtes suivantes + action contrôlée**.

## Fonctionnalités MVP

| Priorité | Fonctionnalité | Ce que le jury doit constater |
|---|---|---|
| 1 | Détection et collecte des indices | La panne produit des logs et un contexte d'incident réel |
| 2 | Enquête fondée sur des preuves | Comparaison de plusieurs hypothèses, justification de celle retenue |
| 3 | Mémoire des incidents et des hypothèses réfutées | Un incident similaire est retrouvé, ses conclusions réutilisées et les pistes réfutées sautées |
| 4 | Proposition de correctif | Correctif expliqué, avec risque et périmètre d'impact |
| 5 | Validation et vérification | Action contrôlée ; un échec déclenche un retour arrière |
| 6 | Abstention | Preuves insuffisantes : aucune modification |
| 7 | Interface de démonstration | Preuves, décision et résultat lisibles sans lire le code |

## Architecture

```
[Collecteurs] → [Dossier d'incident] → [Moteur d'hypothèses + règles]
     ▲ Zetaris                              │          ↕
                                            │  [Mémoire (Postgres)]
                                            │    secours : fichier JSON
                                            ├→ [Meterless Nightshift] → [Nemotron]
                                            ↓
                            [Planificateur de correctif + score de risque]
                                            ↓
                   [Validation humaine] → [Exécution + vérification + rollback]
                                            ↓
                                   [Interface de démo]
```

Le LLM propose des causes ; des **vérifications déterministes** les confirment ou les réfutent.

La mémoire des incidents et des **hypothèses réfutées** est stockée dans **Postgres** (table `hypotheses`), avec un fichier JSON comme secours. L'accès passe uniquement par `memoire.chercher(signature)` et `memoire.ecrire(enregistrement)`. Meterless n'est pas la mémoire : toutes les requêtes LLM passent par **Meterless Nightshift (Meterless Runtime)**, qui les exécute sur des lanes configurées avec des modèles **NVIDIA Nemotron**.

## Technologies sponsors

| Technologie | Rôle dans Rappel | Preuve à montrer | Code |
|---|---|---|---|
| **Meterless Nightshift** | Fournisseur d'IA principal ; passerelle OpenAI-compatible : `POST /v1/chat/completions`, `GET /v1/models`. Jeton Runtime dans `Authorization: Bearer`, en-têtes `meterless-app: rappel` et `meterless-scope: user` | Un appel réel via le Runtime et ses run details/receipts | Prévu : `src/rappel/interfaces/llm.py` |
| **NVIDIA Nemotron** | Modèles servis via Nightshift : lane rapide (ex. `nvidia/nemotron-3.5-lightning-30b-a3b`) pour hypothèses, appels d'outils et rôle détective ; lane profonde (ex. `nvidia/nemotron-3-super-120b-a12b`) pour la conclusion, le sceptique et le correctif. Les noms exacts dépendent du catalogue | Les run details/receipts de Nightshift indiquent un modèle Nemotron ; fixer une lane Nemotron, car `meterless/auto` peut router ailleurs | Via `src/rappel/interfaces/llm.py` (prévu) |
| **Zetaris** | Lecture des données (schéma, échantillons) via MCP, exposée à l'agent comme outil `run_sql` ; secours en lecture directe par configuration | Une lecture réelle via Zetaris alimente le dossier d'incident | Prévu : `src/rappel/ingestion/` et `src/rappel/interfaces/` |

### État d'intégration (transparence)

Aucune intégration n'est encore implémentée. La cible est la suivante :

- **Nightshift → Nemotron** : aucun appel direct à NVIDIA depuis Rappel. La **clé NVIDIA va uniquement dans Nightshift (Setup → Providers)**, jamais dans le code ni dans le `.env`.
- **Zetaris** : lecture via MCP ; secours en lecture directe (CSV/SQLite) activable par configuration. Le mode utilisé sera affiché dans le dossier d'incident (`source: zetaris` ou `source: fallback-local`) : la démo ne prétendra jamais à un appel réel qui n'a pas eu lieu.
- **Mémoire** : Postgres, avec secours JSON, indépendamment du fournisseur d'IA.

Configuration cible du `.env` :

```dotenv
METERLESS_BASE_URL=http://127.0.0.1:8088/v1
METERLESS_RUNTIME_TOKEN=
METERLESS_MODEL=meterless/auto
```

Copier l'URL et le jeton Runtime depuis la Console de Nightshift. `127.0.0.1` ne fonctionne que si Rappel et le Runtime sont sur la même machine. Ne jamais mettre la clé NVIDIA ici. Pour la démo, remplacer `meterless/auto` par la lane Nemotron choisie et vérifier le modèle dans les receipts.

## Démarrage rapide — cible, à venir

> **🚧 Statut : en construction, Phase 0**
>
> Le dépôt contient uniquement ce README : aucun code n'existe encore. Les commandes ci-dessous sont la cible et ne fonctionnent pas encore ; les fichiers, modules et points d'entrée restent à créer.

### Prérequis

- Git.
- Python 3.11 ou 3.12.
- Docker (pour Postgres uniquement).
- Meterless Nightshift installé (édition Windows/Mac/DGX, pas la version navigateur), avec NVIDIA connecté et une lane Nemotron active.

### Commandes cibles (à venir)

Depuis la racine du dépôt, dans un terminal Bash :

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # renseigner uniquement la configuration Runtime
docker compose up -d    # Postgres
uvicorn rappel.api:app --app-dir src --reload  # API FastAPI, terminal dédié
streamlit run ui/app.py                       # UI, autre terminal avec venv activé
pytest                                       # tests, autre terminal avec venv activé
```

## Scénarios de démonstration

- **P1** : `KeyError: 'amount'` → hypothèse « colonne renommée » confirmée, correctif, validation.
- **P2** : incident similaire → la mémoire est réutilisée, les pistes réfutées sont sautées.
- **Échec** : le test de non-régression échoue → retour arrière automatique.
- **Abstention (P3)** : preuves insuffisantes → aucune modification.

## Structure cible

Seul `README.md` existe aujourd'hui ; le reste de cette arborescence est à venir.

```text
.
├── README.md
├── .gitignore
├── .env.example
├── docker-compose.yml
├── contrats/
├── fixtures/
├── pipeline_demo/
├── ui/
├── .github/
│   └── copilot-instructions.md
├── src/
│   └── rappel/
│       ├── enquete/
│       ├── memoire/
│       ├── ingestion/
│       ├── action/
│       └── interfaces/
└── tests/
```

## Équipe

- **Naomy** : Enquête, Mémoire, Abstention, War room.
- **Juan** : Ingestion, Action, Rayon d'impact.
- **Riq** : Interface, tests, pitch.

## Sécurité

Aucune clé dans le dépôt. À la création du projet, `.env` devra être dans `.gitignore` ; seuls des exemples sans secrets seront versionnés. La clé NVIDIA reste uniquement dans Nightshift, jamais dans le code ni dans le `.env`.

## Licence

À définir.
