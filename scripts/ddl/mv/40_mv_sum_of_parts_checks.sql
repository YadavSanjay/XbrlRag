-- ============================================================================
-- mv_sum_of_parts_checks — XBRL Calculations 1.1 round-to-nearest, per filing (Layer 4)
-- ============================================================================
-- One row per (filing, calc link role, parent concept, context key) whose
-- summation binds. It MIRRORS the Python reference
-- src/ingest/sec_edgar/xbrl/calc_check.py::check_calculations
-- (docs/specs/calc-check-c11.md, Requirement 1); change both together.
-- test/integration/test_calc_check_view_live.py asserts they agree.
--
-- Rules (Calculations 1.1, round-to-nearest, as Arelle `--calc c11r`):
--   * Arcs: mv_calculation_network (12_) — both summation arcroles, each
--     (link role, parent, child) once; two weights for one arc make every
--     result of that tree 'inapplicable'.
--   * Context key: (entity, period_start, period_end, dimensions, unit), all
--     compared exactly — start date too (an instant has period_start NULL and
--     period_end = the instant), the full set of explicit (axis, member) pairs
--     (Arelle's is_default rows are not dimensions), and the unit's measures.
--   * Fact group = one concept's facts at one key (repeated iXBRL facts count
--     once). nil = every fact nil; inconsistent = some nil, or two facts with
--     the same decimals and different values, or excess digits, or empty
--     intersection of the intervals [v - 10^-d/2, v + 10^-d/2] (INF exact);
--     otherwise the group's value is that intersection.
--   * Binds when the parent's group is not nil and some child's group is
--     present and not nil. Every period is checked, not just the current one.
--   * status: 'inapplicable' (inconsistent parent/child group or weight
--     conflict; bounds NULL); else 'pass' when [reported] and
--     [sum of weight x child interval] intersect; else 'partial' when a child
--     is absent; else 'fail'.
--   * A STRICT failure (status 'fail', no dimensions) is what GATE-B counts,
--     per accession, against configs/ingest/calc_c11_exceptions.csv
--     (calc_check.calc_gate_decision, src/ingest/sec_edgar/xbrl/promotion.py).
--     'partial', dimensional 'fail' and 'inapplicable' are recorded only.
--
-- Reads raw xbrlDB tables + mv_calculation_network (12_); prefix 40_ orders it
-- after 12_. It no longer reads mv_financial_facts.
--
-- Design: docs/design/XbrlRag_Materialized_Views.md §4 and §6 (publish gate).
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS mv_sum_of_parts_checks CASCADE;

CREATE MATERIALIZED VIEW mv_sum_of_parts_checks AS
WITH unit_key AS (
    -- 'USD', 'USD/shares': numerator measures, then '/' and the denominator's.
    SELECT um.unit_id,
           string_agg(q.local_name, '*' ORDER BY q.local_name)
               FILTER (WHERE COALESCE(um.location_id, 1) = 1)
           || COALESCE('/' || string_agg(q.local_name, '*' ORDER BY q.local_name)
               FILTER (WHERE um.location_id = 2), '')               AS unit
      FROM unit_measure um
      JOIN qname q ON q.qname_id = um.qname_id
     GROUP BY um.unit_id
),
context_dims AS (
    SELECT cd.context_id,
           array_agg(ax.namespace || ':' || ax.local_name
                     ORDER BY ax.namespace || ':' || ax.local_name,
                              COALESCE(mq.namespace || ':' || mq.local_name, cd.typed_text_content))
                                                                    AS axes,
           array_agg(COALESCE(mq.namespace || ':' || mq.local_name, cd.typed_text_content)
                     ORDER BY ax.namespace || ':' || ax.local_name,
                              COALESCE(mq.namespace || ':' || mq.local_name, cd.typed_text_content))
                                                                    AS members
      FROM context_dimension cd
      JOIN qname ax      ON ax.qname_id = cd.dimension_qname_id
      LEFT JOIN qname mq ON mq.qname_id = cd.member_qname_id
     WHERE NOT cd.is_default
     GROUP BY cd.context_id
),
facts AS (
    SELECT f.fact_id,
           f.accession_id,
           q.namespace || ':' || q.local_name                        AS concept,
           c.entity_identifier                                      AS entity,
           c.period_start,
           COALESCE(c.period_end, c.period_instant)                 AS period_end,
           COALESCE(d.axes,    ARRAY[]::text[])                     AS axes,
           COALESCE(d.members, ARRAY[]::text[])                     AS members,
           u.unit,
           f.effective_value                                        AS value,   -- NULL = nil
           CASE WHEN f.is_decimals_infinity THEN NULL ELSE f.decimals_value END AS decimals
      FROM fact f
      JOIN context c   ON c.context_id = f.context_id
      JOIN unit_key u  ON u.unit_id    = f.unit_id
      JOIN element e   ON e.element_id = f.element_id
      JOIN qname q     ON q.qname_id   = e.qname_id
      LEFT JOIN context_dims d ON d.context_id = f.context_id
),
fact_bounds AS (
    SELECT fa.*,
           fa.value - COALESCE(0.5 * power(10::numeric, -fa.decimals), 0) AS lo,
           fa.value + COALESCE(0.5 * power(10::numeric, -fa.decimals), 0) AS hi,
           (fa.decimals IS NOT NULL
            AND mod(fa.value, power(10::numeric, -fa.decimals)) <> 0) AS excess_digits
      FROM facts fa
),
groups AS (
    -- One concept at one context key, reduced per Calculations 1.1.
    SELECT accession_id, concept, entity, period_start, period_end, axes, members, unit,
           array_agg(fact_id ORDER BY fact_id)                      AS fact_ids,
           CASE
               WHEN bool_and(value IS NULL)                         THEN 'nil'
               WHEN bool_or(value IS NULL)                          THEN 'inconsistent'
               WHEN bool_or(excess_digits)                          THEN 'inconsistent'
               WHEN COUNT(DISTINCT (COALESCE(decimals, 2147483647), value))
                    > COUNT(DISTINCT COALESCE(decimals, 2147483647)) THEN 'inconsistent'
               WHEN max(lo) > min(hi)                               THEN 'inconsistent'
               ELSE 'value'
           END                                                      AS state,
           max(lo)                                                  AS low,
           min(hi)                                                  AS high
      FROM fact_bounds
     GROUP BY accession_id, concept, entity, period_start, period_end, axes, members, unit
),
tree_children AS (
    -- One row per (tree, child); conflicting weights flag the whole tree.
    SELECT accession_id, link_role_uri, parent_concept, child_concept,
           min(weight)                                              AS weight,
           COUNT(DISTINCT weight) > 1                               AS weight_conflict
      FROM mv_calculation_network
     GROUP BY accession_id, link_role_uri, parent_concept, child_concept
),
tree_child_groups AS (
    SELECT pg.accession_id, tc.link_role_uri, tc.parent_concept,
           pg.entity, pg.period_start, pg.period_end, pg.axes, pg.members, pg.unit,
           pg.state AS parent_state, pg.low AS parent_low, pg.high AS parent_high,
           pg.fact_ids AS parent_fact_ids,
           tc.child_concept, tc.weight, tc.weight_conflict,
           cg.state AS child_state, cg.low AS child_low, cg.high AS child_high,
           cg.fact_ids AS child_group_fact_ids
      FROM groups pg
      JOIN tree_children tc
             ON tc.accession_id   = pg.accession_id
            AND tc.parent_concept = pg.concept
      LEFT JOIN groups cg
             ON cg.accession_id = pg.accession_id
            AND cg.concept      = tc.child_concept
            AND cg.entity       = pg.entity
            AND cg.period_start IS NOT DISTINCT FROM pg.period_start
            AND cg.period_end   = pg.period_end
            AND cg.axes         = pg.axes
            AND cg.members      = pg.members
            AND cg.unit         = pg.unit
     WHERE pg.state <> 'nil'
),
bound AS (
    SELECT accession_id, link_role_uri, parent_concept,
           entity, period_start, period_end, axes, members, unit,
           min(parent_state)                                        AS parent_state,
           min(parent_low)                                          AS parent_low,
           min(parent_high)                                         AS parent_high,
           min(parent_fact_ids)                                     AS parent_fact_ids,
           bool_or(weight_conflict)
             OR min(parent_state) = 'inconsistent'
             OR bool_or(child_state = 'inconsistent')               AS inapplicable,
           COALESCE(sum(CASE WHEN child_state <> 'value' THEN NULL
                             WHEN weight >= 0 THEN weight * child_low
                             ELSE weight * child_high END), 0)      AS computed_low,
           COALESCE(sum(CASE WHEN child_state <> 'value' THEN NULL
                             WHEN weight >= 0 THEN weight * child_high
                             ELSE weight * child_low END), 0)       AS computed_high,
           COALESCE(array_agg(child_concept ORDER BY child_concept)
                    FILTER (WHERE child_state IS NULL), ARRAY[]::text[]) AS missing_children
      FROM tree_child_groups
     GROUP BY accession_id, link_role_uri, parent_concept,
              entity, period_start, period_end, axes, members, unit
    HAVING bool_or(child_state IS NOT NULL AND child_state <> 'nil')
),
child_ids AS (
    SELECT t.accession_id, t.link_role_uri, t.parent_concept,
           t.entity, t.period_start, t.period_end, t.axes, t.members, t.unit,
           array_agg(x.fact_id ORDER BY x.fact_id)                  AS child_fact_ids
      FROM tree_child_groups t
      CROSS JOIN LATERAL unnest(t.child_group_fact_ids) AS x(fact_id)
     GROUP BY t.accession_id, t.link_role_uri, t.parent_concept,
              t.entity, t.period_start, t.period_end, t.axes, t.members, t.unit
),
checked AS (
    SELECT b.*,
           ci.child_fact_ids,
           CASE
               WHEN b.inapplicable THEN 'inapplicable'
               WHEN GREATEST(b.parent_low, b.computed_low)
                    <= LEAST(b.parent_high, b.computed_high) THEN 'pass'
               WHEN cardinality(b.missing_children) > 0 THEN 'partial'
               ELSE 'fail'
           END                                                      AS status
      FROM bound b
      JOIN child_ids ci
             ON ci.accession_id   = b.accession_id
            AND ci.link_role_uri  = b.link_role_uri
            AND ci.parent_concept = b.parent_concept
            AND ci.entity         = b.entity
            AND ci.period_start IS NOT DISTINCT FROM b.period_start
            AND ci.period_end     = b.period_end
            AND ci.axes           = b.axes
            AND ci.members        = b.members
            AND ci.unit           = b.unit
)
SELECT
    a.filing_accession_number                                       AS accession_no,
    ch.accession_id,
    ch.link_role_uri,
    ch.parent_concept,
    ch.entity,
    ch.period_start,
    ch.period_end,
    ch.axes                                                         AS dimension_axes,
    ch.members                                                      AS dimension_members,
    ch.unit,
    ch.status,
    cardinality(ch.axes) > 0                                        AS dimensional,
    (ch.status = 'fail' AND cardinality(ch.axes) = 0)               AS is_strict_failure,
    CASE WHEN ch.inapplicable THEN NULL ELSE ch.parent_low    END   AS reported_low,
    CASE WHEN ch.inapplicable THEN NULL ELSE ch.parent_high   END   AS reported_high,
    CASE WHEN ch.inapplicable THEN NULL ELSE ch.computed_low  END   AS computed_low,
    CASE WHEN ch.inapplicable THEN NULL ELSE ch.computed_high END   AS computed_high,
    ch.parent_fact_ids,
    ch.child_fact_ids,
    ch.missing_children
FROM checked ch
JOIN accession a ON a.accession_id = ch.accession_id;

CREATE INDEX ix_msop_acc    ON mv_sum_of_parts_checks (accession_no, status);
CREATE INDEX ix_msop_parent ON mv_sum_of_parts_checks (accession_no, parent_concept);
CREATE INDEX ix_msop_strict ON mv_sum_of_parts_checks (accession_no)
    WHERE is_strict_failure;
