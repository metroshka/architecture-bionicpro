-- Только чтение. Запускается после DAG для учебных дней.
SELECT report_date, publication_id
FROM reports.ready_periods
ORDER BY report_date;

-- JOIN с реестром отсекает старые и незавершённые версии.
SELECT m.report_date, m.owner_subject, m.prosthesis_id, m.model,
       m.movement_count, m.avg_response_time_ms
FROM reports.daily_prosthesis_usage AS m
INNER JOIN reports.ready_periods AS ready
    ON m.report_date = ready.report_date AND m.publication_id = ready.publication_id
WHERE m.report_date BETWEEN '2026-11-08' AND '2026-11-10'
ORDER BY m.report_date, m.owner_subject, m.prosthesis_id;

-- У пустого дня есть публикация с нулевыми счётчиками.
SELECT DISTINCT p.report_date, p.row_count, p.event_count
FROM reports.report_publications AS p
INNER JOIN reports.ready_periods AS ready
    ON p.report_date = ready.report_date AND p.publication_id = ready.publication_id
WHERE p.report_date BETWEEN '2026-11-08' AND '2026-11-10'
ORDER BY p.report_date;
