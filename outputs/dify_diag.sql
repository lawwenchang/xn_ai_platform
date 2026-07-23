\pset format unaligned
\pset tuples_only on

-- 1) 所有 draft：memory/window/role_prefix 计数对比（win<mem 即残缺）
SELECT 'DRAFT | ' || a.mode || ' | ' || substring(w.app_id::text,1,8) || ' | ' || a.name || ' | mem=' ||
  (length(w.graph)-length(replace(w.graph,'"memory"','')))/8 || ' win=' ||
  (length(w.graph)-length(replace(w.graph,'"window"','')))/8 || ' rp=' ||
  (length(w.graph)-length(replace(w.graph,'role_prefix','')))/11 || ' | ' || w.updated_at
FROM workflows w JOIN apps a ON a.id=w.app_id
WHERE w.version='draft'
ORDER BY w.updated_at DESC;

-- 2) 各 app 最新发布版：同样计数（运行 /v1/workflows/run 用的就是它）
SELECT 'LATEST_PUB | ' || a.mode || ' | ' || substring(w.app_id::text,1,8) || ' | ' || a.name || ' | mem=' ||
  (length(w.graph)-length(replace(w.graph,'"memory"','')))/8 || ' win=' ||
  (length(w.graph)-length(replace(w.graph,'"window"','')))/8 || ' rp=' ||
  (length(w.graph)-length(replace(w.graph,'role_prefix','')))/11 || ' | ' || w.created_at
FROM workflows w JOIN apps a ON a.id=w.app_id
WHERE w.version <> 'draft'
  AND w.created_at = (SELECT max(w2.created_at) FROM workflows w2
                      WHERE w2.app_id = w.app_id AND w2.version <> 'draft')
ORDER BY a.created_at;
