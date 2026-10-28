// Every hand-written `.d.mts` declares exactly the exports of its sibling
// `.mjs` (FE-32). TypeScript trusts the declaration file, so an export added,
// renamed or removed on one side only would otherwise go unnoticed until it
// fails at runtime.
import assert from "node:assert/strict";
import { existsSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";
import ts from "typescript";

const webDir = new URL("..", import.meta.url).pathname;

function moduleFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) out.push(...moduleFiles(path));
    else if (entry.endsWith(".mjs")) out.push(path);
  }
  return out;
}

const pairs = ["lib", "components"]
  .flatMap((dir) => moduleFiles(join(webDir, dir)))
  .map((mjs) => [mjs, mjs.replace(/\.mjs$/, ".d.mts")])
  .filter(([, dmts]) => existsSync(dmts));

// One program over both sides, so the checker resolves `export *`, re-exports
// and `export default` the same way for code and declarations.
const program = ts.createProgram(pairs.flat(), {
  allowJs: true,
  checkJs: false,
  noEmit: true,
  module: ts.ModuleKind.NodeNext,
  moduleResolution: ts.ModuleResolutionKind.NodeNext,
  target: ts.ScriptTarget.ES2022,
});
const checker = program.getTypeChecker();

function exportNames(file) {
  const source = program.getSourceFile(file);
  assert.ok(source, `${file} is part of the program`);
  const symbol = checker.getSymbolAtLocation(source);
  if (!symbol) return [];
  // Runtime exports only: a .d.mts may also declare types (interfaces, type
  // aliases) that have no JavaScript counterpart.
  return checker
    .getExportsOfModule(symbol)
    .filter((item) => {
      const target =
        item.flags & ts.SymbolFlags.Alias
          ? checker.getAliasedSymbol(item)
          : item;
      return (target.flags & ts.SymbolFlags.Value) !== 0;
    })
    .map((item) => item.getName())
    .sort();
}

test("there are .mjs modules with hand-written declarations to check", () => {
  assert.ok(pairs.length >= 20, `found ${pairs.length} pairs`);
});

for (const [mjs, dmts] of pairs) {
  test(`${relative(webDir, dmts)} declares the exports of its .mjs`, () => {
    assert.deepEqual(exportNames(dmts), exportNames(mjs));
  });
}
