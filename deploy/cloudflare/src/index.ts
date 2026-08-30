import { Container } from "@cloudflare/containers";
export { ContainerProxy } from "@cloudflare/containers";
import { env as globalEnv } from "cloudflare:workers";


type JsonObject = Record<string, unknown>;

type MatVerseEnv = {
  MATVERSE_CONTAINER: DurableObjectNamespace<MatVerseContainer>;
  MATVERSE_AUTH_MODE?: string;
  MATVERSE_BOOTSTRAP_ROOT_JSON?: string;
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
const ED25519_AUTH_SCHEME = "ED25519-PUBLIC-KEY-V1";
const IDENTIFIER_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const PUBLIC_KEY_RE = /^[0-9a-f]{64}$/;
const KEY_ID_RE = /^ed25519:[0-9a-f]{64}$/;
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

function identifierValue(value: unknown, name: string): string {
  const result = stringValue(value, name);
  if (!IDENTIFIER_RE.test(result)) throw new StateHttpError(400, `${name} has invalid identifier syntax`);
  return result;
}

function integerValue(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new StateHttpError(400, `${name} must be a safe integer`);
  }
  return value;
}

function publicKeyValue(value: unknown, name: string): string {
  const result = stringValue(value, name);
  if (!PUBLIC_KEY_RE.test(result)) throw new StateHttpError(400, `${name} must be 32-byte lowercase Ed25519 raw hex`);
  return result;
}

function capabilitiesValue(value: unknown, name: string): string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 128) {
    throw new StateHttpError(400, `${name} must be a non-empty array with at most 128 entries`);
  }
  const capabilities = value.map((item, index) => {
    const capability = stringValue(item, `${name}[${index}]`);
    if (capability.length > 128 || capability.trim() !== capability) {
      throw new StateHttpError(400, `${name}[${index}] must be canonical and <= 128 characters`);
    }
    return capability;
  });
  const canonical = [...new Set(capabilities)].sort();
  if (canonical.length !== capabilities.length || canonical.some((item, index) => item !== capabilities[index])) {
    throw new StateHttpError(400, `${name} must be unique and lexicographically sorted`);
  }
  return capabilities;
}

function requireExactKeys(value: JsonObject, expected: string[], name: string): void {
  const actual = Object.keys(value).sort();
  const canonicalExpected = [...expected].sort();
  if (actual.length !== canonicalExpected.length || actual.some((item, index) => item !== canonicalExpected[index])) {
    throw new StateHttpError(400, `${name} must contain exactly: ${canonicalExpected.join(",")}`);
  }
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

function hexBytes(value: string): Uint8Array {
  if (value.length % 2 !== 0 || !/^[0-9a-f]+$/.test(value)) throw new StateHttpError(400, "hex value is invalid");
  const result = new Uint8Array(value.length / 2);
  for (let index = 0; index < value.length; index += 2) {
    result[index / 2] = Number.parseInt(value.slice(index, index + 2), 16);
  }
  return result;
}

async function principalKeyId(publicKeyHex: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", hexBytes(publicKeyHex));
  const value = [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `ed25519:${value}`;
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

function decodeCapabilities(value: unknown): string[] {
  if (typeof value !== "string") throw new StateHttpError(500, "capabilities_json is not stored as JSON text");
  try {
    return capabilitiesValue(JSON.parse(value), "capabilities_json");
  } catch (error) {
    if (error instanceof StateHttpError) throw error;
    throw new StateHttpError(500, "capabilities_json contains invalid JSON");
  }
}

function nullableInteger(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  return integerValue(value, "stored integer");
}

function nullableString(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  return stringValue(value, "stored string");
}

function rowToPrincipal(row: Record<string, unknown>): JsonObject {
  return {
    principal_id: stringValue(row.principal_id, "principal_id"),
    capabilities: decodeCapabilities(row.capabilities_json),
    status: stringValue(row.status, "status"),
    created_by: stringValue(row.created_by, "created_by"),
    created_at: stringValue(row.created_at, "created_at"),
    revoked_at: nullableInteger(row.revoked_at),
    revocation_reason: nullableString(row.revocation_reason),
  };
}

function rowToPrincipalKey(row: Record<string, unknown>): JsonObject {
  return {
    principal_id: stringValue(row.principal_id, "principal_id"),
    key_id: stringValue(row.key_id, "key_id"),
    public_key_hex: stringValue(row.public_key_hex, "public_key_hex"),
    valid_from: integerValue(row.valid_from, "valid_from"),
    valid_until: integerValue(row.valid_until, "valid_until"),
    previous_key_id: nullableString(row.previous_key_id),
    registered_by: stringValue(row.registered_by, "registered_by"),
    registered_at: stringValue(row.registered_at, "registered_at"),
    revoked_at: nullableInteger(row.revoked_at),
    revocation_reason: nullableString(row.revocation_reason),
  };
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

function pathSegments(url: URL): string[] {
  try {
    return url.pathname.split("/").filter(Boolean).map((item) => decodeURIComponent(item));
  } catch {
    throw new StateHttpError(400, "request path contains invalid percent encoding");
  }
}

export class MatVerseContainer extends Container {
  defaultPort = 8000;
  sleepAfter = "10m";
  enableInternet = false;
  pingEndpoint = "/health";
  envVars = {
    MATVERSE_INSTITUTIONAL_STATE_URL: `http://${INTERNAL_STATE_HOST}`,
    MATVERSE_AUTH_MODE: String(bindings.MATVERSE_AUTH_MODE ?? ""),
    MATVERSE_BOOTSTRAP_ROOT_JSON: String(bindings.MATVERSE_BOOTSTRAP_ROOT_JSON ?? ""),
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
      CREATE TABLE IF NOT EXISTS auth_principals (
        principal_id TEXT PRIMARY KEY,
        capabilities_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('ACTIVE','REVOKED')),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        revoked_at INTEGER,
        revocation_reason TEXT
      );
      CREATE TABLE IF NOT EXISTS auth_principal_keys (
        key_id TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL,
        public_key_hex TEXT NOT NULL UNIQUE,
        valid_from INTEGER NOT NULL,
        valid_until INTEGER NOT NULL,
        previous_key_id TEXT,
        registered_by TEXT NOT NULL,
        registered_at TEXT NOT NULL,
        revoked_at INTEGER,
        revocation_reason TEXT,
        FOREIGN KEY(principal_id) REFERENCES auth_principals(principal_id),
        FOREIGN KEY(previous_key_id) REFERENCES auth_principal_keys(key_id)
      );
      CREATE INDEX IF NOT EXISTS idx_auth_principal_keys_principal
        ON auth_principal_keys(principal_id, valid_from, key_id);
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

  private async ensureBootstrapRoot(): Promise<void> {
    const existing = this.one("SELECT COUNT(*) AS count FROM auth_principals");
    if (Number(existing?.count ?? 0) > 0) return;
    const raw = String(bindings.MATVERSE_BOOTSTRAP_ROOT_JSON ?? "").trim();
    if (!raw) throw new StateHttpError(503, "asymmetric principal registry is empty and MATVERSE_BOOTSTRAP_ROOT_JSON is not configured");

    let manifest: JsonObject;
    try {
      manifest = objectValue(JSON.parse(raw), "MATVERSE_BOOTSTRAP_ROOT_JSON");
    } catch (error) {
      if (error instanceof StateHttpError) throw error;
      throw new StateHttpError(503, "MATVERSE_BOOTSTRAP_ROOT_JSON must be valid JSON");
    }
    requireExactKeys(
      manifest,
      ["principal_id", "public_key_hex", "capabilities", "valid_from", "valid_until"],
      "MATVERSE_BOOTSTRAP_ROOT_JSON",
    );
    const principalId = identifierValue(manifest.principal_id, "bootstrap principal_id");
    const publicKeyHex = publicKeyValue(manifest.public_key_hex, "bootstrap public_key_hex");
    const capabilities = capabilitiesValue(manifest.capabilities, "bootstrap capabilities");
    const validFrom = integerValue(manifest.valid_from, "bootstrap valid_from");
    const validUntil = integerValue(manifest.valid_until, "bootstrap valid_until");
    if (validFrom < 0 || validUntil <= validFrom) throw new StateHttpError(503, "bootstrap validity window is invalid");
    const keyId = await principalKeyId(publicKeyHex);
    const createdAt = new Date().toISOString();

    await this.ctx.storage.transaction(async () => {
      const recheck = this.one("SELECT COUNT(*) AS count FROM auth_principals");
      if (Number(recheck?.count ?? 0) > 0) return;
      this.ctx.storage.sql.exec(
        "INSERT INTO auth_principals(principal_id,capabilities_json,status,created_by,created_at) VALUES (?,?,?,?,?)",
        principalId,
        jcs(capabilities),
        "ACTIVE",
        "EXTERNAL_BOOTSTRAP_TRUST_ANCHOR",
        createdAt,
      );
      this.ctx.storage.sql.exec(
        `INSERT INTO auth_principal_keys(
          key_id,principal_id,public_key_hex,valid_from,valid_until,previous_key_id,
          registered_by,registered_at
        ) VALUES (?,?,?,?,?,?,?,?)`,
        keyId,
        principalId,
        publicKeyHex,
        validFrom,
        validUntil,
        null,
        "EXTERNAL_BOOTSTRAP_TRUST_ANCHOR",
        createdAt,
      );
      await this.appendLedgerEvent(
        {
          event_type: "AUTH_ROOT_PRINCIPAL_BOOTSTRAPPED",
          auth_scheme: ED25519_AUTH_SCHEME,
          principal_id: principalId,
          key_id: keyId,
          public_key_sha256: keyId.slice("ed25519:".length),
          capabilities,
          valid_from: validFrom,
          valid_until: validUntil,
          private_material_present: false,
          created_by: "EXTERNAL_BOOTSTRAP_TRUST_ANCHOR",
          created_at: createdAt,
        },
        "PASS",
      );
    });
  }

  private principalEffectivelyRevoked(row: Record<string, unknown>, now: number): boolean {
    if (row.status !== "REVOKED") return false;
    const revokedAt = nullableInteger(row.revoked_at);
    return revokedAt === null || now >= revokedAt;
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

  private async authCredential(principalId: string, keyId: string): Promise<Response> {
    await this.ensureBootstrapRoot();
    identifierValue(principalId, "principal_id");
    if (!KEY_ID_RE.test(keyId)) throw new StateHttpError(400, "key_id has invalid syntax");
    const principal = this.one("SELECT * FROM auth_principals WHERE principal_id=?", principalId);
    const key = this.one("SELECT * FROM auth_principal_keys WHERE key_id=?", keyId);
    if (!principal || !key || key.principal_id !== principalId) {
      return jsonResponse({ detail: "principal credential not found" }, 404);
    }
    return jsonResponse({
      auth_scheme: ED25519_AUTH_SCHEME,
      principal: rowToPrincipal(principal),
      key: rowToPrincipalKey(key),
      private_material_present: false,
    });
  }

  private async authPrincipal(principalId: string): Promise<Response> {
    await this.ensureBootstrapRoot();
    identifierValue(principalId, "principal_id");
    const principal = this.one("SELECT * FROM auth_principals WHERE principal_id=?", principalId);
    if (!principal) return jsonResponse({ detail: "principal not found" }, 404);
    const keys = this.rows(
      "SELECT * FROM auth_principal_keys WHERE principal_id=? ORDER BY valid_from,key_id",
      principalId,
    );
    return jsonResponse({ principal: rowToPrincipal(principal), keys: keys.map((row) => rowToPrincipalKey(row)) });
  }

  private async registerPrincipal(principalId: string, request: Request): Promise<Response> {
    await this.ensureBootstrapRoot();
    identifierValue(principalId, "principal_id");
    const body = await parseJson(request);
    requireExactKeys(body, ["capabilities", "public_key_hex", "valid_from", "valid_until", "actor_id"], "principal registration");
    const actorId = identifierValue(body.actor_id, "actor_id");
    const capabilities = capabilitiesValue(body.capabilities, "capabilities");
    const publicKeyHex = publicKeyValue(body.public_key_hex, "public_key_hex");
    const validFrom = integerValue(body.valid_from, "valid_from");
    const validUntil = integerValue(body.valid_until, "valid_until");
    if (validFrom < 0 || validUntil <= validFrom) throw new StateHttpError(409, "principal key validity window is invalid");
    const keyId = await principalKeyId(publicKeyHex);

    const result = await this.ctx.storage.transaction(async () => {
      if (this.one("SELECT 1 AS present FROM auth_principals WHERE principal_id=?", principalId)) {
        throw new StateHttpError(409, "principal_id already exists");
      }
      if (this.one("SELECT 1 AS present FROM auth_principal_keys WHERE key_id=?", keyId)) {
        throw new StateHttpError(409, "principal key already registered");
      }
      const createdAt = new Date().toISOString();
      this.ctx.storage.sql.exec(
        "INSERT INTO auth_principals(principal_id,capabilities_json,status,created_by,created_at) VALUES (?,?,?,?,?)",
        principalId,
        jcs(capabilities),
        "ACTIVE",
        actorId,
        createdAt,
      );
      this.ctx.storage.sql.exec(
        `INSERT INTO auth_principal_keys(
          key_id,principal_id,public_key_hex,valid_from,valid_until,previous_key_id,
          registered_by,registered_at
        ) VALUES (?,?,?,?,?,?,?,?)`,
        keyId,
        principalId,
        publicKeyHex,
        validFrom,
        validUntil,
        null,
        actorId,
        createdAt,
      );
      const receipt = await this.appendLedgerEvent(
        {
          event_type: "AUTH_PRINCIPAL_REGISTERED",
          auth_scheme: ED25519_AUTH_SCHEME,
          principal_id: principalId,
          key_id: keyId,
          capabilities,
          valid_from: validFrom,
          valid_until: validUntil,
          created_by: actorId,
          created_at: createdAt,
        },
        "PASS",
      );
      return { receipt, createdAt };
    });

    const principal = this.one("SELECT * FROM auth_principals WHERE principal_id=?", principalId);
    const key = this.one("SELECT * FROM auth_principal_keys WHERE key_id=?", keyId);
    if (!principal || !key) throw new StateHttpError(500, "principal registration readback failed");
    return jsonResponse({ principal: rowToPrincipal(principal), key: rowToPrincipalKey(key), receipt: result.receipt });
  }

  private async rotatePrincipalKey(principalId: string, previousKeyId: string, request: Request): Promise<Response> {
    await this.ensureBootstrapRoot();
    identifierValue(principalId, "principal_id");
    if (!KEY_ID_RE.test(previousKeyId)) throw new StateHttpError(400, "previous_key_id has invalid syntax");
    const body = await parseJson(request);
    requireExactKeys(body, ["public_key_hex", "valid_from", "valid_until", "actor_id"], "principal key rotation");
    const actorId = identifierValue(body.actor_id, "actor_id");
    const publicKeyHex = publicKeyValue(body.public_key_hex, "public_key_hex");
    const validFrom = integerValue(body.valid_from, "valid_from");
    const validUntil = integerValue(body.valid_until, "valid_until");
    if (validFrom < 0 || validUntil <= validFrom) throw new StateHttpError(409, "rotated key validity window is invalid");
    const keyId = await principalKeyId(publicKeyHex);
    const now = Math.floor(Date.now() / 1000);

    const receipt = await this.ctx.storage.transaction(async () => {
      const principal = this.one("SELECT * FROM auth_principals WHERE principal_id=?", principalId);
      if (!principal) throw new StateHttpError(404, "principal not found");
      if (this.principalEffectivelyRevoked(principal, now)) throw new StateHttpError(409, "principal is revoked");
      const previous = this.one("SELECT * FROM auth_principal_keys WHERE key_id=?", previousKeyId);
      if (!previous || previous.principal_id !== principalId) throw new StateHttpError(404, "previous principal key not found");
      const previousRevokedAt = nullableInteger(previous.revoked_at);
      if (previousRevokedAt !== null && now >= previousRevokedAt) throw new StateHttpError(409, "cannot rotate from a revoked key");
      if (validFrom < integerValue(previous.valid_from, "previous valid_from")) {
        throw new StateHttpError(409, "rotated key valid_from cannot predate predecessor");
      }
      if (this.one("SELECT 1 AS present FROM auth_principal_keys WHERE key_id=?", keyId)) {
        throw new StateHttpError(409, "principal key already registered");
      }
      const registeredAt = new Date().toISOString();
      this.ctx.storage.sql.exec(
        `INSERT INTO auth_principal_keys(
          key_id,principal_id,public_key_hex,valid_from,valid_until,previous_key_id,
          registered_by,registered_at
        ) VALUES (?,?,?,?,?,?,?,?)`,
        keyId,
        principalId,
        publicKeyHex,
        validFrom,
        validUntil,
        previousKeyId,
        actorId,
        registeredAt,
      );
      return this.appendLedgerEvent(
        {
          event_type: "AUTH_PRINCIPAL_KEY_ROTATED",
          auth_scheme: ED25519_AUTH_SCHEME,
          principal_id: principalId,
          previous_key_id: previousKeyId,
          key_id: keyId,
          valid_from: validFrom,
          valid_until: validUntil,
          registered_by: actorId,
          registered_at: registeredAt,
        },
        "PASS",
      );
    });
    const key = this.one("SELECT * FROM auth_principal_keys WHERE key_id=?", keyId);
    if (!key) throw new StateHttpError(500, "principal key rotation readback failed");
    return jsonResponse({ key: rowToPrincipalKey(key), receipt });
  }

  private async revokePrincipalKey(principalId: string, keyId: string, request: Request): Promise<Response> {
    await this.ensureBootstrapRoot();
    identifierValue(principalId, "principal_id");
    if (!KEY_ID_RE.test(keyId)) throw new StateHttpError(400, "key_id has invalid syntax");
    const body = await parseJson(request);
    requireExactKeys(body, ["effective_at", "reason", "actor_id"], "principal key revocation");
    const actorId = identifierValue(body.actor_id, "actor_id");
    const effectiveAt = integerValue(body.effective_at, "effective_at");
    const reason = stringValue(body.reason, "reason").trim();
    if (effectiveAt < 0 || !reason || reason.length > 512) throw new StateHttpError(409, "invalid key revocation metadata");

    const result = await this.ctx.storage.transaction(async () => {
      const row = this.one("SELECT * FROM auth_principal_keys WHERE key_id=?", keyId);
      if (!row || row.principal_id !== principalId) throw new StateHttpError(404, "principal key not found");
      const existingRevokedAt = nullableInteger(row.revoked_at);
      if (existingRevokedAt !== null) {
        if (existingRevokedAt === effectiveAt && row.revocation_reason === reason) {
          return { key: rowToPrincipalKey(row), receipt: null, idempotent: true };
        }
        throw new StateHttpError(409, "principal key already revoked with different metadata");
      }
      const replacement = this.one(
        `SELECT key_id FROM auth_principal_keys
         WHERE principal_id=? AND key_id<>? AND valid_from<=? AND valid_until>?
           AND (revoked_at IS NULL OR revoked_at>?)
         LIMIT 1`,
        principalId,
        keyId,
        effectiveAt,
        effectiveAt,
        effectiveAt,
      );
      if (!replacement) throw new StateHttpError(409, "cannot revoke the principal's last usable key; rotate first or revoke the principal");
      this.ctx.storage.sql.exec(
        "UPDATE auth_principal_keys SET revoked_at=?,revocation_reason=? WHERE key_id=?",
        effectiveAt,
        reason,
        keyId,
      );
      const observedAt = new Date().toISOString();
      const receipt = await this.appendLedgerEvent(
        {
          event_type: "AUTH_PRINCIPAL_KEY_REVOKED",
          principal_id: principalId,
          key_id: keyId,
          revoked_at: effectiveAt,
          revocation_reason: reason,
          revoked_by: actorId,
          observed_at: observedAt,
        },
        "PASS",
      );
      const updated = this.one("SELECT * FROM auth_principal_keys WHERE key_id=?", keyId);
      if (!updated) throw new StateHttpError(500, "revoked key readback failed");
      return { key: rowToPrincipalKey(updated), receipt, idempotent: false };
    });
    return jsonResponse(result);
  }

  private async revokePrincipal(principalId: string, request: Request): Promise<Response> {
    await this.ensureBootstrapRoot();
    identifierValue(principalId, "principal_id");
    const body = await parseJson(request);
    requireExactKeys(body, ["effective_at", "reason", "actor_id"], "principal revocation");
    const actorId = identifierValue(body.actor_id, "actor_id");
    const effectiveAt = integerValue(body.effective_at, "effective_at");
    const reason = stringValue(body.reason, "reason").trim();
    if (effectiveAt < 0 || !reason || reason.length > 512) throw new StateHttpError(409, "invalid principal revocation metadata");

    const result = await this.ctx.storage.transaction(async () => {
      const row = this.one("SELECT * FROM auth_principals WHERE principal_id=?", principalId);
      if (!row) throw new StateHttpError(404, "principal not found");
      if (row.status === "REVOKED") {
        const storedAt = nullableInteger(row.revoked_at);
        if (storedAt === effectiveAt && row.revocation_reason === reason) {
          return { principal: rowToPrincipal(row), receipt: null, idempotent: true };
        }
        throw new StateHttpError(409, "principal already revoked with different metadata");
      }
      this.ctx.storage.sql.exec(
        "UPDATE auth_principals SET status='REVOKED',revoked_at=?,revocation_reason=? WHERE principal_id=?",
        effectiveAt,
        reason,
        principalId,
      );
      const receipt = await this.appendLedgerEvent(
        {
          event_type: "AUTH_PRINCIPAL_REVOKED",
          principal_id: principalId,
          revoked_at: effectiveAt,
          revocation_reason: reason,
          revoked_by: actorId,
          observed_at: new Date().toISOString(),
        },
        "PASS",
      );
      const updated = this.one("SELECT * FROM auth_principals WHERE principal_id=?", principalId);
      if (!updated) throw new StateHttpError(500, "revoked principal readback failed");
      return { principal: rowToPrincipal(updated), receipt, idempotent: false };
    });
    return jsonResponse(result);
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
      const segments = pathSegments(url);
      if (request.method === "POST" && url.pathname === "/v1/nonces/consume") return await this.consumeNonce(request);
      if (request.method === "GET" && segments.length === 5 && segments[0] === "v1" && segments[1] === "auth" && segments[2] === "credentials") {
        return await this.authCredential(segments[3], segments[4]);
      }
      if (segments.length >= 4 && segments[0] === "v1" && segments[1] === "auth" && segments[2] === "principals") {
        const principalId = segments[3];
        if (request.method === "GET" && segments.length === 4) return await this.authPrincipal(principalId);
        if (request.method === "POST" && segments.length === 4) return await this.registerPrincipal(principalId, request);
        if (request.method === "POST" && segments.length === 7 && segments[4] === "keys" && segments[6] === "rotate") {
          return await this.rotatePrincipalKey(principalId, segments[5], request);
        }
        if (request.method === "POST" && segments.length === 7 && segments[4] === "keys" && segments[6] === "revoke") {
          return await this.revokePrincipalKey(principalId, segments[5], request);
        }
        if (request.method === "POST" && segments.length === 5 && segments[4] === "revoke") {
          return await this.revokePrincipal(principalId, request);
        }
      }
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