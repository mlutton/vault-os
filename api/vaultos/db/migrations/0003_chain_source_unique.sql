-- CHAIN_MAP auto-dispatch (ADR-0016) tags a chained job's source as
-- "chain:{parent_skill}:{parent_job_id}" so it's traceable to the one parent
-- job that triggered it. This partial unique index lets store.create_job()
-- use INSERT OR IGNORE to atomically reject a second dispatch for the same
-- parent -- the same UNIQUE + INSERT OR IGNORE idiom job_events already uses
-- for its own dedup. Scoped to chain: sources only; ordinary jobs share
-- source values (e.g. "api") by design and must not be constrained by this.

-- Legacy "chain:{skill}" sources (pre-dating the {skill}:{job_id} form added
-- 2026-08-12) collide with each other under the index below -- multiple real
-- historical jobs share the exact same old-format source. Their true parent
-- job isn't recoverable from that format, so rewrite each to a
-- synthesized-unique value (its own id stands in for the unknown parent)
-- rather than lose or merge real rows. Idempotent: already-migrated
-- "chain:{skill}:{id}" sources contain a second colon and are left alone.
UPDATE jobs
SET source = source || ':legacy-' || id
WHERE source LIKE 'chain:%' AND source NOT LIKE 'chain:%:%';

CREATE UNIQUE INDEX jobs_chain_source ON jobs(source) WHERE source LIKE 'chain:%';
