"""서비스 이용통계 (operator): Flow per KST day + Stock, counts only; stored so history survives the 7-day retention."""
from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.db.models_stats import UsageDaily
from backend.app.services import review as rsvc
from backend.app.services import usage
from backend.app.services.consult import today_kst
from tests.test_api import DOG, ask, env, start  # noqa: F401  (fixture)

H = {"X-TwinVet-Client": "web"}


def test_flow_and_stock_counts_and_operator_only_api(env):  # noqa: F811
    c = env.client
    assert c.put("/api/v1/profile", json={"dog": {**DOG, "name": "두부"}}, headers=H).status_code == 200
    cid = start(c)
    ask(c, cid)
    env.drain()
    ans = [m for m in c.get(f"/api/v1/consultations/{cid}/messages").json()["messages"] if m["role"] == "assistant"][0]
    c.put(f"/api/v1/messages/{ans['id']}/feedback", json={"helpful": True, "comment": None}, headers=H)

    with env.app.state.Session() as s:
        op_key = rsvc.create_reviewer(s, "operator", "운영자")[1]
        vet_key = rsvc.create_reviewer(s, "vet", "검토수의")[1]
    op = TestClient(env.app, base_url="http://localhost:8000")
    assert op.post("/api/v1/review/login", json={"access_key": op_key}, headers=H).status_code == 200
    vet = TestClient(env.app, base_url="http://localhost:8000")
    vet.post("/api/v1/review/login", json={"access_key": vet_key}, headers=H)
    assert vet.get("/api/v1/review/usage").status_code == 403
    assert TestClient(env.app).get("/api/v1/review/usage").status_code == 401

    r = op.get("/api/v1/review/usage?days=7").json()
    t = r["flow"]["totals"]
    assert t["consultations"] == 1 and t["questions"] == 1 and t["grounded"] == 1 and t["failed"] == 0
    assert t["feedback_helpful"] == 1 and t["profiles_created"] == 1 and t["answer_ms_avg"] is not None
    today = [d for d in r["flow"]["by_day"] if d["day"] == today_kst().isoformat()][0]
    assert today["consultations"] == 1
    st = r["stock"]["now"]
    assert st["consultations_kept"] == 1 and st["profiles_kept"] == 1 and st["sessions_live"] >= 1
    assert r["stock"]["by_day"][-1]["day"] == today_kst().isoformat()
    body = str(r)
    assert "두부" not in body and "토했어요" not in body and cid not in body  # counts only


def test_history_survives_retention_and_old_days_are_frozen(env):  # noqa: F811
    c = env.client
    cid = start(c)
    ask(c, cid)
    env.drain()
    old = today_kst() - timedelta(days=30)
    with env.app.state.Session() as s:
        # a day already stored long ago (raw records gone): must never be recomputed
        s.add(UsageDaily(day=old, flow={"consultations": 5, "questions": 9}, stock={"profiles_kept": 2}))
        s.commit()
        usage.fill(s, env.settings)
        # raw consultation deleted by retention after today's numbers were stored
        s.execute(text("DELETE FROM consultation"))
        s.commit()
        r = usage.report(s, env.settings, None)
    by_day = {d["day"]: d for d in r["flow"]["by_day"]}
    assert by_day[old.isoformat()]["consultations"] == 5  # frozen
    # today is still inside the retention window, so it is recomputed from what is left (0 now) — the stored
    # history that matters is for days whose raw records are gone, which stay as stored
    assert r["flow"]["totals"]["consultations"] >= 5
    assert any(d["day"] == old.isoformat() and d["profiles_kept"] == 2 for d in r["stock"]["by_day"])
    with env.app.state.Session() as s:
        first = s.execute(text("SELECT count(*) FROM usage_daily")).scalar()
        usage.fill(s, env.settings)
        assert s.execute(text("SELECT count(*) FROM usage_daily")).scalar() == first  # idempotent
