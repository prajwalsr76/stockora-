"""Runs where FastAPI / LangGraph are installed (your machine, CI). Skipped otherwise."""
import pytest

from .conftest import TODAY


@pytest.fixture
def client(fresh, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from stockora.api import main
    from stockora.genai.assistant import OwnerAssistant

    state = main.AppState.__new__(main.AppState)
    import threading
    state.lock = threading.RLock()
    state.conn, state.repo, state.brain, state.llm = fresh.repo.conn, fresh.repo, fresh, None
    state.assistant = OwnerAssistant(fresh)
    monkeypatch.setattr(main, "_state", state)
    monkeypatch.delenv("STOCKORA_API_KEY", raising=False)
    return TestClient(main.app)


def test_read_endpoints(client):
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/api/store").json()["llm_enabled"] is False
    for path in ["/api/briefing", "/api/stock?low_only=true", "/api/orders/plan", "/api/expiry",
                 "/api/dead-stock", "/api/lost-sales", "/api/alerts", "/api/udhaar", "/api/metrics",
                 "/api/products/1/forecast"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/api/products/99999/forecast").status_code == 404


def test_order_approval_flow(client):
    ids = client.post("/api/orders/draft").json()["order_ids"]
    assert ids
    order = client.get("/api/orders?status=draft").json()[0]
    pid = order["lines"][0]["product_id"]
    r = client.post(f"/api/orders/{order['id']}/approve", json={"edits": {str(pid): 1}})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert client.post(f"/api/orders/{order['id']}/approve", json={"edits": {}}).status_code == 409


def test_sale_rules(client):
    stock = {r["product_id"]: r for r in client.get("/api/stock").json()}
    pid = next(p for p, r in stock.items() if r["stock"] >= 1)
    assert client.post("/api/sales", json={"product_id": pid, "qty": 1}).status_code == 200
    assert client.post("/api/sales", json={"product_id": pid, "qty": 1, "unit_price": 99999}).status_code == 422
    assert client.post("/api/sales", json={"product_id": pid, "qty": 10**6}).status_code == 409


def test_assistant_and_bill_validation(client):
    r = client.post("/api/assistant/ask", json={"question": "udhaar kitna baaki hai"}).json()
    assert r["intent"] == "udhaar"
    assert client.post("/api/bills/extract", files={"file": ("b.jpg", b"x", "image/jpeg")}).status_code == 503


def test_api_key_required_when_configured(client, monkeypatch):
    monkeypatch.setenv("STOCKORA_API_KEY", "secret")
    assert client.get("/api/store").status_code == 401
    assert client.get("/api/store", headers={"X-API-Key": "secret"}).status_code == 200


def test_langgraph_daily_ops_interrupts_before_commit(fresh):
    pytest.importorskip("langgraph")
    from stockora.agents.workflows import build_langgraph_daily_ops

    graph = build_langgraph_daily_ops(fresh)
    cfg = {"configurable": {"thread_id": "t1"}}
    graph.invoke({"log": []}, cfg)
    snap = graph.get_state(cfg)
    assert snap.next == ("commit",) and fresh.repo.orders() == []
    graph.update_state(cfg, {"approved": True})
    final = graph.invoke(None, cfg)
    assert final["order_ids"] and all(fresh.repo.order(i)["status"] == "draft" for i in final["order_ids"])
