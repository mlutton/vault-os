"use client";

import { useEffect, useState } from "react";

type SkillArgument = { name: string; required: boolean; type: string };
type Skill = { id: string; label: string; args: SkillArgument[] };
type SkillsResponse = { skills: Skill[] };

function apiUrl(path: string) {
  return `${process.env.NEXT_PUBLIC_API_BASE_URL ?? ""}${path}`;
}

export default function Home() {
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const [submittedJobId, setSubmittedJobId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function loadSkills() {
      try {
        const response = await fetch(apiUrl("/skills"));
        if (!response.ok) throw new Error("Skills could not be loaded");
        const body = (await response.json()) as SkillsResponse;
        if (active) {
          setSkills(body.skills);
          setListError(null);
        }
      } catch {
        if (active) {
          setSkills(null);
          setListError("Skills could not be loaded. Try again shortly.");
        }
      }
    }
    void loadSkills();
    return () => {
      active = false;
    };
  }, []);

  async function submit(skill: Skill) {
    const args = Object.fromEntries(
      skill.args
        .filter((argument) => argument.required)
        .map((argument) => [argument.name, values[`${skill.id}:${argument.name}`] ?? ""]),
    );
    setSubmitting(skill.id);
    setSubmissionError(null);
    setSubmittedJobId(null);
    try {
      const response = await fetch(apiUrl("/jobs"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill: skill.id, args }),
      });
      if (response.status !== 201) throw new Error("Job could not be submitted");
      const body = (await response.json()) as { id: string };
      setSubmittedJobId(body.id);
    } catch {
      setSubmissionError("Job could not be submitted. Please try again.");
    } finally {
      setSubmitting(null);
    }
  }

  return (
    <section className="skill-deck" aria-labelledby="cockpit-title">
      <p className="skill-deck__kicker">Operations cockpit</p>
      <h1 id="cockpit-title">Skill deck</h1>
      <p className="skill-deck__intro">Dispatch a registered VaultOS skill through the API.</p>
      {listError ? <p role="alert">{listError}</p> : null}
      {skills === null && !listError ? <p>Loading registered skills…</p> : null}
      {skills?.length === 0 ? <p>No registered skills are available.</p> : null}
      {skills && skills.length > 0 ? (
        <div className="skill-deck__grid">
          {skills.map((skill) => (
            <article className="skill-card" key={skill.id}>
              <p className="skill-card__id">{skill.id}</p>
              <h2>{skill.label}</h2>
              {skill.args.filter((argument) => argument.required).map((argument) => (
                <label key={argument.name}>
                  {argument.name}
                  <input
                    name={`${skill.id}:${argument.name}`}
                    onChange={(event) =>
                      setValues((current) => ({ ...current, [event.target.name]: event.target.value }))
                    }
                    required
                    type="text"
                    value={values[`${skill.id}:${argument.name}`] ?? ""}
                  />
                </label>
              ))}
              <button disabled={submitting !== null} onClick={() => void submit(skill)} type="button">
                {submitting === skill.id ? "Submitting…" : `Dispatch ${skill.label}`}
              </button>
            </article>
          ))}
        </div>
      ) : null}
      {submissionError ? <p role="alert">{submissionError}</p> : null}
      {submittedJobId ? <p role="status">Job {submittedJobId} submitted.</p> : null}
    </section>
  );
}
