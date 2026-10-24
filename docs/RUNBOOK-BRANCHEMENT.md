# Runbook du branchement réel

Objectif : passer Rappel des doubles locaux aux vrais services (Meterless Nightshift + NVIDIA
Nemotron, Zetaris via API REST, Postgres, Langfuse optionnel) en une séance, sans dépenser à
l'aveugle et sans exposer de secret. Ordre imposé : **Nightshift, NVIDIA, Zetaris, Postgres,
Langfuse**. Tout ce qui précède a été vérifié contre des **faux services locaux**
(`tests/faux_services/`) : ce n'est PAS une preuve sponsor.

## Rôles

| Personne | Rôle pendant le branchement |
| --- | --- |
| Yvan / Riq | Nightshift : jeton Runtime, voies rapide et profonde, clé NVIDIA dans la console ; accord de dépense |
| Juan | Zetaris : instance démarrée (`./verify.sh`), tables de `docs/run_sql.md` exposées, clé API |
| Naomy | Commandes Rappel, `.env`, recette réelle, capture des preuves |
| Riq | Décision GO / NO-GO, accord avant toute commande facturée (`--confirmer`), vidéo |

## Déroulé minute par minute

| Minute | Qui | Action | Commande exacte | Attendu |
| --- | --- | --- | --- | --- |
| 0 | Naomy | État de la machine | `powershell -ExecutionPolicy Bypass -File scripts\preparer_machine_demo.ps1` | aucun ECHOUE |
| 2 | Yvan | Démarrer Nightshift, récupérer le fichier proxy-token | console Meterless | jeton Runtime disponible (pas `rt-local`) |
| 4 | Naomy | Détecter Nightshift | `python scripts\detecter_nightshift.py --fichier-jeton <chemin du proxy-token>` | « en ligne » et voies proposées |
| 6 | Yvan | Clé NVIDIA active côté Nightshift, Nemotron imposé sur chaque voie | console Nightshift | étape humaine (non vérifiable par Rappel) |
| 8 | Juan | Zetaris démarré, `SELECT 1 AS one` dans le SQL Editor | `./verify.sh` | OK |
| 10 | Naomy | Découvrir Zetaris (endpoint, clé du SQL, tables, dialecte) | `python scripts\decouvrir_zetaris.py` | SELECT 1 REUSSI, aucune table manquante |
| 12 | Naomy | Proposer le `.env` (jamais affiché, jamais écrasé sans `--forcer`) | `python scripts\preparer_env.py --fichier-jeton <chemin> --simuler` puis sans `--simuler` | `.env` écrit |
| 14 | Naomy | Postgres | `docker compose up -d postgres` puis `python scripts\check_postgres.py` | aller-retour OK |
| 16 | Naomy | Langfuse (optionnel) | clés `LANGFUSE_*` dans `.env` | trace visible, sinon ignorer |
| 18 | Naomy | Go / no-go en lecture seule | `python scripts\pret_reel.py` | seul « voies et modèle Nemotron » ORANGE |
| 20 | Riq | Accord de dépense (2 appels) | — | accord explicite |
| 21 | Naomy | Go / no-go confirmé | `python scripts\pret_reel.py --confirmer` | **GO** (code 0) |
| 23 | Riq | Accord de dépense (recette complète, plafond affiché avant lancement) | — | accord explicite |
| 24 | Naomy | Recette réelle en une commande | `python scripts\recette_reelle.py --confirmer` | aucun ECHOUE |
| 35 | Naomy | Captures de preuves (tableau ci-dessous) | — | fichiers `data\*.json` conservés |

## Dépannage

| Symptôme | Cause probable | Correction |
| --- | --- | --- |
| 401 Nightshift | jeton Runtime absent ou faux ; clé NVIDIA utilisée à la place du jeton | `--fichier-jeton` du proxy-token, `METERLESS_RUNTIME_TOKEN` dans `.env` |
| 403 Nightshift | jeton de démonstration `rt-local` refusé ; droits du jeton | jeton Runtime réel de la console |
| 404 Nightshift | `METERLESS_BASE_URL` sans `/v1` ou mauvais port ; voie inconnue | `python scripts\detecter_nightshift.py` (normalise l'URL, liste les voies) |
| Délai dépassé | modèle lent, Nightshift surchargé | `METERLESS_DELAI_S` (180 s par défaut) ; ne pas relancer en boucle (coupe-circuit après 3 échecs) |
| Nightshift choisit un autre modèle que Nemotron | voie `meterless/*` routée automatiquement | imposer Nemotron pour la voie dans la console, relancer `pret_reel.py --confirmer` (message « le routage a choisi <modèle> : fixer la voie ») |
| 429 / quota ou crédits insuffisants | crédits NVIDIA ou quota Nightshift épuisés | **arrêter**, prévenir Riq ; aucune relance sans accord |
| 401 Zetaris | `ZETARIS_API_KEY` invalide | nouvelle clé (Juan) |
| 403 Zetaris | `ZETARIS_ORG_ID` faux | identifiant d'organisation de l'instance |
| 404 Zetaris | `ZETARIS_BASE_URL` ou `ZETARIS_CHEMIN_SQL` faux | `python scripts\decouvrir_zetaris.py` (lit l'OpenAPI) |
| Table introuvable | tables de `docs/run_sql.md` non exposées | Juan expose les vues ; relancer `decouvrir_zetaris.py` |
| Dialecte refusé | construction non standard | une reformulation guidée est automatique ; sinon `docs/run_sql.md` (portabilité) |
| `host.docker.internal` | service appelé depuis un conteneur | depuis Windows, utiliser `127.0.0.1` ; depuis un conteneur, `host.docker.internal` |
| Port occupé | autre service sur 8088 / 5432 | `netstat -ano | findstr :8088`, changer le port dans `.env` |
| Chemins Windows longs | dépôt trop profond | cloner près de la racine (`C:\rappel`) ou activer les chemins longs |
| Fins de ligne | `core.autocrlf` | `.gitattributes` fixe LF pour les fixtures générées ; `python scripts\test_demarrage_propre.py` |
| Postgres injoignable | conteneur arrêté | `docker compose up -d postgres` ; sinon repli JSON automatique (`fallback-json` affiché) |

## Critères d'arrêt et repli

- Arrêt immédiat : quota ou crédits insuffisants, 3 échecs consécutifs (coupe-circuit), secret
  affiché par erreur (révoquer la clé, `python scripts\scan_secrets.py`), plafond
  `LLM_MAX_APPELS_SESSION` atteint.
- **Aucune dépense sans l'accord de Riq** : toute commande avec `--confirmer` est annoncée avant.
- Repli : mémoire JSON (`MEMOIRE_PROVIDER=json`), puis preuves simulées (`SOURCE_PROVIDER=fichier`),
  puis LLM simulé (`LLM_PROVIDER=fake`) ; l'interface et les rapports l'affichent (« simulé »).
  Décision à consigner dans `docs/DECISIONS.md`.

## Preuves à capturer par sponsor

| Sponsor | Preuve | Fichier ou écran |
| --- | --- | --- |
| Meterless Nightshift | catalogue `/v1/models` et appels comptés par voie | `data\detection_nightshift.json`, `data\preuves_sponsors.json` |
| NVIDIA Nemotron | modèle renvoyé contenant « nemotron » sur les deux voies (A14) | `data\pret_reel.json`, `data\recette_reelle.json` |
| Zetaris | `SELECT 1 AS one`, tables visibles, `requete_sql` des preuves (source `zetaris`) | `data\decouverte_zetaris.json`, fil d'enquête de l'interface |
| Postgres | aller-retour et souvenir de P1 en mémoire | sortie de `check_postgres.py`, `GET /memoire` |
| Tous | rapport complet sans secret | `data\recette_reelle.json` (horodaté) |
