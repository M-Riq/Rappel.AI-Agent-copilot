# Origine du code

Déclaration de Naomy Tcheums pour l'équipe Rappel. Les dates viennent de `git log`
(fuseau +01:00) ; l'historique d'origine est conservé dans ce dépôt.

## 1. Code écrit avant la fenêtre de build (6-7 octobre 2026)

Le moteur d'enquête, l'interface mémoire et leurs tests ont été écrits **les 6 et 7 octobre
2026**, dans le dépôt `AI-Agent-devops-`, **avant l'ouverture de la fenêtre de build
(22 octobre 2026)**. Ce dépôt avait été créé le 2 octobre (`79af4fe`, « Initial commit »,
M-Riq).

| Commit | Date | Contenu |
| --- | --- | --- |
| `26f4d6c` | 06/10 16:08 | Contrats et fixtures P1/P2/P3 (types dans le schéma) |
| `e57a36f` | 06/10 16:16 | Interfaces : mémoire JSON, LLM faux, abstention à seuil |
| `a5bd785` | 06/10 16:46 | Graphe LangGraph de l'enquête, outils déterministes, événements (A1-A5) |
| `bec2134` | 06/10 17:14 | Influence de la mémoire, abstention calibrée (A6-A10) |
| `a58e2bd` | 06/10 17:34 | Adaptateurs LLM NVIDIA et Claude, secours, plafond d'appels (A13) |
| `df7138f` | 06/10 18:47 | Outils : logs sains ignorés comme preuves |
| `2e1eaaa` | 06/10 19:13 | War room (A11-A12), dépendances API, `api.md` |
| `98c2d18` | 06/10 19:37 | Scripts d'essai LLM, war room, démo P1-P2-P3 |
| `7825b4d` | 06/10 20:01 | Documentation du module Enquête et Mémoire |
| `81c0008` | 06/10 20:18 | Traceur optionnel (A14), stabilité (A15), vérificateur de préparation |
| `6ff998c` | 06/10 20:32 | Fixtures générées depuis l'enquête |
| `7ed3acf` | 07/10 06:56 | API : liste des incidents, mémoire, réinitialisation |

Les numéros A1-A15 de ce tableau reprennent les messages de commit de l'époque, avec une
numérotation antérieure au cahier MVP v4 (la correspondance actuelle est dans `module-enquete.md`).

Ce code préexistant comprend : le graphe d'enquête en mode « outils » (hypothèses
proposées par le LLM, testées par des outils déterministes), la mémoire JSON et son influence,
la politique d'abstention, la war room, l'API locale d'origine, les scripts et les tests.

## 2. Rattachement de l'historique

Le commit `27b7c05` (23/10) est une fusion qui **rattache l'historique** de la branche
`naomy/mvp-sql` sans reprendre aucun fichier (aucune différence avec le squelette) :
l'historique d'origine reste consultable (`git log 27b7c05^2`). L'intégration elle-même
est faite dans les commits suivants.

## 3. Ajouté pendant la fenêtre de build (à partir du 22 octobre 2026)

Le squelette de l'équipe (M-Riq, Juan, agent Copilot) date du 22-23 octobre
(`b0d47b9` à `dd2ee41`) et fait foi pour la structure. Contributions de Naomy pendant le build :

| Commit | Date | Contenu |
| --- | --- | --- |
| `f937a2e` | 23/10 09:26 | MVP v4 (dépôt d'origine) : boucle de preuves SQL (hypothèses + requêtes, verdict vérifié par le code, plafond de tours), garde-fous `run_sql` et base SQLite simulée, LLM à deux voies, mémoire des hypothèses, calibration de l'abstention, conformité des événements (`modele`, `requete_sql`) |
| `27b7c05` | 23/10 09:27 | Rattachement de l'historique (voir section 2) |
| `4a13775` | 23/10 10:50 | Import dans le squelette : modèles, protocoles, adaptateurs `LLMEnquete` (Meterless) et `MemoireEnquete` (JSON/Postgres), `RunSqlZetaris`, configuration unique, alignement sur les tables et contrats de l'équipe (`sql/init.sql`, `contrats/*.schema.json`), tests de conformité |
| commit suivant | 23/10 | API étendue (enquête en thread, fil en direct, résultat, mémoire) sur le contrat du squelette, scripts portés, documentation et cette déclaration |

## 4. Finition du Module A (branche `naomy/finition-module-a`, 23 octobre 2026)

| Commit | Date | Contenu |
| --- | --- | --- |
| `70f31d0` | 23/10 15:29 | Recette automatique A1-A15 (`scripts/recette_module_a.py`) |
| `285e878` | 23/10 15:36 | Appels d'outils natifs `run_sql` optionnels (`LLM_APPEL_OUTILS`) |
| `1f3e22c` | 23/10 15:40 | Robustesse aux réponses réelles du modèle, contrôle de portabilité SQL |
| `c937de1` | 23/10 15:46 | Secours JSON automatique de la mémoire Postgres, `scripts/check_postgres.py` |
| `c1a0b90` | 23/10 15:49 | Route `GET /comparaison` (P1 contre P2) |
| `bffad77` | 23/10 15:59 | Documentation de finition du Module A |

## 5. Compléments modules A/B/C et pilotage (branche `naomy/complement-modules`, 23 octobre 2026)

Écrits par Naomy avec un assistant de code, y compris des parties attribuées à Juan (pipeline
de démo, ingestion, action, rayon d'impact) et à Riq (interface, dossier de soumission) dans le
README : à relire par eux avant fusion. Tout fonctionne en mode simulé ; rien n'a été exécuté
contre Nightshift, Zetaris ou Postgres réels.

| Commit | Date | Contenu |
| --- | --- | --- |
| `b70d7e5` | 23/10 16:55 | Pipeline de demo isole et injecteur P1/P2/P3 |
| `676f2a4` | 23/10 17:12 | Construction du ContexteIncident |
| `9bbfdd8` | 23/10 17:25 | Rayon d'impact auto/validation/refus |
| `97bd4dc` | 23/10 17:48 | Action avec sauvegarde, annulation et refus |
| `b392031` | 23/10 17:51 | Incident construit depuis le pipeline de demo |
| `a03f111` | 23/10 18:05 | Recette globale G1-G4 |
| `59dac9e` | 23/10 18:39 | Dossier de soumission et scan des secrets |
| `b9b362c` | 23/10 18:59 | Interface Streamlit en trois zones |
| `2d86978` | 23/10 19:06 | Traces Langfuse optionnelles |
| `22bbea6` | 23/10 19:18 | Scripts de preparation de la machine de demo |
| `7cf1ded` | 23/10 19:26 | Pilotage : acces, decisions, preuves sponsors |
| `f24451d` | 23/10 19:43 | Parcours de démarrage vérifié (`scripts/test_demarrage_propre.py`, `.gitattributes`) |

## 6. Boucle des 4 agents (branche `naomy/boucle-4-agents`, 24 octobre 2026)

Écrits par Naomy avec un assistant de code. Tout est vérifié en mode simulé (LLM fake, base SQLite,
mémoire JSON, pipeline de démo) ; rien n'a été exécuté contre Nightshift, Zetaris ou Postgres réels
(`scripts/recette_reelle.py --confirmer` reste à lancer avec les clés).

| Commit | Date | Contenu |
| --- | --- | --- |
| `55a321b` | 24/10 08:47 | Orchestrateur de la boucle des 4 agents |
| `422305f` | 24/10 09:05 | Resultat confirme avant de dire resolu |
| `be2b225` | 24/10 09:14 | War room : affichage et comparaison |
| `f3c40ee` | 24/10 09:20 | Graphe de relations des incidents |
| `bd7f4c6` | 24/10 09:23 | Rejeu d'une enquete jusqu'a une etape |
| `3c26f5f` | 24/10 09:30 | Recette reelle en une commande |
| commit suivant | 24/10 | Documentation boucle, war room, graphe |

## 7. Ce qui n'a pas été repris

- Adaptateurs NVIDIA et Claude, secours entre fournisseurs, variables `NIGHTSHIFT_*` et
  `RUN_SQL_BACKEND` : l'équipe passe uniquement par Meterless et par sa configuration.
- Scripts `essai_llm.py`, `essai_detective.py`, `essai_war_room.py` (ciblaient NVIDIA et Claude)
  et `check_meterless.py` d'origine (celui du squelette est conservé).
- L'API d'origine en tant que telle : ses routes ont été réécrites par-dessus le contrat du squelette.
