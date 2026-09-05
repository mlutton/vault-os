import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CockpitFrame } from "./CockpitFrame";

describe("CockpitFrame", () => {
  it("renders the four destinations in product order", () => {
    render(<CockpitFrame>Content</CockpitFrame>);

    const navigation = screen.getByRole("navigation", { name: "Primary navigation" });
    const items = within(navigation).getAllByRole("listitem");

    expect(items).toHaveLength(4);
    expect(items.map((item) => item.textContent)).toEqual([
      "Cockpit",
      "FinancePlanned v1.1",
      "WritingPlanned v1.2",
      "ResearchPlanned v1.2",
    ]);
  });

  it("identifies Cockpit as the active page", () => {
    render(<CockpitFrame>Content</CockpitFrame>);

    expect(screen.getByRole("link", { name: "Cockpit", current: "page" })).toHaveAttribute("href", "/");
  });

  it("exposes exactly one current page", () => {
    render(<CockpitFrame>Content</CockpitFrame>);

    expect(document.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
  });

  it("couples the skip link to the focusable main landmark", () => {
    render(<CockpitFrame>Content</CockpitFrame>);

    const skipLink = screen.getByRole("link", { name: "Skip to content" });
    const main = screen.getByRole("main");
    const mainId = main.getAttribute("id");

    expect(mainId).toBeTruthy();
    expect(skipLink).toHaveAttribute("href", `#${mainId}`);
    expect(main).toHaveAttribute("tabindex", "-1");
  });

  it("shows planned destinations without making them navigable", () => {
    render(<CockpitFrame>Content</CockpitFrame>);

    expect(screen.getAllByText(/^Planned v1\.[12]$/)).toHaveLength(3);
    expect(screen.queryByRole("link", { name: /Finance|Writing|Research/ })).not.toBeInTheDocument();
  });

  it("announces deferred destinations within primary navigation", () => {
    render(<CockpitFrame>Content</CockpitFrame>);

    const navigation = screen.getByRole("navigation", { name: "Primary navigation" });

    expect(
      within(navigation).getByText("Three destinations are planned and not yet built."),
    ).toHaveClass("visually-hidden");
  });

  it("places child content in the main landmark", () => {
    render(
      <CockpitFrame>
        <p>Framed child</p>
      </CockpitFrame>,
    );

    expect(within(screen.getByRole("main")).getByText("Framed child")).toBeInTheDocument();
  });
});
