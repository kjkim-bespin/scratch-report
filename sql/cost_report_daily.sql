-- 프로젝트별 일별 비용 집계 (일별 추이 섹션용)
-- 파라미터: :start (YYYY-MM-01), :end (다음 달 YYYY-MM-01)
SELECT
    DATE(ce.created AT TIME ZONE 'Asia/Seoul')::text    AS date,
    o.id::text                                          AS org_id,
    COALESCE(p.id::text, 'common_' || o.id::text)      AS project_id,
    COUNT(*)::text                                      AS event_count,
    COALESCE(SUM(ce.input_tokens),  0)::text            AS input_tokens,
    COALESCE(SUM(ce.output_tokens), 0)::text            AS output_tokens,
    COALESCE(SUM(ce.cost_cents),    0)::text            AS cost_cents
FROM cost_events ce
JOIN  organizations o ON ce.organization_id = o.id
LEFT JOIN projects  p ON ce.project_id      = p.id
WHERE ce.created >= :start
  AND ce.created <  :end
GROUP BY DATE(ce.created AT TIME ZONE 'Asia/Seoul'), o.id, p.id
ORDER BY DATE(ce.created AT TIME ZONE 'Asia/Seoul') ASC,
         SUM(ce.cost_cents) DESC;
