-- 프로젝트별 전월 비용 집계 (전월 대비 증감률 계산용)
-- 파라미터: :prev_start (전월 YYYY-MM-01), :curr_start (이달 YYYY-MM-01)
SELECT
    o.id::text                                          AS org_id,
    COALESCE(p.id::text, 'common_' || o.id::text)      AS project_id,
    COALESCE(SUM(ce.cost_cents), 0)::text               AS cost_cents
FROM cost_events ce
JOIN  organizations o ON ce.organization_id = o.id
LEFT JOIN projects  p ON ce.project_id      = p.id
WHERE ce.created >= :prev_start
  AND ce.created <  :curr_start
GROUP BY o.id, p.id;
