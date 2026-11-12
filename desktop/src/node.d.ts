/**
 * Minimal ambient type declarations for Node.js built-in modules used in desktop and tests.
 */

declare module "node:crypto" {
  export interface HashBuffer extends Uint8Array {
    readBigUInt64BE(offset?: number): bigint;
  }
  export interface Hash {
    update(data: string | Uint8Array): this;
    digest(): HashBuffer;
  }
  export function createHash(algorithm: string): Hash;
}

declare module "node:fs" {
  export function readFileSync(path: string, encoding: string): string;
}

declare module "node:path" {
  export function resolve(...paths: string[]): string;
}

declare module "node:test" {
  export function describe(name: string, fn: () => void): void;
  export function it(name: string, fn: () => void | Promise<void>): void;
}

declare module "node:assert/strict" {
  export function strictEqual<T>(actual: unknown, expected: T, message?: string | Error): void;
  export function notStrictEqual<T>(actual: unknown, expected: T, message?: string | Error): void;
  export function throws(fn: () => unknown, expected?: RegExp | ((err: unknown) => boolean), message?: string): void;
  export function ok(value: unknown, message?: string | Error): void;
}

declare namespace NodeJS {
  interface Process {
    cwd(): string;
  }
}

declare const process: NodeJS.Process;
