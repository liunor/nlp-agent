from datetime import date, timedelta
import hashlib
import unicodedata

import pytest
from sqlalchemy import create_engine, event, text
from gateway.question_activity import read_question_activity


def test_question_details_frequency_scope_dates_and_pagination():
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def functions(connection, _):
        connection.create_function("REGEXP_REPLACE", 3, lambda value, pattern, replacement: "".join(c for c in value if not c.isspace() and not unicodedata.category(c).startswith("P")))
        connection.create_function("SHA2", 2, lambda value, bits: hashlib.sha256(value.encode()).hexdigest())
    with engine.begin() as c:
        c.execute(text("CREATE TABLE nlp_users(id TEXT,username TEXT,display_name TEXT)"))
        c.execute(text("CREATE TABLE nlp_turns(id TEXT,user_id TEXT,input_text TEXT,created_at DATETIME)"))
        c.execute(text("INSERT INTO nlp_users VALUES ('a','a','甲'),('b','b','乙'),('other','other','其他班')"))
        rows = [("1","a","什么是 Transformer？","2026-11-18 16:00:00"),("2","a","什么是transformer","2026-11-19 01:00:00"),("3","b","什么是transformer!","2026-11-19 02:00:00"),("4","a","仅一人反复问","2026-11-19 03:00:00"),("5","a","仅一人反复问","2026-11-19 04:00:00"),("6","a","仅一人反复问","2026-11-19 05:00:00"),("7","other","什么是transformer","2026-11-19 06:00:00"),("8","a","开始之前","2026-11-18 15:59:59"),("9","b","结束之后","2026-11-19 16:00:00")]
        for row in rows:
            c.execute(text("INSERT INTO nlp_turns VALUES (:id,:user,:question,:created)"), dict(zip(["id","user","question","created"],row)))
    options = dict(student_user_ids={"a","b"}, start_date=date(2026,11,19),end_date=date(2026,11,19),timezone_name="Asia/Shanghai",page_size=2)
    result = read_question_activity(engine, **options)
    assert result["summary"] == {"question_count":6,"user_count":2}
    assert result["total"] == 6 and len(result["items"]) == 2
    assert result["frequent_total"] == 1
    assert result["frequent"][0]["question_count"] == 3 and result["frequent"][0]["user_count"] == 2
    filtered = read_question_activity(engine, **options,group_id=result["frequent"][0]["group_id"])
    assert filtered["total"] == 3
    assert read_question_activity(engine, **options,student_id="b")["total"] == 1
    assert read_question_activity(engine, **options,page=2)["items"] != result["items"]
    options["student_user_ids"] = set()
    empty = read_question_activity(engine, **options)
    assert empty["total"] == empty["frequent_total"] == empty["summary"]["question_count"] == 0
    engine.dispose()


@pytest.mark.parametrize("start,end", [
    (date.min, date.min),
    (date.max, date.max),
    (date(1900, 1, 1) - timedelta(days=1), date(1900, 1, 1)),
    (date(2100, 12, 31), date(2100, 12, 31) + timedelta(days=1)),
])
def test_out_of_range_dates_are_rejected_before_arithmetic(start, end):
    engine = create_engine("sqlite://")
    with pytest.raises(ValueError, match="有效时间段"):
        read_question_activity(
            engine, student_user_ids={"a"}, start_date=start, end_date=end,
            timezone_name="Asia/Shanghai",
        )
    engine.dispose()
