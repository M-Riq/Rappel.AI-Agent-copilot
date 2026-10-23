# Proposition de texte pour README.md

Texte que Riq peut reprendre dans `README.md` (ce fichier n'a pas été modifié par
l'intégration). À adapter au ton du README actuel.

---

## État de l'intégration (23 octobre 2026)

Le moteur d'enquête est intégré au squelette Phase 0/1 :

- **Enquête** (`src/rappel/enquete/`) : le LLM propose au moins deux hypothèses, chacune avec
  une requête SQL ; le code exécute les requêtes (lecture seule, une instruction, tables en liste
  blanche, 50 lignes au plus), le LLM rend un verdict, et **le code ne retient une hypothèse que si
  elle cite une requête réellement exécutée qui renvoie des lignes**. Puis cause et correctif à
  valider, ou abstention. Plafond de tours (`MAX_TOURS_ENQUETE`) et d'appels (`LLM_MAX_APPELS`).
- **LLM** : Meterless Nightshift uniquement (lanes rapide et profonde Nemotron), `FakeLLM` hors ligne.
- **Preuves** : `SOURCE_PROVIDER=fichier` (base SQLite simulée construite depuis le contexte,
  affichée « simule-sqlite ») ou `zetaris` (Zetaris via API REST, une requête par preuve).
- **Mémoire** : JSON en développement, Postgres en démo ; les hypothèses réfutées ne sont pas
  retestées sur un incident de même signature.
- **API** : contrat du squelette conservé ; `POST /incidents` lance l'enquête en arrière-plan.

## Démarrage rapide

Mode simulé, sans réseau ni clé (Windows : `.venv\Scripts\activate`, `copy` au lieu de `cp`) :

```bash
git clone <url-du-depot> rappel && cd rappel
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
pytest --ignore=tests/test_ui.py
python scripts/demo_enquete.py
python scripts/incident_depuis_demo.py --scenario p1 --local
python scripts/recette_globale.py
uvicorn rappel.api:app --app-dir src
```

## Installation et commandes

```bash
pip install -r requirements.txt            # inclut langgraph (moteur et tests)
pytest --ignore=tests/test_ui.py           # sans Streamlit ; pytest complet avec l'interface
uvicorn rappel.api:app --app-dir src       # API locale
python scripts/demo_enquete.py             # P1, P2, P3 : métriques et contrôle de santé
python scripts/verifier_pret.py            # configuration, sécurité du dépôt, démo à blanc
python scripts/recette_module_a.py         # recette A1-A15 du Module A (data/recette.json)
streamlit run ui/app.py                    # interface P1
```

Machine de démonstration : `pip install -r requirements-demo.txt` (psycopg),
`docker compose up -d postgres`, puis dans `.env` : `LLM_PROVIDER=meterless`,
`SOURCE_PROVIDER=zetaris`, `MEMOIRE_PROVIDER=postgres`. Vérifier avec
`python scripts/check_meterless.py`, `python scripts/check_zetaris.py` et
`python scripts/check_postgres.py`, puis `python scripts/recette_module_a.py --confirmer`.
Si Postgres est injoignable, la mémoire bascule sur le JSON local et l'API l'affiche
(`memoire_mode_effectif`, `memoire_mode` : `fallback-json`).

## Modes simulé et réel

| Réglage | Simulé (défaut) | Réel (machine de démo) |
| --- | --- | --- |
| `LLM_PROVIDER` | `fake` | `meterless` (Nightshift, Nemotron) |
| `SOURCE_PROVIDER` | `fichier` (SQLite en mémoire) | `zetaris` (Zetaris via API REST) |
| `MEMOIRE_PROVIDER` | `json` | `postgres` (secours JSON automatique) |
| `ACTION_REELLE_DEMO` | `0` (aucune modification) | `1` : applique le correctif au pipeline de démo isolé |

En mode simulé, l'API et les scripts le disent (`enquete_simulee`, `preuves_simulees`,
« simulé » dans les recettes) : ce n'est pas une preuve sponsor. Les appels réels exigent
`--confirmer` dans les scripts.

## Limites honnêtes

- Meterless Nightshift, Zetaris et Postgres ne sont testés qu'avec des doubles hors réseau ;
  le corps REST Zetaris et l'exposition des tables du moteur restent à confirmer.
- Les seuils (abstention 0,6) sont calibrés sur trois scénarios et des perturbations
  synthétiques ; la confiance est un score heuristique.
- API locale sans authentification, sans persistance des incidents ; elle n'applique un correctif
  qu'au pipeline de démo isolé (`PIPELINE_DEMO_DIR`), avec sauvegarde, et seulement si
  `ACTION_REELLE_DEMO=1`.
- La stabilité avec un vrai modèle (même conclusion 4 fois sur 5) reste à mesurer.
- Origine du code : voir `docs/ORIGINE-DU-CODE.md` ; dossier de soumission : `docs/SOUMISSION.md`.
