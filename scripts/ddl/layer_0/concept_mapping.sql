-- Curated concept-family → QName preference list.
-- concept_family is the fuzzy slot the plan generator emits ("Revenue").
-- qname is the exact us-gaap/extension QName the resolver maps it to.
-- Bootstrapped from HiFi-KPI synonym mining + manual curation.
CREATE TABLE IF NOT EXISTS concept_mapping (
    concept_family  TEXT NOT NULL,
    qname           TEXT NOT NULL,
    preference_rank INT  NOT NULL,   -- 1 = preferred, 2+ = fallbacks
    cik_override    TEXT,            -- NULL = all filers; CIK = filer-specific override
    is_deprecated   BOOLEAN DEFAULT FALSE,
    source          TEXT            -- 'hifi_kpi' | 'manual' | 'llm'
);


CREATE UNIQUE INDEX IF NOT EXISTS concept_mapping_uq
ON concept_mapping (
    concept_family,
    qname,
    COALESCE(cik_override, '')
);
-- Role of the QName in its family for the row's filer, and the substitution an answer
-- states when the role is 'substitute' (docs/specs/concept-family-coverage.md). NULL
-- role: the role the rank implies (50-99 component, else primary). Written from the
-- generated CSV (src/ingest/util/generate_concept_mapping.py); `auto_override` rows
-- copy their QName's generic values, and `ns_expanded` copies carry their base row's.
ALTER TABLE concept_mapping ADD COLUMN IF NOT EXISTS concept_role      TEXT;
ALTER TABLE concept_mapping ADD COLUMN IF NOT EXISTS substitution_code TEXT;
ALTER TABLE concept_mapping ADD COLUMN IF NOT EXISTS substitution_note TEXT;
