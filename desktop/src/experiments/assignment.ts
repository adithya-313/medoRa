/**
 * Deterministic experiment assignment engine using SHA-256 bucketing (TypeScript).
 */

import { createHash } from "node:crypto";

/**
 * Computes a deterministic bucket in [0, 9999] for an experiment and shop.
 *
 * @param experimentKey Unique identifier of the experiment.
 * @param shopId Unique identifier of the shop.
 * @returns An integer in the range [0, 9999].
 */
export function computeBucket(experimentKey: string, shopId: string): number {
  if (typeof experimentKey !== "string" || experimentKey.length === 0) {
    throw new TypeError("experimentKey must be a non-empty string");
  }
  if (typeof shopId !== "string" || shopId.length === 0) {
    throw new TypeError("shopId must be a non-empty string");
  }

  // Digest using SHA-256, taking the first 8 bytes as big-endian uint64.
  const digest = createHash("sha256").update(`${experimentKey}:${shopId}`).digest();
  const uint64 = digest.readBigUInt64BE(0);
  return Number(uint64 % 10000n);
}

/**
 * Assigns a shop to an experiment variant based on basis-point weights.
 *
 * @param experimentKey Unique identifier of the experiment.
 * @param shopId Unique identifier of the shop.
 * @param variants Array of [variantName, weightBasisPoints] tuples.
 * @returns The assigned variant name.
 */
export function assignVariant(
  experimentKey: string,
  shopId: string,
  variants: [string, number][],
): string {
  if (!Array.isArray(variants) || variants.length === 0) {
    throw new TypeError("variants must be a non-empty array of [name, weight] tuples");
  }

  const seenNames = new Set<string>();
  let totalWeight = 0;

  for (const item of variants) {
    if (!Array.isArray(item) || item.length !== 2) {
      throw new TypeError("Each variant must be a [name, weight] tuple");
    }
    const [name, weight] = item;
    if (typeof name !== "string" || name.length === 0) {
      throw new TypeError("Variant name must be a non-empty string");
    }
    if (seenNames.has(name)) {
      throw new Error(`Duplicate variant name: ${name}`);
    }
    seenNames.add(name);

    if (typeof weight !== "number" || !Number.isInteger(weight) || weight < 0) {
      throw new TypeError(`Variant weight must be a non-negative integer, got ${weight}`);
    }
    totalWeight += weight;
  }

  if (totalWeight !== 10000) {
    throw new Error(`Variant weights must sum to exactly 10000 basis points, got ${totalWeight}`);
  }

  const bucket = computeBucket(experimentKey, shopId);
  let cumulative = 0;
  for (const [name, weight] of variants) {
    cumulative += weight;
    // Weight 0 leaves cumulative unchanged, so bucket < cumulative cannot match.
    if (bucket < cumulative) {
      return name;
    }
  }

  throw new Error(`Could not assign variant for bucket ${bucket} with cumulative ${cumulative}`);
}
