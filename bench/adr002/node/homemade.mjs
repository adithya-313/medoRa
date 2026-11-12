/**
 * Homemade feature flag variant assignment in Node.js.
 */
import crypto from 'node:crypto';

/**
 * Deterministically assign a shop to experiment variant A or B.
 *
 * @param {string} experimentKey - Experiment identifier.
 * @param {string} shopId - Shop identifier.
 * @param {number} splitPercent - Percentage allocated to variant A (0-100).
 * @returns {'A' | 'B'}
 */
export function assignVariant(experimentKey, shopId, splitPercent) {
  if (splitPercent < 0 || splitPercent > 100) {
    throw new RangeError(`splitPercent must be in range 0..100, got ${splitPercent}`);
  }
  const hash = crypto.createHash('sha256').update(`${experimentKey}:${shopId}`, 'utf8').digest();
  const first8Bytes = hash.subarray(0, 8);
  const intVal = first8Bytes.readBigUInt64BE(0);
  const bucket = Number(intVal % 10000n);
  return bucket < splitPercent * 100 ? 'A' : 'B';
}

// When invoked directly from CLI, evaluate 10,000 shops and print JSON array
const currentPath = process.argv[1].replace(/\\/g, '/');
if (import.meta.url.endsWith(currentPath) || import.meta.url === `file:///${currentPath}`) {
  const expKey = process.argv[2] || 'adr002-aa';
  const split = parseInt(process.argv[3] || '50', 10);
  const results = [];
  for (let i = 1; i <= 10000; i++) {
    const shopId = `shop-${String(i).padStart(5, '0')}`;
    results.push(assignVariant(expKey, shopId, split));
  }
  process.stdout.write(JSON.stringify(results));
}
