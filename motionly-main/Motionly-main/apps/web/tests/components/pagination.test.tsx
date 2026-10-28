import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Pagination } from "@/components/pagination";

const hrefFor = (page: number) => `/projects?q=reel&page=${page}`;

describe("Pagination", () => {
  it("renders nothing for a single page", () => {
    const { container } = render(
      <Pagination page={1} pageCount={1} hrefFor={hrefFor} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("is a labelled nav that marks the current page", () => {
    render(<Pagination page={2} pageCount={3} hrefFor={hrefFor} />);
    const nav = screen.getByRole("navigation", { name: "Pagination" });
    const current = within(nav).getByRole("link", { name: "Page 2" });
    expect(current).toHaveAttribute("aria-current", "page");
    expect(
      within(nav).getByRole("link", { name: "Page 3" }),
    ).not.toHaveAttribute("aria-current");
  });

  it("links to the previous and next pages with rel and the query kept", () => {
    render(<Pagination page={2} pageCount={3} hrefFor={hrefFor} />);
    const previous = screen.getByRole("link", { name: "Previous page" });
    const next = screen.getByRole("link", { name: "Next page" });
    expect(previous).toHaveAttribute("href", "/projects?q=reel&page=1");
    expect(previous).toHaveAttribute("rel", "prev");
    expect(next).toHaveAttribute("href", "/projects?q=reel&page=3");
    expect(next).toHaveAttribute("rel", "next");
  });

  it("has no previous link on the first page and no next link on the last", () => {
    const { rerender } = render(
      <Pagination page={1} pageCount={3} hrefFor={hrefFor} />,
    );
    expect(screen.queryByRole("link", { name: "Previous page" })).toBeNull();
    rerender(<Pagination page={3} pageCount={3} hrefFor={hrefFor} />);
    expect(screen.queryByRole("link", { name: "Next page" })).toBeNull();
  });

  it("collapses long ranges around the current page", () => {
    render(<Pagination page={10} pageCount={20} hrefFor={hrefFor} />);
    const nav = screen.getByRole("navigation", { name: "Pagination" });
    const pages = within(nav)
      .getAllByRole("link")
      .map((link) => link.textContent)
      .filter((text) => /^\d+$/.test(text ?? ""));
    expect(pages).toEqual(["1", "9", "10", "11", "20"]);
  });
});
