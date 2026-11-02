# Feuille de route Rappel — Zetaris Demo Challenge (v9)

**Référence unique** : [`docs/CAHIER-DES-CHARGES-v9.md`](CAHIER-DES-CHARGES-v9.md) (version 9, 2 novembre 2026).
À **relire à la fin de chaque phase** : la revue de fin de phase (§ « Revue cahier ») est obligatoire avant de passer à la suivante.

**Échéance** : soumission **le 2 novembre 2026 avant 23 h 45 UTC** (§25 ; viser 20 h 00 UTC pour garder une marge).
Les phases 0 à 4 forment le **chemin critique du Challenge** (§6 « Indispensable »). Les phases 5 à 7 complètent le MVP et Alert & Approve **après** la soumission.

Légende des acteurs : **[Copilot]** code / documents dans le dépôt · **[Toi]** action manuelle sur ta machine (installation, clés, décisions).

---

## Suivi global

| Phase | Titre | Cahier § | Chemin critique | Statut |
|---|---|---|---|---|
| 0 | Remise à niveau du dépôt (v9, une personne, sans AWS) | 2, 10, 19, 26 | Oui | ⬜ À faire |
| 1 | Zetaris local sur le Mac Intel + données synthétiques | 11, 14 | Oui | ⬜ À faire |
| 2 | Réseau Mac → Nightshift (Windows) + preuve Nemotron | 12, 13 | Oui | ⬜ À faire |
| 3 | P1 réel de bout en bout (`run_id`, observabilité, mode strict) | 7 (F1, F2, F4, F5), 8, 10 bis, 16, 20 | Oui | ⬜ À faire |
| 4 | Vidéo et soumission | 24, 25 | Oui | ⬜ À faire |
| 5 | P2 (mémoire H-MEM) et P3 (abstention) en réel | 7 (F3, F6), 8, 15 | Non | ⬜ À faire |
| 6 | Alert & Approve (statuts, décisions persistantes, e-mail) | 9, 17 | Non | ⬜ À faire |
| 7 | Interface, reproductibilité, documentation finale | 7 (F7), 18, 21, 26 | Non | ⬜ À faire |

Mettre à jour la colonne « Statut » (⬜ À faire · 🟨 En cours · ✅ Validé par toi · ⛔ Bloqué) à chaque fin de phase.

---

## Analyse d'écarts : dépôt actuel vs cahier v9

Audit du 2 novembre 2026 sur `main` (97 commits). Suite de tests : **954 réussis, 4 ignorés** (tous contre de faux services).

### À conserver (conforme ou réutilisable)

| Élément | Où | Commentaire |
|---|---|---|
| Garde-fou SQL (lecture seule, une instruction, liste blanche, limite) | `src/rappel/interfaces/run_sql.py`, `securite.py` | Conforme §19 |
| Client Zetaris REST + passerelle UI (`/api/proxy/sql-editor/sqls/run-query`) | `interfaces/tools_zetaris.py` | Conforme §11 ; à revalider contre l'installation **locale** |
| `RunSqlZetaris` refuse le mode fichier (issue #15) | `run_sql.py` | Base du « mode Zetaris strict » §20 |
| Schéma attendu lu dans `rappel.schema_contract` | `ingestion/contrat_zetaris.py` | Conforme P1 étape 3 |
| Pipeline de démo isolé, injecteur `p1/p2/p3/reset`, logs, `etat.json` | `pipeline_demo/`, `scripts/injecteur.py` | Conforme §10 bis ; CSV sources non modifiés |
| Client Nightshift (OpenAI-compatible, plafonds d'appels) | `interfaces/llm.py` | Conforme §12, §23 ; champ `modele` à exploiter comme preuve |
| Boucle LangGraph, plafond de tours, abstention, rayon d'impact, exécuteur avec retour arrière | `enquete/`, `action/`, `interfaces/abstention.py` | Conforme F2, F4, F5, F6 (en simulé) |
| H-MEM local SQLite | `hmem/`, `interfaces/memoire_hmem.py` | Conforme §15 |
| Interface Streamlit 7 pages, badge « simulé » | `ui/` | Conforme §18 |
| Scripts de recette réelle, `scan_secrets.py` | `scripts/` | À exécuter en réel |

### À mettre à jour ou à créer

| # | Écart | Cahier § | Phase |
|---|---|---|---|
| E1 | README annonce « aucun code », équipe de 4, architecture une machine (`127.0.0.1`), mémoire Postgres | 2, 10, 26 | 0 |
| E2 | `.github/copilot-instructions.md` : « Phase 0/1 », machine 4 Go, mémoire Postgres en démo, PR #1 | 2, 15 | 0 |
| E3 | Références AWS (S3 `WorkspaceS3Bucket`, utilisateur IAM, « version AWS de Zetaris ») dans `run_sql.py`, `pret_reel.py`, `DECISIONS.md` | 2, 6 | 0 |
| E4 | Docs d'équipe obsolètes (`SOUMISSION.md` daté du 27 oct., `validation-ui-yvan.md`, `ACCES-ET-COMMANDES.md`, `INSTALLATION-DEMO.md` orienté Windows/une machine) | 2, 25 | 0 |
| E5 | `.env.example` : `METERLESS_BASE_URL=127.0.0.1` (Nightshift est sur le PC Windows), `MEMOIRE_PROVIDER=json` par défaut au lieu de `hmem`, pas de variables SMTP | 10, 13, 15, 17 | 0, 6 |
| E6 | Aucune source de données Zetaris locale : pas de Postgres « sources » ni MinIO, pas de script de chargement ni de SQL `CREATE DATASOURCE` / `REGISTER DATASOURCE TABLE` | 11, 14 | 1 |
| E7 | Pas de script de validation réseau en 10 étapes Mac → PC | 13 | 2 |
| E8 | Pas de `run_id` (seul `incident_id` côté API) | 10 bis, 14, 16 | 3 |
| E9 | Tables `pipeline_runs`, `incident_logs`, `observed_schema` inexistantes ; logs et schéma observé seulement en local, invisibles pour Zetaris | 10 bis, 14 | 3 |
| E10 | Mémoire Postgres bascule silencieusement en JSON (`fallback-json`) : contraire au « pas de repli silencieux » | 20 | 3 |
| E11 | Outil `run_sql` décrit « orders, logs, commits, métriques » au lieu des tables v9 | 14 | 3 |
| E12 | Mapping `total → amount` : condition « équivalence sémantique confirmée » à rendre explicite et vérifiée par SQL | 7 F4, 8 | 3 |
| E13 | Statuts d'incident (Detected … Failed), identité du décideur, historique persistant : absents (incidents en mémoire du processus) | 9, 16 | 6 |
| E14 | Notification e-mail : absente | 9, 17 | 6 |
| E15 | Persistance des incidents après redémarrage : non vérifiée | 16 | 6 |

---

## Phase 0 — Remise à niveau du dépôt (v9)

**Objectif** : le dépôt dit la vérité (§ « Statut de ce document ») et reflète l'architecture v9 : une personne, deux machines, aucun AWS.

**Étapes**

1. [Copilot] Versionner le cahier v9 (`docs/CAHIER-DES-CHARGES-v9.md`) et cette roadmap.
2. [Copilot] Réécrire le README : état réel (code existant, simulé vs réel), architecture Mac + PC Windows (§10), démarrage rapide qui fonctionne, lien vers la roadmap. Retirer la section Équipe (historique en une ligne).
3. [Copilot] Mettre à jour `.github/copilot-instructions.md` (v9, H-MEM, mode strict, règles §10 bis).
4. [Copilot] `.env.example` : `METERLESS_BASE_URL=http://<IP_PRIVEE_PC>:<PORT>/v1`, `MEMOIRE_PROVIDER=hmem`, commentaire sur `meterless/auto` interdit comme preuve.
5. [Copilot] Retirer les références AWS du code et des messages ; consigner l'historique AWS dans `DECISIONS.md` (« abandonné, nettoyage à faire par toi »). Archiver les docs d'équipe obsolètes dans `docs/archive/`.
6. [Toi] **En parallèle, lancer dès maintenant le téléchargement de Zetaris** (≈ 4 Go, phase 1).

**Tests locaux (Mac)**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-demo.txt
pytest -q                                  # attendu : 954+ réussis, 0 échec
python scripts/scan_secrets.py             # attendu : aucun secret
grep -rniE "aws|s3 bucket|iam" README.md src scripts .env.example   # attendu : aucune occurrence active
```

**Critère de sortie** : tests verts, aucun secret, README relu et validé par toi.

---

## Phase 1 — Zetaris local sur le Mac Intel + données synthétiques

**Objectif** : Zetaris tourne en local, et Python lit `transactions` (500 lignes) et `schema_contract` (6 lignes) **par Zetaris** (§11, §14). Postgres n'est qu'une **source** ; Zetaris reste la couche SQL.

**Étapes**

1. [Toi] Docker Desktop : 8 Go, 4 CPU, 15 Go libres. Télécharger le paquet `zetaris/getting-started` depuis ton compte Zetaris Cloud.
2. [Toi] `./preflight.sh` → `docker compose pull` → `docker compose up -d` → `./verify.sh` (9 contrôles). Noter tout écart lié au Mac **Intel** (non testé officiellement).
3. [Toi] Créer la clé API dans l'interface (`http://localhost:3000`), la mettre dans `.env` (`ZETARIS_API_KEY`), jamais ailleurs.
4. [Copilot] **Option A (prioritaire)** : service `postgres-sources` dans `docker-compose.yml` (rôle lecture seule `rappel_lecture`), script `scripts/charger_sources.py` qui charge les CSV **sans les modifier** (vérification d'empreinte SHA-256 avant/après).
5. [Copilot] `sql/zetaris/declarer_sources.sql` : `CREATE DATASOURCE … (JDBC)` + `REGISTER DATASOURCE TABLE` pour `transactions` et `schema_contract`, à coller dans le SQL Editor. **Option B (MinIO + `CREATE LIGHTNING FILESTORE TABLE`) seulement si A échoue.**
6. [Copilot] `scripts/check_zetaris.py --sources` : `SELECT 1`, `COUNT(*)` sur les deux tables, vérification de la colonne `required`.
7. [Toi] Redémarrer les conteneurs Zetaris et revérifier (persistance, §11).

**Tests locaux (Mac)**

```bash
./verify.sh                                         # dans le dossier Zetaris : 9/9 OK
docker compose up -d postgres-sources               # dans le dépôt Rappel
python scripts/charger_sources.py                   # attendu : 500 + 6 lignes, empreintes CSV inchangées
python scripts/check_zetaris.py --sources           # attendu : SELECT 1 OK ; transactions=500 ; schema_contract=6
# Dans http://localhost:3000 (SQL Editor) : SELECT COUNT(*) FROM <source>.transactions  -> 500
docker compose -f <zetaris>/docker-compose.yml restart && python scripts/check_zetaris.py --sources   # persistance
pytest -q tests/test_zetaris_passerelle.py tests/test_contrat_zetaris.py
```

**Critère de sortie** : les deux tables sont lues par REST Zetaris depuis Python ; capture d'écran du SQL Editor conservée pour la vidéo.
**Si bloqué** (installation impossible sur Intel) : ⛔ décision immédiate avec toi ; **aucun repli présenté comme réel** (§20).

---

## Phase 2 — Réseau Mac → Nightshift (PC Windows) + preuve Nemotron

**Objectif** : depuis le Mac, `POST /v1/chat/completions` atteint Nightshift sur le PC et la réponse **renvoie un modèle Nemotron** (§12, §13).

**Étapes**

1. [Toi, PC] Lancer Nightshift, fournisseur NVIDIA connecté, voie(s) Nemotron **fixée(s)** (pas `meterless/auto`). Noter l'IPv4 privée (`ipconfig`), le port, l'interface d'écoute (doit être `0.0.0.0` ou l'IP privée, pas `127.0.0.1`).
2. [Toi, PC] Pare-feu Windows : règle entrante sur le port, **profil Privé uniquement**.
3. [Copilot] `scripts/valider_reseau.py` : les 10 étapes du §13 en un rapport OK/ÉCHEC, jeton jamais affiché.
4. [Copilot] Enregistrer le champ `modele` renvoyé par le service dans chaque événement d'enquête et l'afficher (badge) ; refuser de qualifier « Nemotron » si le champ ne le contient pas.
5. [Toi] Renseigner `METERLESS_BASE_URL`, `METERLESS_RUNTIME_TOKEN`, `METERLESS_MODEL_RAPIDE`, `METERLESS_MODEL_PROFOND` dans `.env`.

**Tests locaux**

```powershell
# PC Windows
Get-NetTCPConnection -LocalPort <PORT> -State Listen      # adresse d'écoute ≠ 127.0.0.1
```

```bash
# Mac
nc -vz <IP_PC> <PORT>                       # port accessible
python scripts/valider_reseau.py            # attendu : 10/10 OK, dernière ligne « modèle renvoyé : …nemotron… »
python scripts/check_meterless.py           # attendu : réponse + modèle Nemotron
pytest -q tests/test_meterless_runtime.py tests/test_llm_fournisseurs.py
```

**Critère de sortie** : rapport 10/10 et modèle Nemotron visible ; coût de l'appel vérifié sur ton compte NVIDIA (§23).

---

## Phase 3 — P1 réel de bout en bout

**Objectif** : le déroulement des 9 étapes du §10 bis fonctionne en réel : échec réel du pipeline, `run_id`, preuves lues dans Zetaris, diagnostic Nemotron, mapping proposé seulement si équivalence confirmée, décision humaine, exécution sur la copie isolée, mémoire écrite.

**Étapes**

1. [Copilot] Générer un `run_id` unique par exécution du pipeline (`pipeline.py`, `etat.json`, contexte d'incident, API, contrats JSON).
2. [Copilot] Base d'observabilité (Postgres `postgres-sources`, schéma `observabilite`) : tables `pipeline_runs`, `incident_logs`, `observed_schema` (§14) remplies par Python à chaque exécution ; les CSV sources ne sont jamais écrits.
3. [Copilot + Toi] Déclarer ces tables dans Zetaris (même procédure que phase 1) pour qu'elles deviennent des **preuves interrogeables**.
4. [Copilot] Mettre à jour la description de l'outil `run_sql` et la liste blanche avec les tables v9.
5. [Copilot] **Mode Zetaris strict** : en mode réel, toute indisponibilité (Zetaris, Nightshift, mémoire) produit une erreur explicite, plus de bascule silencieuse (`fallback-json` interdit hors mode développement).
6. [Copilot] Règle d'équivalence `total ↔ amount` vérifiée par requêtes (même type numérique, mêmes `transaction_id`, aucune valeur nulle) avant toute proposition de mapping ; sinon pas de mapping.
7. [Copilot] `scripts/recette_reelle.py --scenario p1 --confirmer` : enchaîne les 9 étapes et produit `data/recette_p1.json` (requêtes, résultats, modèle, `run_id`, empreintes CSV).

**Tests locaux (Mac, Zetaris + Nightshift actifs)**

```bash
python scripts/injecteur.py reset && python scripts/injecteur.py p1
python scripts/recette_reelle.py --scenario p1 --confirmer
#   attendu : statut pipeline rouge ; run_id présent ; ≥ 2 hypothèses ; chaque preuve cite une requête Zetaris ;
#             modele contient « nemotron » ; mapping total->amount proposé avec équivalence confirmée ; ≤ 6 tours
uvicorn rappel.api:app --app-dir src &        # puis
streamlit run ui/app.py                       # parcours P1 complet sans ligne de commande (accepter le correctif)
python scripts/check_zetaris.py --run-id <run_id>   # incident_logs / pipeline_runs lisibles dans Zetaris
python scripts/stabilite_p1.py --repetitions 5 --confirmer   # rejeu 5 fois, run_id différents
shasum -a 256 pipeline_demo/donnees/*.csv     # identique à avant la phase
# Test du mode strict : arrêter Zetaris puis relancer la recette -> erreur explicite, aucun résultat « simulé » masqué
pytest -q
```

**Critère de sortie** : P1 réel vert 5 fois sur 5 ; aucune donnée source modifiée ; aucune correction hors pipeline isolé.

---

## Phase 4 — Vidéo et soumission (avant 23 h 45 UTC)

**Objectif** : vidéo conforme au §25 et soumission.

**Étapes**

1. [Toi] Confirmer dans le règlement : durée maximale, format, langue, lien de soumission, date (« Tue 2 Nov » vs lundi).
2. [Copilot] Script de tournage `docs/VIDEO-SCRIPT.md` (anglais) : SQL Editor Zetaris en direct → injection P1 → enquête avec requêtes Zetaris → badge modèle Nemotron → décision humaine → retour au vert ; segment de **40 à 60 s** « why Zetaris is amazing » ; passage transparent sur GitHub Copilot (critère « Working with SpaceXSI »).
3. [Copilot] Sous-titres anglais (`.srt`) et réponse « In one sentence, why did you think it was amazing? ».
4. [Toi] Enregistrer (fermer les applications lourdes, 16 Go), exporter en MP4, nommer `TEAM-MONSTERS_021126.mp4`.
5. [Toi] Soumettre ; conserver la confirmation.

**Tests locaux**

- Visionner la vidéo complète : Zetaris visible **en fonctionnement réel**, aucun badge « simulé » présenté comme réel, aucun secret à l'écran (`.env`, jetons, clé NVIDIA).
- `ffprobe TEAM-MONSTERS_021126.mp4` : durée et format conformes.
- `python scripts/scan_secrets.py` sur le dépôt public.

**Critère de sortie** : soumission confirmée avant 23 h 45 UTC.

---

## Phase 5 — P2 (mémoire H-MEM) et P3 (abstention) en réel

**Étapes**

1. [Copilot] `MEMOIRE_PROVIDER=hmem` par défaut en réel ; seule une résolution **validée par l'humain** est réutilisable ; un souvenir est « à revérifier », jamais une preuve (§15).
2. [Copilot] P2 : nouveau `run_id`, recherche H-MEM, comparaison anciennes/nouvelles preuves par SQL, explication de ce qui est réutilisé et des pistes réfutées sautées.
3. [Copilot] P3 : séparation faits / hypothèses, liste des informations manquantes, aucun correctif.
4. [Copilot] Compteur P1 vs P2 (tours, appels au modèle, durée) mesuré en réel.

**Tests locaux**

```bash
python scripts/injecteur.py reset && python scripts/injecteur.py p2 && python scripts/recette_reelle.py --scenario p2 --confirmer
#   attendu : souvenir P1 retrouvé (validé), moins d'appels que P1, explication de la réutilisation
python scripts/injecteur.py reset && python scripts/injecteur.py p3 && python scripts/recette_reelle.py --scenario p3 --confirmer
#   attendu : abstention, informations manquantes listées, aucun correctif proposé
# Contre-test : P2 sans validation humaine de P1 -> aucune résolution réutilisée automatiquement
pytest -q tests/test_hmem_rappel.py tests/test_memoire_influence.py tests/test_abstention_calibration.py
```

---

## Phase 6 — Alert & Approve

**Étapes**

1. [Copilot] Persistance des incidents (SQLite local) : survivent au redémarrage de l'API (§16).
2. [Copilot] Machine à états : Detected → Investigating → Awaiting Approval → Approved / Rejected / More Information Required → Resolved / Failed. **Approved ≠ exécuté** (§9).
3. [Copilot] Décisions avec identité (nom saisi), horodatage, commentaire ; historique consultable (API + page Streamlit).
4. [Copilot] E-mail via SMTP configurable (`SMTP_HOST`, `SMTP_PORT`, `SMTP_EXPEDITEUR`, `SMTP_DESTINATAIRES`, `RAPPEL_URL_PUBLIQUE`), testé avec **Mailpit** local (service Docker gratuit). Contenu : identifiant, scénario, gravité, description, horodatage, lien vers la fiche.
5. [Copilot] Sans mécanisme d'exécution : afficher l'instruction de correction **sans prétendre l'avoir appliquée**.

**Tests locaux**

```bash
docker compose up -d mailpit                 # interface : http://localhost:8025
python scripts/injecteur.py p1 && python scripts/recette_reelle.py --scenario p1 --confirmer
#   attendu : e-mail reçu dans Mailpit avec les 6 champs et un lien qui ouvre la fiche
# Dans l'interface : approuver, refuser, « investigation approfondie » sur trois incidents -> statuts corrects
# Redémarrer l'API -> incidents, statuts et historique des décisions toujours présents
pytest -q tests/test_alert_approve.py tests/test_notifications.py    # créés dans cette phase
```

---

## Phase 7 — Interface, reproductibilité, documentation finale

**Étapes**

1. [Copilot] Interface : chaque page affiche preuves, requêtes SQL, `modele`, `run_id`, statut, décisions ; badge « simulé » partout où c'est le cas (§18).
2. [Copilot] `scripts/test_demarrage_propre.py` en réel : clone neuf → `.env` → services → P1 vert.
3. [Copilot] Documents finaux : README, `INSTALLATION-DEMO.md` (Mac + PC), `DECISIONS.md`, `PREUVES-SPONSORS.md` avec les mesures réelles ; §26 du cahier mis à jour.
4. [Toi] Nettoyage AWS (utilisateur IAM `rappel-zetaris-lecture`, objets du bucket) — **à ton initiative uniquement** (§2, §19).

**Tests locaux**

```bash
python scripts/test_demarrage_propre.py --confirmer
python scripts/pret_reel.py --confirmer               # attendu : GO
python scripts/collecter_preuves_sponsors.py --confirmer
python scripts/scan_secrets.py && pytest -q
```

---

## Revue cahier (à faire à la fin de CHAQUE phase)

Relire `docs/CAHIER-DES-CHARGES-v9.md` et cocher :

- [ ] Zetaris reste la **couche SQL centrale** ; Postgres n'est qu'une source (§11).
- [ ] Python **n'écrit jamais** dans les CSV sources ni dans Zetaris (§10 bis, §14) — empreintes SHA-256 inchangées.
- [ ] Toute preuve conserve sa requête SQL ; tout résultat simulé porte un badge (§16, §18).
- [ ] Le `modele` renvoyé est enregistré ; `meterless/auto` n'est jamais une preuve (§12).
- [ ] Aucun repli silencieux (§20) ; aucune correction automatique sur des données métier (§24).
- [ ] Aucun secret dans Git ni dans les journaux ; API non exposée hors du Mac (§19).
- [ ] Aucune ressource AWS, aucun service payant (§6, §23).
- [ ] Le §26 (état réel) et le tableau « Suivi global » ci-dessus sont mis à jour sans surévaluer l'état.
