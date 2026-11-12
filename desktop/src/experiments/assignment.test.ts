/**
 * Test suite for TypeScript experiment assignment and golden fixture parity.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it } from "node:test";
import { assignVariant, computeBucket } from "./assignment.ts";

interface GoldenFixture {
  experiment_key: string;
  variants: [string, number][];
  assignments: Record<string, string>;
}

describe("TypeScript experiment assignment", () => {
  it("computes exact boundary buckets matching SHA-256 slice", () => {
    const key = "test-boundary";
    assert.strictEqual(computeBucket(key, "shop-13669"), 0);
    assert.strictEqual(computeBucket(key, "shop-03286"), 3332);
    assert.strictEqual(computeBucket(key, "shop-09143"), 3333);
    assert.strictEqual(computeBucket(key, "shop-07949"), 4999);
    assert.strictEqual(computeBucket(key, "shop-05498"), 5000);
    assert.strictEqual(computeBucket(key, "shop-21318"), 6665);
    assert.strictEqual(computeBucket(key, "shop-13061"), 6666);
    assert.strictEqual(computeBucket(key, "shop-11965"), 9999);

    const threeWay: [string, number][] = [
      ["A", 3333],
      ["B", 3333],
      ["C", 3334],
    ];
    assert.strictEqual(assignVariant(key, "shop-13669", threeWay), "A");
    assert.strictEqual(assignVariant(key, "shop-03286", threeWay), "A");
    assert.strictEqual(assignVariant(key, "shop-09143", threeWay), "B");
    assert.strictEqual(assignVariant(key, "shop-21318", threeWay), "B");
    assert.strictEqual(assignVariant(key, "shop-13061", threeWay), "C");
    assert.strictEqual(assignVariant(key, "shop-11965", threeWay), "C");

    const twoWay: [string, number][] = [
      ["A", 5000],
      ["B", 5000],
    ];
    assert.strictEqual(assignVariant(key, "shop-07949", twoWay), "A");
    assert.strictEqual(assignVariant(key, "shop-05498", twoWay), "B");
  });

  it("throws on invalid inputs without silent fallbacks", () => {
    assert.throws(() => computeBucket("", "shop-1"), /non-empty string/);
    assert.throws(() => computeBucket("exp", ""), /non-empty string/);
    assert.throws(() => assignVariant("exp", "shop-1", []), /non-empty array/);
    assert.throws(
      () =>
        assignVariant("exp", "shop-1", [
          ["A", 5000],
          ["A", 5000],
        ]),
      /Duplicate variant name/,
    );
    assert.throws(
      () =>
        assignVariant("exp", "shop-1", [
          ["A", -1],
          ["B", 10001],
        ]),
      /non-negative integer/,
    );
    assert.throws(
      () =>
        assignVariant("exp", "shop-1", [
          ["A", 5000],
          ["B", 4999],
        ]),
      /must sum to exactly 10000/,
    );
  });

  it("never allocates variants with weight 0", () => {
    const variants: [string, number][] = [
      ["A", 5000],
      ["NEVER", 0],
      ["B", 5000],
    ];
    for (let i = 0; i < 500; i++) {
      const shop = `shop-${i.toString().padStart(5, "0")}`;
      const assigned = assignVariant("test-zero-weight", shop, variants);
      assert.notStrictEqual(assigned, "NEVER");
      assert.ok(assigned === "A" || assigned === "B");
    }
  });

  it("matches all 1,000 assignments from assignment_golden_v1.json", () => {
    const goldenPath = resolve(process.cwd(), "tests/fixtures/assignment_golden_v1.json");
    const rawData = readFileSync(goldenPath, "utf-8");
    const golden = JSON.parse(rawData) as GoldenFixture;

    assert.strictEqual(golden.experiment_key, "golden-v1");
    assert.strictEqual(Object.keys(golden.assignments).length, 1000);

    for (const [shopId, expectedVariant] of Object.entries(golden.assignments)) {
      const actual = assignVariant(golden.experiment_key, shopId, golden.variants);
      assert.strictEqual(
        actual,
        expectedVariant,
        `Mismatch for ${shopId}: expected ${expectedVariant}, got ${actual}`,
      );
    }
  });
});
