import { describe, expect, it } from "vitest";

import vectors from "../__fixtures__/payload-hash-vectors.json";
import {
  NonIntegerNumberError,
  canonicalJson,
  computePayloadHash,
} from "../canonical-json.js";

describe("canonicalJson and computePayloadHash", () => {
  it("contains at least 20 shared payload vectors", () => {
    expect(vectors.length).toBeGreaterThanOrEqual(20);
  });

  it.each(vectors)(
    "matches Python canonical_json and sha256 for vector: $name",
    async ({ payload, expected_canonical_json, expected_sha256 }) => {
      expect(canonicalJson(payload)).toBe(expected_canonical_json);
      await expect(computePayloadHash(payload)).resolves.toBe(expected_sha256);
    },
  );

  it.each([1.5, 0.25, -3.14, 0.1, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY])(
    "rejects non-integer floating-point or non-finite number: %s",
    async (badNumber) => {
      expect(() => canonicalJson({ val: badNumber })).toThrow(NonIntegerNumberError);
      await expect(computePayloadHash({ val: badNumber })).rejects.toThrow(NonIntegerNumberError);
    },
  );
});

