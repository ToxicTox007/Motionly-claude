// Every page and loading screen sets the document title through PageTitle
// (directly, or through AppShell or LegalPage), and the root layout does not
// set one through Next metadata. React keeps a rendered <title> in <head> while
// its component is mounted; Next's streamed metadata title was observed to
// disappear after a server-action redirect and some client navigations.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";

const webDir = new URL("..", import.meta.url).pathname;

function files(dir, pattern) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return files(path, pattern);
    return pattern.test(name) ? [path] : [];
  });
}

test("every page and loading screen sets the document title", () => {
  const missing = files(
    join(webDir, "app"),
    /^(page|loading|global-error)\.tsx$/,
  )
    .filter(
      (path) =>
        !/<(PageTitle|AppShell|LegalPage)\b/.test(readFileSync(path, "utf8")),
    )
    .map((path) => relative(webDir, path));

  assert.deepEqual(missing, []);
});

test("titles name the page and end with the product name", () => {
  const source = readFileSync(
    join(webDir, "components/page-title.tsx"),
    "utf8",
  );
  assert.match(
    source,
    /<title>\{title \? `\$\{title\} · Motionly` : "Motionly"\}<\/title>/,
  );
  assert.match(
    readFileSync(join(webDir, "components/app-shell.tsx"), "utf8"),
    /<PageTitle title=\{title\} \/>/,
  );
  assert.match(
    readFileSync(join(webDir, "components/legal-page.tsx"), "utf8"),
    /<PageTitle title=\{title\} \/>/,
  );
});

test("the root layout leaves the title to the pages", () => {
  const layout = readFileSync(join(webDir, "app/layout.tsx"), "utf8");
  const metadata = layout.slice(
    layout.indexOf("export const metadata"),
    layout.indexOf("};", layout.indexOf("export const metadata")),
  );
  assert.doesNotMatch(metadata, /\btitle:/);
});

test("the project page titles every phase with the project's name", () => {
  const page = readFileSync(
    join(webDir, "app/(app)/projects/[projectId]/page.tsx"),
    "utf8",
  );
  // Upload, processing and editor; "not found" uses AppShell.
  assert.equal(page.split("<PageTitle title={project.title} />").length - 1, 3);
});
