# Accès et commandes

Qui détient quel accès, où le ranger, et quelles commandes lancer. **Aucune valeur secrète
dans ce dépôt, dans les messages d'équipe ni dans les captures** : chaque clé vit uniquement
dans le `.env` local (ignoré par git) de la machine qui en a besoin.

## Accès

| Accès | Détenteur | Où il vit | Vérification |
| --- | --- | --- | --- |
| Jeton Meterless Nightshift et identifiants des voies (console) | Naomy | `.env` : `METERLESS_RUNTIME_TOKEN`, `METERLESS_MODEL_RAPIDE`, `METERLESS_MODEL_PROFOND` | `python scripts/check_meterless.py` |
| Instance Zetaris (API REST, port 8888 ; SQL Editor port 3000) | Juan | `.env` : `ZETARIS_BASE_URL`, `ZETARIS_API_KEY`, `ZETARIS_ORG_ID` | `python scripts/check_zetaris.py` |
| Postgres local (Docker) | machine de démo | `.env` : `DATABASE_URL` (identifiant local de démonstration) | `python scripts/check_postgres.py` |
| Langfuse (optionnel) | Naomy | `.env` : `LANGFUSE_*` (vides = désactivé) | trace visible dans Langfuse |
| Dépôt GitHub, formulaire de dépôt | Riq | compte de l'équipe | accusé de réception |

Si une clé fuit (journal, capture, commit) : la révoquer dans la console du service, en créer
une nouvelle, relancer `python scripts/scan_secrets.py`.

## Commandes par personne

| Personne | Commandes |
| --- | --- |
| Naomy (enquête, mémoire, abstention) | `pytest --ignore=tests/test_ui.py` ; `python scripts/recette_module_a.py [--confirmer]` ; `python scripts/collecter_preuves_sponsors.py [--confirmer]` |
| Juan (Zetaris, ingestion, action, rayon d'impact) | `./verify.sh` puis `SELECT 1 AS one` dans le SQL Editor ; `python scripts/verifier_machine_demo.py --confirmer` ; `python scripts/injecteur.py p1` |
| Riq (interface, bout en bout, vidéo) | `uvicorn rappel.api:app --app-dir src` ; `streamlit run ui/app.py` ; `python scripts/recette_globale.py [--confirmer]` ; `pytest` (avec Streamlit) |

## Commandes communes

```bash
python scripts/verifier_pret.py                # configuration, sécurité du dépôt, démo à blanc
python scripts/scan_secrets.py                 # secrets : historique et arbre de travail
python scripts/verifier_machine_demo.py        # machine de démo (local) ; --confirmer : services réels
python scripts/test_demarrage_propre.py        # parcours « Démarrage rapide » sur un clone propre
```

Les scripts qui appellent un service réel exigent `--confirmer` et affichent le plafond
d'appels (`LLM_MAX_APPELS`) avant de commencer.
