// The projects list state (search, sort, page) lives in the URL, so it can be
// shared, bookmarked and restored with back/forward. The API does the search
// and paging (GET /projects?limit&offset&q&sort), so results cover every
// project, not just the first page.

export const PROJECT_SORTS = [
  "updated_desc",
  "updated_asc",
  "title_asc",
] as const;
export type ProjectSort = (typeof PROJECT_SORTS)[number];

export const PROJECT_SORT_LABELS: Record<ProjectSort, string> = {
  updated_desc: "Recently updated",
  updated_asc: "Oldest updated",
  title_asc: "Title A–Z",
};

export const PROJECT_PAGE_SIZE = 20;
/** Matches the API's limit on `q`. */
export const PROJECT_SEARCH_MAX_LENGTH = 120;

export type ProjectListQuery = { q: string; sort: ProjectSort; page: number };

type SearchParams = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

function isProjectSort(value: string | undefined): value is ProjectSort {
  return PROJECT_SORTS.includes(value as ProjectSort);
}

export function parseProjectListQuery(params: SearchParams): ProjectListQuery {
  const q = (first(params.q) ?? "").trim().slice(0, PROJECT_SEARCH_MAX_LENGTH);
  const sortParam = first(params.sort);
  const pageParam = first(params.page) ?? "";
  const page = /^\d+$/.test(pageParam) ? Number(pageParam) : 1;
  return {
    q,
    sort: isProjectSort(sortParam) ? sortParam : "updated_desc",
    page: Number.isSafeInteger(page) && page >= 1 ? page : 1,
  };
}

/** The API request for one page of the list. */
export function projectListApiPath(
  { q, sort, page }: ProjectListQuery,
  pageSize = PROJECT_PAGE_SIZE,
) {
  const params = new URLSearchParams({
    limit: String(pageSize),
    offset: String((page - 1) * pageSize),
    sort,
  });
  if (q) params.set("q", q);
  return `/projects?${params.toString()}`;
}

/** The /projects URL for a list state, leaving defaults out. */
export function projectListHref({ q, sort, page }: ProjectListQuery) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (sort !== "updated_desc") params.set("sort", sort);
  if (page > 1) params.set("page", String(page));
  const query = params.toString();
  return query ? `/projects?${query}` : "/projects";
}

export function pageCountFor(total: number, pageSize = PROJECT_PAGE_SIZE) {
  return Math.max(1, Math.ceil(total / pageSize));
}
