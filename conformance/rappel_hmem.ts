import { spawnSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const racine = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const serveur = path.join(racine, "scripts", "hmem_serveur.py");
// python3 par défaut : sur macOS, « python » peut être Python 2 hors du .venv.
const pythonParDefaut = process.platform === "win32" ? "python" : "python3";
let pythonVerifie: string | undefined;

type RecordShape = Record<string, any> & { id: string };

function erreur(message: string): never {
  throw new Error(`H-MEM Python : ${message}`);
}

function interpreteur(): string {
  const commande = process.env.PYTHON ?? pythonParDefaut;
  if (pythonVerifie === commande) return commande;
  const essai = spawnSync(
    commande,
    ["-c", "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"],
    { encoding: "utf8" },
  );
  if (essai.error || essai.status !== 0) {
    erreur(
      `interpréteur « ${commande} » introuvable ou antérieur à Python 3.11 ; ` +
        "définir PYTHON vers le Python du .venv de Rappel (ex. PYTHON=/chemin/Rappel/.venv/bin/python).",
    );
  }
  pythonVerifie = commande;
  return commande;
}

function recordProxy(hmem: HMEM, value: RecordShape): RecordShape {
  return new Proxy(value, {
    set(target, property, next) {
      const key = String(property);
      hmem.call("set", { id: target.id, key, value: next });
      target[key] = next;
      return true;
    },
  });
}

export class HMEM {
  private path: string;
  private clock: () => number;
  private actor: string;

  store: any;
  ledger: any;
  sleep: any;
  dreaming: any;
  conflicts: any;

  constructor(options: { clock?: () => number; persistDir?: string; actor?: string } = {}) {
    this.clock = options.clock ?? Date.now;
    this.actor = options.actor ?? "conformance";
    const directory = options.persistDir ?? mkdtempSync(path.join(tmpdir(), "rappel-hmem-"));
    this.path = path.join(directory, "hmem.sqlite");

    this.store = {
      get: (id: string) => {
        const result = this.call("get", { id });
        return result ? recordProxy(this, result) : undefined;
      },
      all: () => this.call("all", {}).map((record: RecordShape) => recordProxy(this, record)),
    };
    this.ledger = {
      history: (id: string) => this.call("ledger.history", { id }),
      all: () => this.call("ledger.all", {}),
    };
    this.sleep = {
      preview: () => this.call("sleep.preview", {}),
      execute: (preview?: unknown) => this.call("sleep.execute", { preview }),
      restore: (backupId: string) => this.call("sleep.restore", { backupId }),
    };
    this.dreaming = {
      dream: () => this.call("dreaming.dream", {}),
      pending: () => this.call("dreaming.pending", {}),
      approve: (id: string) => this.call("dreaming.approve", { id }),
      reject: (id: string, reason?: string) => this.call("dreaming.reject", { id, reason }),
    };
    this.conflicts = {
      scan: () => this.call("conflicts.scan", {}),
      autoResolve: (conflict: unknown) => this.call("conflicts.autoResolve", { conflict }),
      queue: () => [],
    };
  }

  call(op: string, data: Record<string, unknown>): any {
    const child = spawnSync(
      interpreteur(),
      [serveur],
      {
        input: `${JSON.stringify({
          path: this.path,
          now: this.clock(),
          actor: this.actor,
          op,
          data,
        })}\n`,
        encoding: "utf8",
        maxBuffer: 8 * 1024 * 1024,
      },
    );
    if (child.error) erreur(child.error.message);
    if (child.status !== 0) erreur(child.stderr || `processus Python code ${child.status}`);
    let response: any;
    try {
      response = JSON.parse(child.stdout.trim().split("\n").at(-1) ?? "");
    } catch {
      erreur("réponse JSON Lines invalide.");
    }
    if (!response.ok) erreur(response.error ?? "opération refusée.");
    return response.result;
  }

  add(input: Record<string, unknown>): RecordShape {
    return recordProxy(this, this.call("add", input));
  }

  query(query: string, opts?: Record<string, unknown>): any {
    const result = this.call("query", { query, opts });
    return {
      ...result,
      memories: result.memories.map((item: any) => ({
        ...item,
        memory: recordProxy(this, item.memory),
      })),
    };
  }

  feedback(id: string, kind: string): RecordShape {
    return recordProxy(this, this.call("feedback", { id, kind }));
  }
}
