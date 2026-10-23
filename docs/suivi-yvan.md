# Livraison Yvan - interface Phase 0/1

Date : 2026-10-23. Branche de travail : `ivan`.

## Perimetre du lot

- Interface Streamlit, tests associes et theme sombre.
- Aucun changement du backend, des contrats, des fixtures ou du README.
- Aucune installation de sponsor, aucune cle requise et aucun patch execute.

## Fonctions disponibles

- Saisie d'un identifiant d'incident, actualisation manuelle et polling optionnel toutes les cinq secondes.
- Fil trie par etape puis horodatage, modele declare et SQL visibles.
- Avertissement lorsqu'une preuve n'a pas de requete SQL.
- Separation entre API et fixture locale P1 ; aucune substitution automatique en cas d'erreur API.
- Rejet d'evenements explicitement rattaches a un autre incident.
- Trois zones : enquete, memoire et action. La memoire indique que ses donnees ne sont pas encore transmises.
- Correctif P1 de demonstration : risque, confiance, reversibilite, elements touches, patch et justification.
- Appliquer/Refuser relies a la route existante uniquement pour P1, en mode API fake/fichier.
- Application desactivee pour un correctif refuse ou irreversible ; aucune action proposee en cas d'abstention.
- Boutons desactives en consultation locale et apres un envoi dans la session Streamlit.
- En cas de timeout ou de reponse invalide, decision non confirmee et aucun renvoi automatique.

Le verrou des boutons est local a la session, pas une garantie d'idempotence du serveur.
Apres une decision non confirmee, verifier l'etat cote API avant toute nouvelle tentative,
y compris dans une nouvelle session. La route Phase 0/1 conserve les decisions en memoire
et repond `execute: false` ; elle ne repare pas le pipeline.

## Lancement local

Sur cette machine, l'environnement Python 3.12.15 verifie est `.venv/yvan312`.
Activer cet environnement dans chaque terminal :

```bash
source .venv/yvan312/bin/activate
```

Premier terminal :

```bash
LLM_PROVIDER=fake SOURCE_PROVIDER=fichier MEMOIRE_PROVIDER=json python -m uvicorn rappel.api:app --app-dir src --host 127.0.0.1 --port 8000
```

Second terminal :

```bash
LLM_PROVIDER=fake SOURCE_PROVIDER=fichier MEMOIRE_PROVIDER=json API_URL=http://127.0.0.1:8000 python -m streamlit run ui/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

Interface : http://127.0.0.1:8501. API : http://127.0.0.1:8000/docs.
Les serveurs restent limites a la machine locale. Aucun fichier `.env` n'est necessaire pour ce mode.

## Verifications

```bash
python -m pytest tests/test_ui.py -q
python -m pytest -q
```

- 26 tests UI passes : tri, choix d'incident, erreurs, simulation, polling, decisions et garde-fous.
- Un test Streamlit est relie a FastAPI TestClient, sans services externes.
- Navigateur : refus P1 enregistre, boutons desactives, theme sombre et absence de debordement horizontal a 1440 et 390 pixels.
- Avertissement connu : deprecation Starlette TestClient/httpx, deja present avant ce lot.

## Raccordements a valider avec l'equipe

| Besoin | Interlocuteur | Etat |
| --- | --- | --- |
| Evenements d'enquete reelle et preuves SQL pour chaque preuve | Naomy / Juan | API reelle encore indisponible ; une preuve de fixture P1 n'a pas de SQL |
| Incidents consultes et hypotheses refutees evitees | Naomy | Lecture API a convenir, sans lire directement la base depuis l'UI |
| Correctif reel, identifiant, execution, succes et retour arriere | Juan | Lecture et suivi du correctif a convenir ; seule la decision P1 simulee est disponible |
| Etapes d'enquete, duree, tokens et appels Nemotron | Naomy / Juan | Mesures a exposer ; le nombre d'evenements affiche n'est pas un nombre de tours |
| Comparaison P1/P2 et abstention P3 de bout en bout | Naomy / Juan | Bloque par la boucle reelle et ses mesures ; aucun gain chiffre invente |
| Langfuse cloud et trace correlee a chaque incident | Yvan avec Naomy / Juan | Compte et instrumentation de l'execution a configurer ; aucune trace reelle validee |
| Badge Nemotron via Meterless et preuves sponsors | Yvan avec Naomy / Riq | Ne pas afficher une certification sponsor a partir du seul mode fake ou d'un nom de modele declare |
| README et video | Yvan / Riq | README reserve a sa PR dediee ; video finale apres integration reelle |

Les changements de contrats, fixtures et interfaces necessitent l'accord de l'equipe et la validation de Riq.
Ce lot ne valide pas C2, C4, C6, C8-C10 ni la demonstration reelle des sponsors.