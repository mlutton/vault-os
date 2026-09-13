"use client";

import { useEffect, useRef, useState } from "react";

import { apiUrl } from "../lib/api";

type Run = {
  id: string;
  skill: string;
  status: string;
  ts_completed: string | null;
  duration_s: number | null;
  exit_code: number | null;
  summary: string | null;
};

type SkillOption = { id: string; label: string };

const EM_DASH = "—";

// Deliberately not `new Date(ts)`: completion timestamps are already UTC
// (vaultos.timeutil.utcnow_z), and the display must not depend on the
// runtime's local timezone or on Date's handling of >3-digit fractions.
const COMPLETED_TS_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z$/;

function formatCompletedUtc(ts: string | null): string {
  if (!ts) return EM_DASH;
  const match = COMPLETED_TS_RE.exec(ts);
  if (!match) return EM_DASH;
  const [, year, month, day, hour, minute] = match;
  return `${year}-${month}-${day} ${hour}:${minute} UTC`;
}

function displayField(value: string | number | null): string {
  return value === null || value === undefined ? EM_DASH : String(value);
}

function runsUrl(filters: { skill: string; since: string }): string {
  const params = new URLSearchParams({ limit: "50" });
  if (filters.skill) params.set("skill", filters.skill);
  if (filters.since) params.set("since", filters.since);
  return `${apiUrl("/runs")}?${params.toString()}`;
}

export function RunHistory({ skills }: Readonly<{ skills: SkillOption[] | null }>) {
  const [skillFilter, setSkillFilter] = useState("");
  const [sinceFilter, setSinceFilter] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latestRequestId = useRef(0);

  useEffect(() => {
    const requestId = ++latestRequestId.current;
    async function load() {
      setRuns(null);
      setError(null);
      try {
        const response = await fetch(runsUrl({ skill: skillFilter, since: sinceFilter }));
        if (!response.ok) throw new Error("Run history could not be loaded");
        const body = (await response.json()) as Run[];
        if (latestRequestId.current === requestId) {
          setRuns(body);
        }
      } catch {
        if (latestRequestId.current === requestId) {
          setRuns(null);
          setError("Run history could not be loaded. Try again shortly.");
        }
      }
    }
    void load();
  }, [skillFilter, sinceFilter, refreshToken]);

  return (
    <section className="run-history" aria-labelledby="run-history-title">
      <p className="run-history__kicker">Run history</p>
      <h2 id="run-history-title">Recent runs</h2>
      <div className="run-history__controls">
        <label>
          Skill
          <select value={skillFilter} onChange={(event) => setSkillFilter(event.target.value)}>
            <option value="">All skills</option>
            {(skills ?? []).map((skill) => (
              <option key={skill.id} value={skill.id}>
                {skill.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Since (UTC)
          <input
            onChange={(event) => setSinceFilter(event.target.value)}
            type="date"
            value={sinceFilter}
          />
        </label>
        <button onClick={() => setRefreshToken((token) => token + 1)} type="button">
          Refresh
        </button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
      {runs === null && !error ? <p>Loading run history…</p> : null}
      {runs?.length === 0 ? <p>No completed runs match.</p> : null}
      {runs && runs.length > 0 ? (
        <table className="run-history__table">
          <thead>
            <tr>
              <th scope="col">Skill</th>
              <th scope="col">Outcome</th>
              <th scope="col">Completed (UTC)</th>
              <th scope="col">Duration</th>
              <th scope="col">Exit code</th>
              <th scope="col">Summary</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{run.skill}</td>
                <td>{run.status}</td>
                <td>{formatCompletedUtc(run.ts_completed)}</td>
                <td>{run.duration_s === null ? EM_DASH : `${run.duration_s}s`}</td>
                <td>{displayField(run.exit_code)}</td>
                <td>{displayField(run.summary)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}
