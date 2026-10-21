import assert from "node:assert/strict";
import test from "node:test";
import { activeHref } from "../lib/nav-active.mjs";

const HREFS = [
  "/dashboard",
  "/projects/new",
  "/projects",
  "/billing",
  "/account/team",
  "/account/api-keys",
  "/account",
];

test("an exact pathname activates its own href", () => {
  assert.equal(activeHref("/dashboard", HREFS), "/dashboard");
  assert.equal(activeHref("/projects", HREFS), "/projects");
  assert.equal(activeHref("/billing", HREFS), "/billing");
});

test("the longest matching prefix wins", () => {
  // /projects/new is a longer match than /projects.
  assert.equal(activeHref("/projects/new", HREFS), "/projects/new");
  // A nested project id falls back to /projects, not /projects/new.
  assert.equal(activeHref("/projects/abc", HREFS), "/projects");
  assert.equal(activeHref("/projects/abc/edit", HREFS), "/projects");
});

test("nested account routes prefer the specific section", () => {
  assert.equal(activeHref("/account/team", HREFS), "/account/team");
  assert.equal(activeHref("/account/api-keys", HREFS), "/account/api-keys");
  assert.equal(activeHref("/account", HREFS), "/account");
  assert.equal(activeHref("/account/profile", HREFS), "/account");
});

test("only path-segment boundaries match, not string prefixes", () => {
  assert.equal(activeHref("/projectsx", HREFS), null);
  assert.equal(activeHref("/billing-history", HREFS), null);
});

test("no match returns null", () => {
  assert.equal(activeHref("/unknown", HREFS), null);
  assert.equal(activeHref("/", HREFS), null);
});

test("a trailing slash on the pathname still matches", () => {
  assert.equal(activeHref("/projects/", HREFS), "/projects");
});

test("malformed input is handled gracefully", () => {
  assert.equal(activeHref(undefined, HREFS), null);
  assert.equal(activeHref("/projects", null), null);
});
