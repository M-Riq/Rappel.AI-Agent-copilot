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
| 3 | Mémoire des incidents | Un incident similaire est retrouvé et ses conclusions réutilisées |
| 4 | Proposition de correctif | Correctif expliqué, avec risque et périmètre d'impact |
| 5 | Validation et vérification | Action contrôlée ; un échec déclenche un retour arrière |
| 6 | Abstention | Preuves insuffisantes : aucune modification |
| 7 | Interface de démonstration | Preuves, décision et résultat lisibles sans lire le code |

## Architecture

```
[Collecteurs] → [Dossier d'incident] → [Moteur d'hypothèses (Nemotron + règles)]
     ▲ Zetaris                                ↓                ↑
                                      [Mémoire (Meterless)]────┘
                                              ↓
                                   [Planificateur de correctif + score de risque]
                                              ↓
                          [Validation humaine] → [Exécution + vérification + rollback]
                                              ↓
                                     [Interface de démo]
```

Le LLM propose des causes ; des **vérifications déterministes** les confirment ou les réfutent.

## Technologies sponsors

| Technologie | Rôle dans Rappel | Preuve à montrer | Code |
|---|---|---|---|
| **Zetaris** | Accéder aux données utiles à l'enquête (schéma, échantillons) | Une lecture réelle via Zetaris alimente le dossier d'incident | `rappel/integrations/zetaris.py` |
| **NVIDIA Nemotron** | Modèle d'IA qui raisonne sur les indices et explique le diagnostic | Un appel réel au modèle pendant la démo | `rappel/integrations/nemotron.py` |
| **Meterless** | Couche de contexte et de mémoire retenue entre incidents | Mémoire réutilisée entre P1 et P2 ; fonctionnalité Meterless intégrée précisée ci-dessous | `rappel/integrations/meterless.py` |
| **Cursor + SpaceXAI** | Accélérer développement, tests et débogage | Utilisation documentée dans `docs/CURSOR.md` | `.cursor/rules/rappel.mdc` |

### État d'intégration (transparence)

- **Nemotron** : appel via l'API compatible OpenAI de NVIDIA NIM (`https://integrate.api.nvidia.com/v1`), clé `NVIDIA_API_KEY`.
- **Zetaris** et **Meterless** : les interfaces publiques exactes n'étant pas confirmées, les adaptateurs sont configurables par variables d'environnement (URL, clé, chemins d'API) et utilisent un **repli local explicite** (CSV/SQLite pour les données, JSON pour la mémoire). Le mode utilisé est toujours affiché dans le dossier d'incident (`source: zetaris` ou `source: fallback-local`) : la démo ne prétend jamais à un appel réel qui n'a pas eu lieu. Adaptez les chemins d'API dans `.env` avec la documentation fournie par les sponsors.

## Démarrage rapide

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # renseigner les clés
python -m rappel.cli demo --scenario amount_total      # P1 : première panne
python -m rappel.cli demo --scenario amount_total_bis  # P2 : réutilise la mémoire
python -m rappel.cli demo --scenario unknown           # abstention
python -m pytest
```

## Scénarios de démonstration

- **P1** : `KeyError: 'amount'` → hypothèse « colonne renommée » confirmée, correctif, validation.
- **P2** : incident similaire → la mémoire est réutilisée, les pistes réfutées sont sautées.
- **Échec** : le test de non-régression échoue → retour arrière automatique.
- **Abstention** : preuves insuffisantes → aucune modification.

## Utilisation de Cursor dans le développement

Voir [`docs/CURSOR.md`](docs/CURSOR.md).

## Structure

```
rappel/
  models.py  collectors.py  investigator.py  risk.py  fixer.py  cli.py
  integrations/ zetaris.py nemotron.py meterless.py
demo/ scenarios (pipeline cassé, logs, schéma)
tests/
docs/CURSOR.md
```

## Licence

À définir.
