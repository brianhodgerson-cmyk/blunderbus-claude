-- finance.transactions_deduped
--
-- Canonical source for spending/income aggregation in BlunderBus finance code.
--
-- Why this view exists:
--   Monarch's overnight ingest occasionally re-pulls the same transaction with
--   a fresh `id`. Two patterns observed:
--     1. Sequential (+1) ids in the SAME paginated batch — Monarch returned the
--        same row twice. Caught at ingest by the in-batch sig_index in
--        scripts/monarch_ingest.py.
--     2. Re-issued ids ~10^14 apart on a LATER snapshot — api.monarch.com
--        regenerated the id (common after pending→cleared graduation, but also
--        seen for already-cleared rows). Caught at ingest by the existing-row
--        sig_index lookup (when same `is_pending` state) OR collapsed at read
--        time by this view.
--
-- The underlying `finance.transactions` is ReplacingMergeTree(inserted_at)
-- ORDER BY id — different ids never merge, so we collapse here.
--
-- Grouping key: (date, merchant, abs(amount), account_id).
-- Pick rule:    argMax(*, inserted_at) — the most-recently-curated row wins.
--               This is correct because Monarch's ML reclassifies categories
--               between snapshots (e.g. H-E-B Apr-17 $89.28 moved
--               Groceries → Gas). The latest insert reflects current Monarch
--               state; older inserts may carry stale auto-categorization.
--
-- signature_count column: number of raw rows that collapsed. =1 normal,
--                         ≥2 means dedup did work. ≥3 deserves investigation.
--
-- See: memory/finance/learnings.md "Systemic data-quality fixes (2026-05-14)",
--      decisions/2026-05-14.md "monarch_dedup_signature".

DROP VIEW IF EXISTS finance.transactions_deduped;

CREATE VIEW finance.transactions_deduped AS
WITH src AS (
  SELECT
    id, date, amount, merchant, category, account_id,
    account_name, institution, notes, is_pending,
    inserted_at AS ts,
    abs(amount) AS abs_amount
  FROM finance.transactions FINAL
)
SELECT
    argMax(id,           ts) AS id,
    date,
    argMax(amount,       ts) AS amount,
    merchant,
    argMax(category,     ts) AS category,
    account_id,
    argMax(account_name, ts) AS account_name,
    argMax(institution,  ts) AS institution,
    argMax(notes,        ts) AS notes,
    argMax(is_pending,   ts) AS is_pending,
    max(ts)                  AS inserted_at,
    count()                  AS signature_count
FROM src
GROUP BY date, merchant, abs_amount, account_id;
