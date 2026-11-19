"""Paginated teacher question reads and exact-text frequency aggregation."""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import bindparam, text
from gateway.analytics_time import utc_offset_for

# Dates outside this window are not meaningful for classroom analytics and
# would overflow the ``end_date + 1 day``/timezone arithmetic below.
MIN_QUERY_DATE = date(1900, 1, 1)
MAX_QUERY_DATE = date(2100, 12, 31)


def validate_question_period(start_date: date, end_date: date) -> None:
    """Reject reversed or out-of-range dates before any bound is computed."""
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    if start_date < MIN_QUERY_DATE or end_date > MAX_QUERY_DATE:
        raise ValueError("请选择 1900-01-01 至 2100-12-31 之间的有效时间段")


def read_question_activity(engine, *, student_user_ids, start_date, end_date, timezone_name, page=1, frequent_page=1, page_size=10, search="", student_id=None, group_id=None):
    validate_question_period(start_date, end_date)
    offset = utc_offset_for(timezone_name)
    start = datetime.combine(start_date, time.min) - offset
    end = datetime.combine(end_date + timedelta(days=1), time.min) - offset
    params = {"users": sorted(student_user_ids), "start": start, "end": end, "search": search.lower(), "student": student_id, "group": group_id, "size": page_size, "skip": (page-1)*page_size, "fskip": (frequent_page-1)*page_size}
    normalized = "REGEXP_REPLACE(LOWER(t.input_text), '[[:space:][:punct:]]+', '')"
    fingerprint = f"SHA2({normalized},256)"
    base = " FROM nlp_turns t JOIN nlp_users u ON u.id=t.user_id WHERE t.user_id IN :users AND t.created_at>=:start AND t.created_at<:end AND TRIM(t.input_text)<>'' AND INSTR(LOWER(t.input_text),:search)>0"
    detail = base + " AND (:student IS NULL OR t.user_id=:student)" + f" AND (:group IS NULL OR {fingerprint}=:group)"
    def statement(sql):
        return text(sql).bindparams(bindparam("users", expanding=True))
    with engine.connect() as connection:
        with connection.begin():
            summary = dict(connection.execute(statement("SELECT COUNT(*) AS question_count, COUNT(DISTINCT t.user_id) AS user_count" + base), params).mappings().one())
            total = connection.execute(statement("SELECT COUNT(*)" + detail), params).scalar_one()
            items = [dict(row) for row in connection.execute(statement("SELECT t.id,t.user_id,u.username,u.display_name,t.input_text AS question,t.created_at" + detail + " ORDER BY t.created_at DESC,t.id DESC LIMIT :size OFFSET :skip"),params).mappings()]
            groups = f"SELECT {fingerprint} AS group_id, MIN(t.input_text) AS question, COUNT(*) AS question_count,COUNT(DISTINCT t.user_id) AS user_count,MAX(t.created_at) AS last_asked" + base + f" AND {normalized}<>'' GROUP BY {fingerprint} HAVING COUNT(DISTINCT t.user_id)>=2"
            frequent_total = connection.execute(statement("SELECT COUNT(*) FROM (" + groups + ") g"), params).scalar_one()
            frequent = [dict(row) for row in connection.execute(statement(groups + " ORDER BY user_count DESC,question_count DESC,last_asked DESC,group_id LIMIT :size OFFSET :fskip"),params).mappings()]
    for row in items:
        value = row["created_at"]
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        row["created_at"] = value.replace(tzinfo=timezone.utc).astimezone(timezone(offset)).isoformat()
    return {"summary": summary, "items":items,"total":total,"page":page,"page_size":page_size,"frequent":frequent,"frequent_total":frequent_total,"frequent_page":frequent_page,"start_date":start_date,"end_date":end_date,"timezone":timezone_name}
