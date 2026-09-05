import { render, screen } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { CockpitFrame } from "../components/CockpitFrame";
import RootLayout from "./layout";
import Home from "./page";

describe("Home", () => {
  it("mounts CockpitFrame around application children from the root layout", () => {
    const layout = RootLayout({ children: <Home /> });
    const body = layout.props.children as ReactElement<{ children: ReactNode }>;
    const frame = body.props.children as ReactElement<{ children: ReactNode }>;

    expect(layout.type).toBe("html");
    expect(body.type).toBe("body");
    expect(frame.type).toBe(CockpitFrame);
    expect((frame.props.children as ReactElement).type).toBe(Home);
  });

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
