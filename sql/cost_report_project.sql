-- 프로젝트별 이달 비용 집계 (리포트 생성용)
-- 파라미터: :start (YYYY-MM-01), :end (다음 달 YYYY-MM-01)
SELECT
    o.id::text                                          AS org_id,
    o.name                                              AS org_name,
    COALESCE(p.id::text, 'common_' || o.id::text)      AS project_id,
    COALESCE(p.name, '프로젝트 공통')                    AS project_name,
    COUNT(*)::text                                      AS event_count,
    COALESCE(SUM(ce.input_tokens),       0)::text       AS input_tokens,
    COALESCE(SUM(ce.output_tokens),      0)::text       AS output_tokens,
    COALESCE(SUM(ce.cache_write_tokens), 0)::text       AS cache_write_tokens,
    COALESCE(SUM(ce.cache_read_tokens),  0)::text       AS cache_read_tokens,
    COALESCE(SUM(ce.cost_cents),         0)::text       AS cost_cents,
    COALESCE(bp.monthly_limit_cents,     0)::text       AS monthly_limit_cents
FROM cost_events ce
JOIN  organizations  o  ON ce.organization_id = o.id
LEFT JOIN projects   p  ON ce.project_id      = p.id
LEFT JOIN budget_policies bp ON ce.organization_id = bp.organization_id
WHERE ce.created >= :start
  AND ce.created <  :end
GROUP BY o.id, o.name, p.id, p.name, bp.monthly_limit_cents
ORDER BY o.name ASC, SUM(ce.cost_cents) DESC;
