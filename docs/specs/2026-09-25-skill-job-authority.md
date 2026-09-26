# Skill-job authority and index

**Status:** decided for the runner correctness work. This table covers skill
jobs. Finance retains relational authority as a domain exception; separating
its database is a later change.

| Fact | Authoritative record | Rebuildable projection |
| --- | --- | --- |
| Submitted work | `system/queue/<id>.json` intent | `jobs` queued row |
| Execution claim | `system/runs/<id>.attempt-<n>.json` under the single-runner lock | Not projected into the index in S1a |
| Terminal outcome | `system/runs/<id>.json` with completion evidence | `jobs` terminal row and `job_events` |
| Runner liveness and unresolved attempts | `system/runner-status.json` plus attempt and terminal files | `/runner` response |

The runner writes an attempt before it starts an engine. It writes the
terminal record, removes the intent, then posts the terminal event. A
missing or unparseable attempt-linked terminal record gives no evidence of
success. An attempt without a matching terminal record is reported and held
for manual recovery; rebuilding the jobs index never executes it. Attempt
records are not projected into the index in S1a. An unresolved attempt appears
as a `queued` row rebuilt from its intent plus an entry in `/runner`
`unresolved_attempts`. Historical records without attempt IDs retain their
original optional-field format for
index rebuild. The API serves the index to clients, and a fresh index can be
rebuilt from the files.

The current file layout is `<vault>/system`. The runner refuses a configured
state root that differs from the readers' root. Moving files to a new root
requires a separate migration.
