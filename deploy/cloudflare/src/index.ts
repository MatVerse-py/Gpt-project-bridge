import { Container } from "@cloudflare/containers";
export { ContainerProxy } from "@cloudflare/containers";
import { env as globalEnv } from "cloudflare:workers";


type JsonObject = Record<string, unknown>;

type MatVerseEnv = {
  MATVERSE_CONTAINER: DurableObjectNamespace<MatVerseContainer>;
  MATVERSE_PRINCIPALS_JSON?: string;
  MATVERSE_BUILD_COMMIT?: string;
  MATVERSE_BUILD_REF?: string;
  MATVERSE_FROZEN_CONTRACT_HASH?: string;
  MATVERSE_RUNTIME_ID?: string;
  MATVERSE_BUILD_TIMESTAMP?: string;
};

const bindings = globalEnv as unknown as MatVerseEnv;
const INTERNAL_STATE_HOST = "state.matverse.internal";
const PILOT_INSTANCE = "pilot-v1";
const GENESIS = "GENESIS";
const textEncoder = new TextEncoder();

class StateHttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function objectValue(value: unknown, name: string): JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new StateHttpError(400, `${name} must be an object`);
  }
  return value as JsonObject;
}

function stringValue(value: unknown, name: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new StateHttpError(400, `${name} must be a non-empty string`);
  }
  return value;
}

function integerValue(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new StateHttpError(400, `${name} must be a safe integer`);
  }
  return value;
}

function jcs(value: unknown): string {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new StateHttpError(400, "JCS numbers must be safe integers");
    return String(value);
  }
  if (Array.isArray(value)) return `[${value.map((item) => jcs(item)).join(",")}]`;
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${jcs(record[key])}`)
      .join(",")}}`;
  }
  throw new StateHttpError(400, `unsupported JCS type: ${typeof value}`);
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", textEncoder.encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function parseJson(request: Request): Promise<JsonObject> {
  try {
    return objectValue(await request.json(), "request body");
  } catch (error) {
    if (error instanceof StateHttpError) throw error;
    throw new StateHttpError(400, "request body must be valid JSON");
  }
}

function decodeJsonObject(value: unknown, name: string): JsonObject {
  if (typeof value !== "string") throw new StateHttpError(500, `${name} is not stored as JSON text`);
  try {
    return objectValue(JSON.parse(value), name);
  } catch (error) {
    if (error instanceof StateHttpError) throw error;
    throw new StateHttpError(500, `${name} contains invalid JSON`);
  }
}

function rowToIntent(row: Record<string, unknown>): JsonObject {
  const principalId = stringValue(row.principal_id, "principal_id");
  const actorId = typeof row.actor_id === "string" && row.actor_id ? row.actor_id : principalId;
  return {
    intent_id: row.intent_id,
    intent_hash: row.intent_hash,
    status: row.status,
    principal_id: principalId,
    actor_id: actorId,
    requested_operation: row.requested_operation,
    target: { kind: row.target_kind, id: row.target_id },
    parameters_hash: row.parameters_hash,
    parameter_persistence: "HASH_ONLY",
    source: decodeJsonObject(row.source_json, "source_json"),
    created_at: row.created_at,
    receipt: decodeJsonObject(row.receipt_json, "receipt_json"),
    execution_decision: "HOLD",
  };
}

export class MatVerseContainer extends Container {
  defaultPort = 8000;
  sleepAfter = "10m";
  enableInternet = false;
  pingEndpoint = "localhost/health";
  envVars = {
    MATVERSE_INSTITUTIONAL_STATE_URL: `http://${INTERNAL_STATE_HOST}`,
    MATVERSE_PRINCIPALS_JSON: String(bindings.MATVERSE_PRINCIPALS_JSON ?? ""),
    MATVERSE_BUILD_COMMIT: String(bindings.MATVERSE_BUILD_COMMIT ?? ""),
    MATVERSE_BUILD_REF: String(bindings.MATVERSE_BUILD_REF ?? "main"),
    MATVERSE_FROZEN_CONTRACT_HASH: String(bindings.MATVERSE_FROZEN_CONTRACT_HASH ?? ""),
    MATVERSE_RUNTIME_ID: String(bindings.MATVERSE_RUNTIME_ID ?? ""),
    MATVERSE_BUILD_TIMESTAMP: String(bindings.MATVERSE_BUILD_TIMESTAMP ?? ""),
  };

  constructor(ctx: DurableObjectState<{}>, env: MatVerseEnv) {
    super(ctx, env);
    this.ensureSchema();
  }

  private ensureSchema(): void {
    const sql = this.ctx.storage.sql;
    sql.exec(`
      CREATE TABLE IF NOT EXISTS ledger (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        prev_hash TEXT NOT NULL,
        event_hash TEXT NOT NULL UNIQUE,
        event_json TEXT NOT NULL,
        decision TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS auth_nonces (
        principal_id TEXT NOT NULL,
        nonce TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        PRIMARY KEY(principal_id, nonce)
      );
      CREATE TABLE IF NOT EXISTS contract_artifacts (
        artifact_hash TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        version TEXT NOT NULL,
        content_json TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS institutional_intents (
        intent_id TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        requested_operation TEXT NOT NULL,
        target_kind TEXT NOT NULL,
        target_id TEXT NOT NULL,
        parameters_hash TEXT NOT NULL,
        source_json TEXT NOT NULL,
        intent_hash TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK(status IN ('PENDING_EVALUATION')),
        created_at TEXT NOT NULL,
        receipt_json TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_institutional_intents_principal
        ON institutional_intents(principal_id, created_at, intent_id);
      CREATE INDEX IF NOT EXISTS idx_institutional_intents_actor
        ON institutional_intents(actor_id, created_at, intent_id);
    `);
  }

  private rows(query: string, ...bindings: unknown[]): Record<string, unknown>[] {
    return this.ctx.storage.sql.exec(query, ...bindings).toArray() as Record<string, unknown>[];
  }

  private one(query: string, ...bindings: unknown[]): Record<string, unknown> | undefined {
    return this.rows(query, ...bindings)[0];
  }

  private async genesisCommitment(): Promise<string> {
    return sha256Hex(jcs({ ledger_head: GENESIS, events: 0 }));
  }

  private async currentProjectionReceipt(): Promise<string> {
    const last = this.one("SELECT event_hash FROM ledger ORDER BY seq DESC LIMIT 1");
    if (last && typeof last.event_hash === "string") return last.event_hash;
    return this.genesisCommitment();
  }

  private async appendLedgerEvent(event: JsonObject, decision: string): Promise<JsonObject> {
    const last = this.one("SELECT event_hash FROM ledger ORDER BY seq DESC LIMIT 1");
    const prevHash = last && typeof last.event_hash === "string" ? last.event_hash : GENESIS;
    const eventJson = jcs(event);
    const eventHash = await sha256Hex(prevHash + eventJson + decision);
    this.ctx.storage.sql.exec(
      "INSERT INTO ledger(prev_hash,event_hash,event_json,decision) VALUES (?,?,?,?)",
      prevHash,
      eventHash,
      eventJson,
      decision,
    );
    const seqRow = this.one("SELECT last_insert_rowid() AS seq");
    const seq = Number(seqRow?.seq ?? 0);
    if (!Number.isSafeInteger(seq) || seq < 1) throw new StateHttpError(500, "ledger sequence unavailable");
    return { seq, prev_hash: prevHash, event_hash: eventHash, decision };
  }

  private async consumeNonce(request: Request): Promise<Response> {
    const body = await parseJson(request);
    const principalId = stringValue(body.principal_id, "principal_id");
    const nonce = stringValue(body.nonce, "nonce");
    const expiresAt = integerValue(body.expires_at, "expires_at");
    const now = Math.floor(Date.now() / 1000);

    const consumed = await this.ctx.storage.transaction(async () => {
      this.ctx.storage.sql.exec("DELETE FROM auth_nonces WHERE expires_at < ?", now);
      const existing = this.one(
        "SELECT 1 AS present FROM auth_nonces WHERE principal_id=? AND nonce=?",
        principalId,
        nonce,
      );
      if (existing) return false;
      this.ctx.storage.sql.exec(
        "INSERT INTO auth_nonces(principal_id,nonce,expires_at) VALUES (?,?,?)",
        principalId,
        nonce,
        expiresAt,
      );
      return true;
    });
    return jsonResponse({ consumed });
  }

  private snapshot(): Response {
    const ledger = this.rows("SELECT seq,prev_hash,event_hash,event_json,decision FROM ledger ORDER BY seq");
    const contractArtifacts = this.rows(
      "SELECT artifact_hash,kind,version FROM contract_artifacts ORDER BY artifact_hash",
    );
    return jsonResponse({ ledger, contract_artifacts: contractArtifacts });
  }

  private getIntent(url: URL): Response {
    const intentId = stringValue(url.searchParams.get("intent_id"), "intent_id");
    const row = this.one("SELECT * FROM institutional_intents WHERE intent_id=?", intentId);
    if (!row) return jsonResponse({ detail: "institutional intent not found" }, 404);
    return jsonResponse(rowToIntent(row));
  }

  private listIntents(url: URL): Response {
    const principalId = stringValue(url.searchParams.get("principal_id"), "principal_id");
    const limit = Number(url.searchParams.get("limit") ?? "100");
    const offset = Number(url.searchParams.get("offset") ?? "0");
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 200) {
      throw new StateHttpError(400, "limit must be between 1 and 200");
    }
    if (!Number.isSafeInteger(offset) || offset < 0) {
      throw new StateHttpError(400, "offset must be >= 0");
    }
    const rows = this.rows(
      `SELECT * FROM institutional_intents
       WHERE principal_id=? OR actor_id=?
       ORDER BY created_at,intent_id
       LIMIT ? OFFSET ?`,
      principalId,
      principalId,
      limit,
      offset,
    );
    return jsonResponse({ intents: rows.map((row) => rowToIntent(row)) });
  }

  private async acceptIntent(request: Request): Promise<Response> {
    const body = await parseJson(request);
    const intent = objectValue(body.intent, "intent");
    const principalId = stringValue(body.principal_id, "principal_id");
    const allowDelegatedActor = body.allow_delegated_actor === true;
    const expectedLedgerHead = stringValue(body.expected_ledger_head, "expected_ledger_head");

    const intentId = stringValue(intent.intent_id, "intent.intent_id");
    const intentHash = stringValue(intent.intent_hash, "intent.intent_hash");
    const actorId = stringValue(intent.actor_id, "intent.actor_id");
    const operation = stringValue(intent.requested_operation, "intent.requested_operation");
    const target = objectValue(intent.target, "intent.target");
    const targetKind = stringValue(target.kind, "intent.target.kind");
    const targetId = stringValue(target.id, "intent.target.id");
    const parameters = objectValue(intent.parameters ?? {}, "intent.parameters");
    const source = objectValue(intent.source, "intent.source");
    const createdAt = stringValue(intent.created_at, "intent.created_at");

    if (actorId !== principalId && !allowDelegatedActor) {
      throw new StateHttpError(409, "intent actor_id must match authenticated principal unless delegated submit is authorized");
    }

    const canonicalIntent: JsonObject = { ...intent };
    delete canonicalIntent.intent_hash;
    const computedIntentHash = await sha256Hex(jcs(canonicalIntent));
    if (computedIntentHash !== intentHash) throw new StateHttpError(409, "intent_hash mismatch");
    const parametersHash = await sha256Hex(jcs(parameters));

    const result = await this.ctx.storage.transaction(async () => {
      const existing = this.one("SELECT * FROM institutional_intents WHERE intent_id=?", intentId);
      if (existing) {
        const existingActor = typeof existing.actor_id === "string" && existing.actor_id
          ? existing.actor_id
          : existing.principal_id;
        if (
          existing.intent_hash !== intentHash ||
          existing.principal_id !== principalId ||
          existingActor !== actorId
        ) {
          throw new StateHttpError(409, "intent_id collision or principal/actor mismatch");
        }
        return { ...rowToIntent(existing), idempotent: true };
      }

      const collision = this.one("SELECT intent_id FROM institutional_intents WHERE intent_hash=?", intentHash);
      if (collision) throw new StateHttpError(409, "intent_hash already registered under a different intent_id");

      const currentHead = await this.currentProjectionReceipt();
      if (currentHead !== expectedLedgerHead) {
        throw new StateHttpError(409, `intent source binding became stale before persistence; current_ledger_head=${currentHead}`);
      }

      const acceptedAt = new Date().toISOString();
      const event: JsonObject = {
        event_type: "INSTITUTIONAL_INTENT_ACCEPTED",
        intent_id: intentId,
        intent_hash: intentHash,
        requested_operation: operation,
        target_kind: targetKind,
        target_id: targetId,
        parameters_hash: parametersHash,
        parameter_persistence: "HASH_ONLY",
        principal_id: principalId,
        actor_id: actorId,
        projection_hash: stringValue(source.projection_hash, "intent.source.projection_hash"),
        source_commit: stringValue(source.commit_sha, "intent.source.commit_sha"),
        created_at: createdAt,
        accepted_at: acceptedAt,
        ledger_at: new Date().toISOString(),
        execution_decision: "HOLD",
        execution_reason: "intent commitment accepted; raw parameters require later authorized resubmission and evaluation",
      };
      const receipt = await this.appendLedgerEvent(event, "PASS");
      this.ctx.storage.sql.exec(
        `INSERT INTO institutional_intents(
          intent_id,principal_id,actor_id,requested_operation,target_kind,target_id,
          parameters_hash,source_json,intent_hash,status,created_at,receipt_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
        intentId,
        principalId,
        actorId,
        operation,
        targetKind,
        targetId,
        parametersHash,
        jcs(source),
        intentHash,
        "PENDING_EVALUATION",
        createdAt,
        jcs(receipt),
      );
      return {
        intent_id: intentId,
        intent_hash: intentHash,
        status: "PENDING_EVALUATION",
        principal_id: principalId,
        actor_id: actorId,
        requested_operation: operation,
        target: { kind: targetKind, id: targetId },
        parameters_hash: parametersHash,
        parameter_persistence: "HASH_ONLY",
        source,
        created_at: createdAt,
        receipt,
        idempotent: false,
        execution_decision: "HOLD",
      };
    });

    return jsonResponse(result);
  }

  async stateFetch(request: Request): Promise<Response> {
    try {
      const url = new URL(request.url);
      if (request.method === "POST" && url.pathname === "/v1/nonces/consume") return await this.consumeNonce(request);
      if (request.method === "GET" && url.pathname === "/v1/snapshot") return this.snapshot();
      if (request.method === "POST" && url.pathname === "/v1/intents/accept") return await this.acceptIntent(request);
      if (request.method === "GET" && url.pathname === "/v1/intents/item") return this.getIntent(url);
      if (request.method === "GET" && url.pathname === "/v1/intents/list") return this.listIntents(url);
      return jsonResponse({ detail: "state route not found" }, 404);
    } catch (error) {
      if (error instanceof StateHttpError) return jsonResponse({ detail: error.message }, error.status);
      console.error("durable institutional state failure", error);
      return jsonResponse({ detail: "durable institutional state failure" }, 500);
    }
  }
}

MatVerseContainer.outboundByHost = {
  [INTERNAL_STATE_HOST]: async (request, env, ctx) => {
    const typedEnv = env as unknown as MatVerseEnv;
    const id = typedEnv.MATVERSE_CONTAINER.idFromString(ctx.containerId);
    const stub = typedEnv.MATVERSE_CONTAINER.get(id);
    return stub.stateFetch(request);
  },
};

export default {
  async fetch(request: Request, env: MatVerseEnv): Promise<Response> {
    const container = env.MATVERSE_CONTAINER.getByName(PILOT_INSTANCE);
    return container.fetch(request);
  },
};
