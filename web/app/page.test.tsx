import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CockpitFrame } from "../components/CockpitFrame";
import RootLayout from "./layout";
import Home from "./page";

describe("Home", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("mounts CockpitFrame around application children from the root layout", () => {
    const layout = RootLayout({ children: <Home /> });
    const body = layout.props.children as ReactElement<{ children: ReactNode }>;
    const frame = body.props.children as ReactElement<{ children: ReactNode }>;

    expect(layout.type).toBe("html");
    expect(body.type).toBe("body");
    expect(frame.type).toBe(CockpitFrame);
    expect((frame.props.children as ReactElement).type).toBe(Home);
  });

  it("renders registered skills and dispatches required string arguments through the configured API", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            version: 1,
            skills: [
              {
                id: "deep-research",
                label: "Deep research",
                args: [{ name: "topic", required: true, type: "string", max_length: 500 }],
              },
              {
                id: "metrics-pull",
                label: "Metrics pull",
                args: [{ name: "optional", required: false, type: "string", max_length: 40 }],
              },
            ],
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ id: "job-123", skill: "deep-research", status: "queued", runner_alive: true }),
          { status: 201 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <CockpitFrame>
        <Home />
      </CockpitFrame>,
    );

    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeInTheDocument();
    expect(await screen.findByText("deep-research")).toBeInTheDocument();
    expect(screen.getByText("Deep research")).toBeInTheDocument();
    expect(screen.getByText("metrics-pull")).toBeInTheDocument();
    expect(screen.getByText("Metrics pull")).toBeInTheDocument();
    expect(screen.getByLabelText("topic")).toBeInTheDocument();
    expect(screen.queryByLabelText("optional")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("topic"), { target: { value: "Agent testing" } });
    fireEvent.click(screen.getByRole("button", { name: "Dispatch Deep research" }));

    await screen.findByText("Job job-123 submitted.");
    await waitFor(() => {
      expect(fetchMock).toHaveBeenNthCalledWith(1, "https://api.example.test/skills");
      expect(fetchMock).toHaveBeenNthCalledWith(2, "https://api.example.test/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill: "deep-research", args: { topic: "Agent testing" } }),
      });
    });
  });

  it("shows a distinct empty state with no dispatch control", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ version: 1, skills: [] }), { status: 200 })),
    );

    render(<Home />);

    expect(await screen.findByText("No registered skills are available.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Dispatch/ })).not.toBeInTheDocument();
  });

  it("clears the deck and presents a useful error when skills cannot load", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Unavailable", { status: 503 })));

    render(<Home />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Skills could not be loaded");
    expect(screen.queryByRole("button", { name: /Dispatch/ })).not.toBeInTheDocument();
  });

  it("keeps the dispatch control usable and hides success after a failed submission", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            version: 1,
            skills: [{ id: "deep-research", label: "Deep research", args: [] }],
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(new Response("Unavailable", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<Home />);

    const dispatch = await screen.findByRole("button", { name: "Dispatch Deep research" });
    fireEvent.click(dispatch);

    expect(await screen.findByRole("alert")).toHaveTextContent("Job could not be submitted");
    expect(dispatch).toBeEnabled();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
