import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CockpitFrame } from "../components/CockpitFrame";
import Home from "./page";

describe("Home", () => {
  it("renders the existing home content inside the cockpit frame", () => {
    render(
      <CockpitFrame>
        <Home />
      </CockpitFrame>,
    );

    const content = screen.getByRole("main");

    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeInTheDocument();
    expect(content).toContainElement(screen.getByRole("heading", { name: "VaultOS Web" }));
    expect(content).toHaveTextContent("The operations cockpit is coming soon.");
  });
});
