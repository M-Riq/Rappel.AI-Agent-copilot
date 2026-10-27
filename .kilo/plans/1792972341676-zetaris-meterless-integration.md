# State of the code — Zetaris & Meterless Nightshift

## 1. Current state (what already works)

The repo is **Rappel**, a DevOps/MLOps incident investigation copilot. Both sponsor integrations are **implemented with real API clients and dual-mode simulation**:

| Component | File | Status |
|---|---|---|
| Zetaris REST client | `src/rappel/interfaces/tools_zetaris.py:179` (`run_sql`) | Real client + SQLite fallback (`fichier` mode) |
| Zetaris routing | `src/rappel/interfaces/run_sql.py:396-475` (`RunSqlZetaris`) | `source_provider == "zetaris"` → `tools_zetaris.run_sql` |
| Meterless Nightshift client | `src/rappel/interfaces/llm.py:132` (`MeterlessLLM`) | OpenAI-compatible client, fast/deep lane selection, discovery |
| LLM factory | `src/rappel/interfaces/llm.py:248` (`completer`) | `fake` vs `meterless` |
| Config | `src/rappel/config.py` | All env vars + validation |
| Diagnostics | `scripts/check_zetaris.py`, `scripts/check_meterless.py`, `scripts/verifier_machine_demo.py` | Working |
| Tests | `tests/test_run_sql.py`, `tests/test_meterless_runtime.py`, etc. | 626 tests pass in simulated mode |

**Default modes** (no external services): `LLM_PROVIDER=fake`, `SOURCE_PROVIDER=fichier`, `MEMOIRE_PROVIDER=json`.

## 2. What has to be done

### For Zetaris
1. **Confirm the REST contract on the live instance** — `tools_zetaris.py:188-200` hardcodes:
   - endpoint `ZETARIS_CHEMIN_SQL` = `/sql-editor/sqls/run`
   - body key `ZETARIS_CLE_SQL` = `sql`
   - headers `Authorization: Bearer {key}`, `X-Org-ID: 1`, `X-Request-ID`
   These are documented as *to be confirmed* in `README.md:72` and `docs/run_sql.md:108-113`. Run `python scripts/decouvrir_zetaris.py` against the real instance and align if different.
2. **Align the table/column contract** — `docs/run_sql.md:7-30` defines `logs`, `commits`, `colonnes_attendues`, `colonnes_observees`, `metriques`. `tools_zetaris.py:80-88` (SQLite fallback) only has `logs`, `commits`, `metriques`, `schema_orders`, `information_schema.columns`. The real Zetaris instance must expose the `docs/run_sql.md` table names (or the docs must be updated to match reality). `verifier_machine_demo.py:35-39` expects `pipeline.commits` + `pipeline.logs` returning commit `a1b2c3` and 214 `KeyError` rows.
3. **Verify the SQL dialect** — `docs/run_sql.md:105-158` lists supported functions and known portability gaps (`LIMIT`, `date`/`type` as reserved words, `NULL AS colonne` in `UNION ALL`, case-sensitivity of `LIKE`). Run `tests/test_run_sql.py` against the real instance.
4. **Replay P1 scenario end-to-end** — `python scripts/recette_reelle.py --confirmer` (requires real keys in `.env`).

### For Meterless Nightshift
1. **Obtain the Runtime token** — `METERLESS_RUNTIME_TOKEN` is empty by default; `llm.py:149-151` (`jeton_utilisable`) rejects the demo token `rt-local`. Get a real token from the Nightshift console and put it in `.env` (never in code).
2. **Fix the lane identifiers** — `config.py:19-20` has `meterless_model_rapide=""` and `meterless_model_profond=""` empty. `llm.py:205-213` falls back to `meterless/auto` when lanes are empty, which may route to a non-Nemotron model. `README.md:73` and `docs/DECISIONS.md:19-20` say: if Nightshift routes to a non-Nemotron model, fix the lane in `.env`. Run `python scripts/check_meterless.py` and confirm the effective model contains "nemotron".
3. **Verify A14 (Nemotron on both lanes)** — `scripts/recette_reelle.py` step A14 requires the returned model (not the requested one) to contain "nemotron" on *both* the fast and deep lanes.
4. **Set `LLM_PROVIDER=meterless`** in `.env` to switch from simulation to real.

### Cross-cutting
5. **Run `python scripts/verifier_machine_demo.py --confirmer`** — the Go/no-go gate from `docs/DECISIONS.md:5-26`. Each service decided separately; any failure → fallback (announced, never presented as real).
6. **Collect sponsor proofs** — `python scripts/collecter_preuves_sponsors.py --confirmer` writes `data/preuves_sponsors.json` (Zetaris queries, Meterless per-lane usage, Nemotron model names). Currently all `NON_MESURE`.
7. **Update README** — `README.md:68-101` still says "Aucune intégration n'est encore implémentée" and "le dépôt contient uniquement ce README", which is now false. The integration state table and "Phase 0 / en construction" block need to reflect reality.
8. **Decision log** — `docs/DECISIONS.md:24-26` has empty Postgres/Zetaris/Meterless rows for the J4 go/no-go. Fill in after `verifier_machine_demo.py --confirmer`.

## 3. Blockers / open questions
- **No live services available in this environment** — all real-mode scripts require Zetaris (port 8888), Meterless Nightshift (port 8088), and Postgres (Docker) running with valid keys in `.env`. These must be run on the demo machine by the owner (Juan for Zetaris, Naomy for Meterless).
- **SQL dialect unknown** — the exact Zetaris dialect (case-sensitivity, reserved words, `LIMIT` vs `FETCH FIRST`) can only be confirmed against the live instance.