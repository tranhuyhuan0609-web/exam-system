/**
 * Canonical JSON serialization and SHA-256 payload hashing shared with the Python backend.
 *
 * Matches Python's:
 *   json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
 *
 * IMPORTANT CONSTRAINT (Floating-point serialization):
 * Non-integer numbers (e.g., `1.0`, `0.5`, `NaN`, `Infinity`) serialize differently
 * between Python (`1.0`) and JavaScript (`1`). Autosave payloads MUST NOT contain
 * floating-point numbers; decimal answers (e.g. Part III short numeric) must be
 * serialized as strings (`"0,5"` or `"0.5"`). Non-integer numbers are rejected below.
 */

export class NonIntegerNumberError extends TypeError {
  constructor(value: number) {
    super(
      `Non-integer number ${String(value)} is forbidden in autosave payloads; ` +
        "serialize decimals as strings to preserve identical Python/JS payload hashes.",
    );
    this.name = "NonIntegerNumberError";
  }
}

export function canonicalJson(value: unknown): string {
  if (typeof value === "number") {
    if (!Number.isFinite(value) || !Number.isInteger(value)) {
      throw new NonIntegerNumberError(value);
    }
    return JSON.stringify(value);
  }
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (typeof value !== "object") {
    throw new TypeError(`Unsupported value type in canonicalJson: ${typeof value}`);
  }
  if (Array.isArray(value)) {
    return "[" + value.map((item) => canonicalJson(item)).join(",") + "]";
  }
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj)
    .filter((key) => obj[key] !== undefined)
    .sort();
  return "{" + keys.map((key) => JSON.stringify(key) + ":" + canonicalJson(obj[key])).join(",") + "}";
}

export async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export async function computePayloadHash(payload: unknown): Promise<string> {
  return sha256Hex(canonicalJson(payload));
}

