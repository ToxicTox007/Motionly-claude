import { describe, expect, it } from "vitest";
import {
  PROJECT_PAGE_SIZE,
  pageCountFor,
  parseProjectListQuery,
  projectListApiPath,
  projectListHref,
} from "@/lib/project-list-query";

describe("parseProjectListQuery", () => {
  it("defaults to the newest projects, first page, no search", () => {
    expect(parseProjectListQuery({})).toEqual({
      q: "",
      sort: "updated_desc",
      page: 1,
    });
  });

  it("reads q, sort and page from the URL", () => {
    expect(
      parseProjectListQuery({ q: "  launch ", sort: "title_asc", page: "3" }),
    ).toEqual({ q: "launch", sort: "title_asc", page: 3 });
  });

  it("ignores unknown sorts, bad pages and repeated parameters", () => {
    expect(
      parseProjectListQuery({ sort: "created", page: "-2" }),
    ).toMatchObject({ sort: "updated_desc", page: 1 });
    expect(parseProjectListQuery({ page: "abc" }).page).toBe(1);
    expect(parseProjectListQuery({ page: "2.5" }).page).toBe(1);
    expect(parseProjectListQuery({ q: ["a", "b"] }).q).toBe("a");
  });

  it("caps the search at the API's 120 characters", () => {
    expect(parseProjectListQuery({ q: "x".repeat(200) }).q).toHaveLength(120);
  });
});

describe("projectListApiPath", () => {
  it("turns the page into limit and offset", () => {
    expect(projectListApiPath({ q: "", sort: "updated_desc", page: 3 })).toBe(
      `/projects?limit=${PROJECT_PAGE_SIZE}&offset=40&sort=updated_desc`,
    );
  });

  it("encodes the search", () => {
    expect(
      projectListApiPath({ q: "50% off & more", sort: "title_asc", page: 1 }),
    ).toBe("/projects?limit=20&offset=0&sort=title_asc&q=50%25+off+%26+more");
  });
});

describe("projectListHref", () => {
  it("omits defaults so the plain list keeps a clean URL", () => {
    expect(projectListHref({ q: "", sort: "updated_desc", page: 1 })).toBe(
      "/projects",
    );
  });

  it("keeps the search and sort when changing page", () => {
    expect(projectListHref({ q: "reel", sort: "title_asc", page: 2 })).toBe(
      "/projects?q=reel&sort=title_asc&page=2",
    );
  });
});

describe("pageCountFor", () => {
  it("rounds up and is at least one", () => {
    expect(pageCountFor(0)).toBe(1);
    expect(pageCountFor(20)).toBe(1);
    expect(pageCountFor(21)).toBe(2);
    expect(pageCountFor(57)).toBe(3);
  });
});
