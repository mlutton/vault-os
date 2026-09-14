import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CockpitFrame } from "../components/CockpitFrame";
import RootLayout from "./layout";
import Home from "./page";

type Handler = (context: { search: URLSearchParams; init?: RequestInit }) => Response | Promise<Response>;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

// Routes fetch by method + pathname rather than call order, since the page
// now issues two independent GETs on mount (/skills and /runs) whose
// relative order is not something a test should assert on.
function stubFetch(routes: Record<string, Handler>) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const parsed = new URL(url, "http://localhost");
    const method = init?.method ?? "GET";
    const key = `${method} ${parsed.pathname}`;
    const handler = routes[key];
    if (!handler) {
      return Promise.reject(new Error(`unstubbed fetch: ${key}${parsed.search}`));
    }
    return Promise.resolve(handler({ search: parsed.searchParams, init }));
  });
}

const NO_RUNS = () => jsonResponse([]);

const RUN_OK = {
  id: "run-1",
  skill: "metrics-pull",
  status: "ok",
  ts_completed: "2026-08-01T00:00:01.123456Z",
  duration_s: 42,
  exit_code: 0,
  summary: "Pulled metrics",
};

const RUN_ERROR = {
  id: "run-2",
  skill: "acquire",
  status: "error",
  ts_completed: "2026-08-02T03:04:05.000Z",
  duration_s: null,
  exit_code: 1,
  summary: null,
};

const TWO_SKILLS = [
  { id: "metrics-pull", label: "Metrics pull", args: [] },
  { id: "acquire", label: "Acquire", args: [] },
];

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
    const fetchMock = stubFetch({
      "GET /skills": () =>
        jsonResponse({
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
      "GET /runs": NO_RUNS,
      "POST /jobs": () =>
        jsonResponse({ id: "job-123", skill: "deep-research", status: "queued", runner_alive: true }, 201),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <CockpitFrame>
        <Home />
      </CockpitFrame>,
    );

    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeInTheDocument();
    const deck = within(screen.getByRole("region", { name: "Skill deck" }));
    expect(await deck.findByText("deep-research")).toBeInTheDocument();
    expect(deck.getByText("Deep research")).toBeInTheDocument();
    expect(deck.getByText("metrics-pull")).toBeInTheDocument();
    expect(deck.getByText("Metrics pull")).toBeInTheDocument();
    expect(deck.getByLabelText("topic")).toBeInTheDocument();
    expect(deck.queryByLabelText("optional")).not.toBeInTheDocument();

    fireEvent.change(deck.getByLabelText("topic"), { target: { value: "Agent testing" } });
    fireEvent.click(deck.getByRole("button", { name: "Dispatch Deep research" }));

    await screen.findByText("Job job-123 submitted.");
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("https://api.example.test/skills");
      expect(fetchMock).toHaveBeenCalledWith("https://api.example.test/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill: "deep-research", args: { topic: "Agent testing" } }),
      });
    });
  });

  it("shows a distinct empty state with no dispatch control", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => jsonResponse({ version: 1, skills: [] }),
        "GET /runs": NO_RUNS,
      }),
    );

    render(<Home />);

    expect(await screen.findByText("No registered skills are available.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Dispatch/ })).not.toBeInTheDocument();
  });

  it("clears the deck and presents a useful error when skills cannot load", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => new Response("Unavailable", { status: 503 }),
        "GET /runs": NO_RUNS,
      }),
    );

    render(<Home />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Skills could not be loaded");
    expect(screen.queryByRole("button", { name: /Dispatch/ })).not.toBeInTheDocument();
  });

  it("keeps the dispatch control usable and hides success after a failed submission", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () =>
          jsonResponse({
            version: 1,
            skills: [{ id: "deep-research", label: "Deep research", args: [] }],
          }),
        "GET /runs": NO_RUNS,
        "POST /jobs": () => new Response("Unavailable", { status: 503 }),
      }),
    );

    render(<Home />);

    const dispatch = await screen.findByRole("button", { name: "Dispatch Deep research" });
    fireEvent.click(dispatch);

    expect(await screen.findByRole("alert")).toHaveTextContent("Job could not be submitted");
    expect(dispatch).toBeEnabled();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("Run history panel", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  function runHistoryRegion() {
    return screen.getByRole("region", { name: "Recent runs" });
  }

  it("shows a loading message before the run-history response resolves", () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => jsonResponse({ version: 1, skills: [] }),
        "GET /runs": () => new Promise(() => {}),
      }),
    );

    render(<Home />);

    expect(within(runHistoryRegion()).getByText("Loading run history…")).toBeInTheDocument();
  });

  it("shows each run's skill, outcome, UTC completion time, duration, exit code and summary, newest first, and accepts a microsecond fraction under a non-UTC runtime timezone", async () => {
    vi.stubEnv("TZ", "America/New_York");
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => jsonResponse({ version: 1, skills: TWO_SKILLS }),
        "GET /runs": () => jsonResponse([RUN_OK, RUN_ERROR]),
      }),
    );

    render(<Home />);

    const region = within(runHistoryRegion());
    const rows = await waitFor(() => {
      const found = region.getAllByRole("row").slice(1);
      expect(found).toHaveLength(2);
      return found;
    });

    expect(within(rows[0]).getByText("metrics-pull")).toBeInTheDocument();
    expect(within(rows[0]).getByText("ok")).toBeInTheDocument();
    expect(within(rows[0]).getByText("2026-08-01 00:00 UTC")).toBeInTheDocument();
    expect(within(rows[0]).getByText("42s")).toBeInTheDocument();
    expect(within(rows[0]).getByText("0")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Pulled metrics")).toBeInTheDocument();

    expect(within(rows[1]).getByText("acquire")).toBeInTheDocument();
    expect(within(rows[1]).getByText("error")).toBeInTheDocument();
    expect(within(rows[1]).getByText("2026-08-02 03:04 UTC")).toBeInTheDocument();
    expect(within(rows[1]).getByText("1")).toBeInTheDocument();
    expect(within(rows[1]).getAllByText("—")).toHaveLength(2);
  });

  it("requests the registered skill and shows only its runs; All skills omits the parameter", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => jsonResponse({ version: 1, skills: TWO_SKILLS }),
        "GET /runs": ({ search }) =>
          search.get("skill") === "metrics-pull" ? jsonResponse([RUN_OK]) : jsonResponse([RUN_OK, RUN_ERROR]),
      }),
    );
    const fetchMock = vi.mocked(globalThis.fetch);

    render(<Home />);

    const region = within(runHistoryRegion());
    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(3));

    fireEvent.change(region.getByLabelText("Skill"), { target: { value: "metrics-pull" } });

    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(2));
    expect(region.getByText("metrics-pull")).toBeInTheDocument();
    expect(region.queryByText("acquire")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/runs?limit=50&skill=metrics-pull");
    expect(fetchMock).toHaveBeenCalledWith("/runs?limit=50");
  });

  it('acceptance condition 2 — "All skills" omits the skill parameter after a skill was selected', async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => jsonResponse({ version: 1, skills: TWO_SKILLS }),
        "GET /runs": ({ search }) =>
          search.get("skill") === "metrics-pull" ? jsonResponse([RUN_OK]) : jsonResponse([RUN_OK, RUN_ERROR]),
      }),
    );
    const fetchMock = vi.mocked(globalThis.fetch);

    render(<Home />);

    const region = within(runHistoryRegion());
    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(3));

    fireEvent.change(region.getByLabelText("Skill"), { target: { value: "metrics-pull" } });
    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(2));

    const callsBeforeReselect = fetchMock.mock.calls.filter((call) => call[0] === "/runs?limit=50").length;

    fireEvent.change(region.getByLabelText("Skill"), { target: { value: "" } });

    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(3));
    expect(region.getByText("metrics-pull")).toBeInTheDocument();
    expect(region.getByText("acquire")).toBeInTheDocument();
    const callsAfterReselect = fetchMock.mock.calls.filter((call) => call[0] === "/runs?limit=50").length;
    expect(callsAfterReselect).toBeGreaterThan(callsBeforeReselect);
  });

  it("requests the UTC since-date and clears the parameter when the filter is cleared", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => jsonResponse({ version: 1, skills: [] }),
        "GET /runs": ({ search }) =>
          search.get("since") === "2026-08-02" ? jsonResponse([RUN_ERROR]) : jsonResponse([RUN_OK, RUN_ERROR]),
      }),
    );
    const fetchMock = vi.mocked(globalThis.fetch);

    render(<Home />);

    const region = within(runHistoryRegion());
    expect(region.getByLabelText("Since (UTC)")).toBeInTheDocument();
    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(3));

    fireEvent.change(region.getByLabelText("Since (UTC)"), { target: { value: "2026-08-02" } });
    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(2));
    expect(fetchMock).toHaveBeenCalledWith("/runs?limit=50&since=2026-08-02");

    fireEvent.change(region.getByLabelText("Since (UTC)"), { target: { value: "" } });
    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(3));
    expect(fetchMock).toHaveBeenCalledWith("/runs?limit=50");
  });

  it("shows a distinct empty message when no completed runs match", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => jsonResponse({ version: 1, skills: [] }),
        "GET /runs": NO_RUNS,
      }),
    );

    render(<Home />);

    expect(await within(runHistoryRegion()).findByText("No completed runs match.")).toBeInTheDocument();
  });

  it("shows an error alert and no rows when the run-history request fails", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => jsonResponse({ version: 1, skills: [] }),
        "GET /runs": () => new Response("Unavailable", { status: 503 }),
      }),
    );

    render(<Home />);

    const region = within(runHistoryRegion());
    expect(await region.findByRole("alert")).toHaveTextContent("Run history could not be loaded");
    expect(region.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows no stale rows from an earlier successful load after a later request fails", async () => {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => jsonResponse({ version: 1, skills: [] }),
        "GET /runs": () => {
          call += 1;
          return call === 1 ? jsonResponse([RUN_OK]) : new Response("Unavailable", { status: 503 });
        },
      }),
    );

    render(<Home />);

    const region = within(runHistoryRegion());
    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(2));

    fireEvent.click(region.getByRole("button", { name: "Refresh" }));

    await region.findByRole("alert");
    expect(region.queryByRole("table")).not.toBeInTheDocument();
    expect(region.queryByText("metrics-pull")).not.toBeInTheDocument();
  });

  it("shows only the latest-selected filter's rows when an earlier request resolves last", async () => {
    let resolveMetricsPull!: (response: Response) => void;
    const metricsPullDeferred = new Promise<Response>((resolve) => {
      resolveMetricsPull = resolve;
    });
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => jsonResponse({ version: 1, skills: TWO_SKILLS }),
        "GET /runs": ({ search }) => {
          const skill = search.get("skill");
          if (skill === "metrics-pull") return metricsPullDeferred;
          if (skill === "acquire") return jsonResponse([RUN_ERROR]);
          return jsonResponse([RUN_OK, RUN_ERROR]);
        },
      }),
    );

    render(<Home />);

    const region = within(runHistoryRegion());
    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(3));

    fireEvent.change(region.getByLabelText("Skill"), { target: { value: "metrics-pull" } });
    fireEvent.change(region.getByLabelText("Skill"), { target: { value: "acquire" } });

    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(2));
    expect(region.getByText("acquire")).toBeInTheDocument();

    resolveMetricsPull(jsonResponse([RUN_OK]));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(region.getByText("acquire")).toBeInTheDocument();
    expect(region.queryByText("metrics-pull")).not.toBeInTheDocument();
  });

  it("keeps run history working with only All skills when the skills request fails, and keeps the skill deck working when run history fails", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => new Response("Unavailable", { status: 503 }),
        "GET /runs": () => jsonResponse([RUN_OK]),
      }),
    );

    render(<Home />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Skills could not be loaded");
    const region = within(runHistoryRegion());
    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(2));
    expect(within(region.getByLabelText("Skill")).getAllByRole("option")).toHaveLength(1);
    expect(within(region.getByLabelText("Skill")).getByText("All skills")).toBeInTheDocument();
  });

  it("keeps the skill deck working when the run-history request fails", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () =>
          jsonResponse({ version: 1, skills: [{ id: "deep-research", label: "Deep research", args: [] }] }),
        "GET /runs": () => new Response("Unavailable", { status: 503 }),
      }),
    );

    render(<Home />);

    expect(await screen.findByText("deep-research")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dispatch Deep research" })).toBeEnabled();
    expect(await within(runHistoryRegion()).findByRole("alert")).toHaveTextContent(
      "Run history could not be loaded",
    );
  });

  it("re-issues the current filtered request when Refresh is clicked", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => jsonResponse({ version: 1, skills: TWO_SKILLS }),
        "GET /runs": () => jsonResponse([RUN_OK]),
      }),
    );
    const fetchMock = vi.mocked(globalThis.fetch);

    render(<Home />);

    const region = within(runHistoryRegion());
    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(2));

    fireEvent.change(region.getByLabelText("Skill"), { target: { value: "metrics-pull" } });
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/runs?limit=50&skill=metrics-pull"),
    );

    const callsBeforeRefresh = fetchMock.mock.calls.filter(
      (call) => call[0] === "/runs?limit=50&skill=metrics-pull",
    ).length;

    fireEvent.click(region.getByRole("button", { name: "Refresh" }));

    await waitFor(() => {
      const callsAfterRefresh = fetchMock.mock.calls.filter(
        (call) => call[0] === "/runs?limit=50&skill=metrics-pull",
      ).length;
      expect(callsAfterRefresh).toBeGreaterThan(callsBeforeRefresh);
    });
  });

  it("acceptance condition 4 — Refresh shows its result", async () => {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      stubFetch({
        "GET /skills": () => jsonResponse({ version: 1, skills: [] }),
        "GET /runs": () => {
          call += 1;
          return call === 1 ? jsonResponse([RUN_OK]) : jsonResponse([RUN_ERROR]);
        },
      }),
    );

    render(<Home />);

    const region = within(runHistoryRegion());
    await waitFor(() => expect(region.getAllByRole("row")).toHaveLength(2));
    expect(region.getByText("metrics-pull")).toBeInTheDocument();

    fireEvent.click(region.getByRole("button", { name: "Refresh" }));

    await waitFor(() => expect(region.getByText("acquire")).toBeInTheDocument());
    expect(region.queryByText("metrics-pull")).not.toBeInTheDocument();
  });
});
