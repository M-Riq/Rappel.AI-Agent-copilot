# Rappel — Phase 0/1

- Python 3.11+, noms et messages en français. Les contrats JSON dans `contrats/`
  définissent les formats ; les fixtures doivent rester conformes.
- Ne pas modifier `README.md` dans cette PR : il est géré séparément par la PR #1.
- Machine 4 Go : `pip install -r requirements.txt`, puis `pytest`.
  Modes par défaut : `LLM_PROVIDER=fake`, `SOURCE_PROVIDER=fichier`,
  `MEMOIRE_PROVIDER=json`. Aucun service externe ni Docker requis.
- API : `uvicorn rappel.api:app --app-dir src`.
  Interface : `streamlit run ui/app.py`. Copier `.env.example` en `.env` au besoin.
- Machine de démo : installer `requirements-demo.txt`, démarrer Postgres avec
  `docker compose up -d postgres`, configurer `.env` puis activer explicitement
  `meterless`, `zetaris`, `postgres`. Zetaris et Nightshift sont installés
  séparément. Le mode fichier n'est pas une preuve sponsor pendant la démo.
- Meterless Nightshift est le fournisseur d'IA principal. Tout appel réel passe
  par son API compatible OpenAI, avec Authorization Bearer, `meterless-app: rappel`,
  `meterless-scope: user`. Les lanes utilisent Nemotron ; aucun appel direct NVIDIA,
  aucune clé NVIDIA dans le code ou `.env`.
- Zetaris est la source centrale, via REST uniquement. Une seule instruction SQL
  par appel, UUID neuf dans `X-Request-ID`, Authorization Bearer, `X-Org-ID`.
  Confirmer le corps REST dans la documentation locale avant la démo.
  Chaque preuve conserve sa requête SQL.
- Mémoire : Postgres en démo, JSON en développement. Aucun mem0, Redis ou MCP.
- Ne jamais journaliser de jetons, clés, identifiants de connexion ou en-têtes
  d'authentification. Aucun secret suivi ; `.env` et `data/` restent ignorés.
- L'API Phase 0/1 enregistre les décisions mais n'exécute jamais les patchs.
  Hors mode fake, ne pas présenter les événements de fixture comme une enquête réelle.
