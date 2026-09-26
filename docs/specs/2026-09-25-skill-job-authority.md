# Skill-job authority and index

**Status:** decided for the runner correctness work. This table covers skill
jobs. Finance retains relational authority as a domain exception; separating
its database is a later change.

| Fact | Authoritative record | Rebuildable projection |
| --- | --- | --- |
| Submitted work | `system/queue/<id>.json` intent | `jobs` queued row |
| Execution claim | `system/runs/<id>.attempt-<n>.json` under the single-runner lock | Not projected into the index in S1a |
| Terminal outcome | `system/runs/<id>.json` with completion evidence | `jobs` terminal row and `job_events` |
| Pending chain transition | `transitions` in the parent's terminal record; deterministic child intent/run files | Child row with `chain:<parent skill>:<parent id>` source |
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

## Submission and child identity

Submission validates the skill and arguments, publishes the intent atomically
and durably (fsynced file, atomic publication, fsynced directory), then creates
the queued index row. Acknowledgment follows durable publication. If SQLite
fails after the intent is published, `POST /jobs` still returns **201** with
the accepted job ID and `status: queued`; the failure is logged and a later
reconcile restores its index row. The intent stays in place. Until then a
detail lookup can return 404 because clients read the index. A file-write
failure is not acknowledged as an accepted submission. Every ordinary
submission creates a new job.

The existing edges remain `acquire -> daily-topic-digest` and
`deep-research -> research-into-draft`, with empty child arguments. Each edge
has a `rule_id` naming that edge and `rule_version: 1`. Child identity is
`uuid5(CHAIN_NAMESPACE, "<parent attempt id>:<rule id>:<rule version>")`,
where `CHAIN_NAMESPACE` is the fixed UUID
`7f6cc4ad-76b5-5d6e-a536-ef1bcb735e13`. A legacy HTTP completion without
an attempt ID uses the parent job ID; replay of a runner completion uses
its durable attempt identity.

A successful parent's terminal record includes
`transitions: [{rule_id, rule_version, child_id, child_skill}]` before the
intent is removed. Failed runs and skills without an edge have an empty
list. A child intent retains its parent job ID, parent attempt ID, rule ID
and rule version in `chain`; its terminal file preserves that provenance.
Chain intents are published exclusively. An existing intent or run record
(including an unresolved attempt) makes dispatch a no-op returning the same
ID, even when the index is missing. File dedupe is primary; the partial unique
index on the chain source remains a secondary guard. Duplicate acknowledgment
fsyncs the observed file and directory too, so it is durable even if a
concurrent publisher has not finished its directory fsync. If a legacy
DB-only chain source owns another ID, the newly accepted intent stays in
place and the log calls for `reindex`; a DB-only owner never justifies
discarding authoritative work. An existing file-backed legacy owner still
dedupes the dispatch.

## Runner recovery and rebuilding

An explicit runner recovery pass runs at daemon startup and before every
claim, under the same single-runner lock as execution. Only strict,
attempt-bearing terminal records are consumed. Recovery validates recorded
child identity, then:

1. Removes a remaining parent intent.
2. Posts the recorded terminal event if the index lacks a completed outcome,
   restoring submission/start metadata as needed. An index-derived orphan
   assessment can be replaced by the durable outcome.
3. Dispatches each recorded transition whose child has neither an intent nor
   a run record, using the recorded child ID, skill, rule ID and version.

Each step is idempotent; repeating recovery changes nothing. Missing
child skills and file-write failures are logged as pending and
can be tried again at a later boundary. Recovery never reruns the parent.
Ambiguous attempts still require manual recovery. A DB-only queued row with
no valid intent never executes.

**The rebuild never enqueues work.** Reconcile and `reindex` only project
files into the jobs index; they never dispatch or consume transitions.
Orphan marking is index-derived and never triggers a retry.

## One-shot settlement of pre-S1a intents

`python -m vaultos.cli settle-intents` reports intents whose live index row
is terminal (`ok` or `error`) and which have neither a terminal
run file nor any attempt record. Reporting is the default and changes no
job files. With `--apply`, the command holds the runner lock, writes an
exclusive terminal record derived from the row with
`settled_from_index: true`, then durably removes the intent. Queued, running,
orphaned (a provisional index assessment),
missing-row and attempt-bearing jobs are never touched. Settled records
contain no attempt identity and trigger no runner recovery or chain dispatch.
This bounded DB-to-file exception is only for pre-S1a state, to be run once
by the operator after reviewing the report; it is not ordinary recovery.

The current file layout is `<vault>/system`. The runner refuses a configured
state root that differs from the readers' root. Moving files to a new root
requires a separate migration.
