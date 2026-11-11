"""REST API for the Stockora web app. Thin layer: all logic lives in service/agents/genai.

Run:  uvicorn stockora.api.main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import os
import threading
from datetime import date
from pathlib import Path
from typing import Optional

try:  # load backend/.env when python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import area_trends, billing, invoice_pdf
from ..agents import reorder, stock_alerts
from ..connectors import barcode
from .. import model_store
from ..db import InsufficientStock, Repo, connect
from ..genai import intake
from ..genai.assistant import OwnerAssistant
from ..genai.llm import default_llm
from ..service import StoreBrain


class AppState:
    """One open shop at a time (the active store), its forecasts, and the chat assistant.

    Start-up is fast: saved forecasts are loaded at once (stockora/model_store.py). If they are
    from an earlier day, or missing, the model retrains in a background thread on its own database
    connection and the result is swapped in under the lock. A daily check repeats this.
    """

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.db_path = Path(os.getenv("STOCKORA_DB", "stockora.db"))
        self.conn = connect(self.db_path)
        if self.conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0] == 0:
            from ..seed import build_demo_store
            build_demo_store(self.conn, date.today())
        default = os.getenv("STOCKORA_STORE_ID") or Repo.setting(self.conn, "active_store") or "1"
        self.training = False
        self.train_error: str | None = None
        self.model_meta: dict | None = None
        self.llm = default_llm(Repo(self.conn, int(default)))
        self.open_store(int(default), wait_if_untrained=True)
        threading.Thread(target=self._daily_loop, daemon=True).start()

    # ------------------------------------------------------------- stores
    def open_store(self, store_id: int, wait_if_untrained: bool = False) -> None:
        if self.conn.execute("SELECT 1 FROM stores WHERE id=?", (store_id,)).fetchone() is None:
            store_id = self.conn.execute("SELECT MIN(id) FROM stores").fetchone()[0]
        with self.lock:
            self.repo = Repo(self.conn, store_id)
            self.brain = StoreBrain(self.repo)
            self.model_meta = model_store.load(self.brain, self._model_path())
            if self.model_meta is None and wait_if_untrained:
                self.brain.refresh()                         # very first start: nothing saved yet
                self.model_meta = model_store.save(self.brain, self._model_path(), self._learner())
            self.assistant = OwnerAssistant(self.brain, self.llm, agentic=True)
            Repo.set_setting(self.conn, "active_store", str(store_id))
        if self.model_meta is None or (self._auto_retrain() and not model_store.is_current(self.model_meta, date.today())):
            self.retrain_async()

    @staticmethod
    def _auto_retrain() -> bool:
        """STOCKORA_AUTO_RETRAIN=0 keeps saved forecasts until the next deploy (used on the free public
        demo server, whose tiny CPU would take minutes to retrain; the demo is rebuilt daily instead)."""
        return os.getenv("STOCKORA_AUTO_RETRAIN", "1") != "0"

    def _model_path(self) -> Path:
        return model_store.path_for(self.db_path, self.repo.store_id)

    @staticmethod
    def _learner() -> str:
        try:
            import xgboost  # noqa: F401
            return "xgb_tweedie"
        except ImportError:
            return "sk_bag"

    # ------------------------------------------------------------- training
    def retrain_async(self) -> bool:
        with self.lock:
            if self.training:
                return False
            self.training = True
        threading.Thread(target=self._retrain, args=(self.repo.store_id,), daemon=True).start()
        return True

    def _retrain(self, store_id: int) -> None:
        try:
            conn = connect(self.db_path)          # own connection: sqlite objects are not shared across threads
            fresh = StoreBrain(Repo(conn, store_id), date.today()).refresh()
            with self.lock:
                if self.repo.store_id == store_id:      # the owner may have switched shops meanwhile
                    for f in model_store.FIELDS:
                        setattr(self.brain, f, getattr(fresh, f))
                    self.brain.today = fresh.today
                    self.model_meta = model_store.save(self.brain, self._model_path(), self._learner())
                    self.assistant = OwnerAssistant(self.brain, self.llm, agentic=True)
                    self.train_error = None
            conn.close()
        except Exception as e:  # noqa: BLE001 - keep serving the last good forecasts
            self.train_error = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            self.training = False

    def _daily_loop(self) -> None:
        import time
        while True:
            time.sleep(1800)
            if self._auto_retrain() and not self.training and not model_store.is_current(self.model_meta, date.today()):
                self.retrain_async()

    def refresh(self) -> None:
        self.retrain_async()

    def model_status(self) -> dict:
        meta = getattr(self, "model_meta", None) or {}
        return {"trained_for": meta.get("trained_for"), "saved_at": meta.get("saved_at"), "version": meta.get("version"),
                "learner": meta.get("learner"), "retraining": getattr(self, "training", False),
                "last_error": getattr(self, "train_error", None)}


app = FastAPI(title="Stockora API", version="0.1.0",
              description="AI stock manager for Indian kirana stores")
app.add_middleware(CORSMiddleware,
                   allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
                   allow_origin_regex=os.getenv("CORS_ORIGIN_REGEX") or None,
                   allow_methods=["*"], allow_headers=["*"])
_state: AppState | None = None


def state() -> AppState:
    global _state
    if _state is None:
        _state = AppState()
    return _state


def auth(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("STOCKORA_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(401, "invalid API key")


def S() -> AppState:  # noqa: N802
    return state()


Dep = [Depends(auth)]


# ---------------------------------------------------------------- models
class SaleIn(BaseModel):
    product_id: int
    qty: float = Field(gt=0)
    unit_price: Optional[float] = None


class ApproveIn(BaseModel):
    edits: dict[int, float] = {}


class CreditIn(BaseModel):
    customer_id: int
    amount: float
    note: str = ""


class CountIn(BaseModel):
    counted: float = Field(ge=0)


class RequestIn(BaseModel):
    item: str = Field(min_length=1, max_length=120)
    qty: float = Field(default=1, gt=0, le=1000)


class HubReportsIn(BaseModel):
    reports: list[dict] = Field(max_length=200)


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=2000)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    language: Optional[str] = Field(default=None, pattern="^(en|hi)$")
    history: list[Turn] = Field(default_factory=list, max_length=12)


class InvoiceLineIn(BaseModel):
    product_id: int
    qty: float = Field(gt=0, le=100000)
    qty_unit: Optional[str] = Field(default=None, pattern="^(pcs|kg|g|L|l|ml)$")
    unit_price: Optional[float] = Field(default=None, gt=0)
    discount: float = Field(default=0, ge=0)


class PaymentIn(BaseModel):
    cash: float = Field(default=0, ge=0)
    upi: float = Field(default=0, ge=0)
    udhaar: float = Field(default=0, ge=0)


class CustomerIn(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = Field(default=None, max_length=80)
    phone: Optional[str] = Field(default=None, max_length=20)
    gstin: Optional[str] = Field(default=None, max_length=15)
    save: bool = False


class InvoiceIn(BaseModel):
    lines: list[InvoiceLineIn] = Field(min_length=1, max_length=200)
    payment: PaymentIn = Field(default_factory=PaymentIn)
    customer: Optional[CustomerIn] = None
    bill_discount: float = Field(default=0, ge=0)


class CancelIn(BaseModel):
    reason: str = Field(min_length=2, max_length=200)


class ReturnItemIn(BaseModel):
    invoice_line_id: int
    qty: float = Field(gt=0)


class CreditNoteIn(BaseModel):
    items: list[ReturnItemIn] = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=2, max_length=200)
    refund_mode: str = Field(default="cash", pattern="^(cash|upi|udhaar)$")
    restock: bool = True


class StoreSettingsIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    address: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=20)
    city: Optional[str] = Field(default=None, max_length=60)
    gstin: Optional[str] = Field(default=None, max_length=15)
    state_code: Optional[str] = Field(default=None, pattern="^[0-9]{2}$")
    gst_scheme: Optional[str] = Field(default=None, pattern="^(regular|composition|unregistered)$")
    invoice_prefix: Optional[str] = Field(default=None, max_length=5)


class NewShopIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    city: str = Field(default="", max_length=60)
    state_code: str = Field(pattern="^[0-9]{2}$")
    size: str = Field(default="small", pattern="^(small|medium|large)$")
    gst_scheme: str = Field(default="unregistered", pattern="^(regular|composition|unregistered)$")
    gstin: Optional[str] = Field(default=None, max_length=15)
    phone: Optional[str] = Field(default=None, max_length=20)
    address: Optional[str] = Field(default=None, max_length=200)


class StarterItemIn(BaseModel):
    sku: Optional[str] = Field(default=None, max_length=40)
    name: Optional[str] = Field(default=None, max_length=120)
    mrp: Optional[float] = Field(default=None, gt=0)
    gst_rate: Optional[float] = None
    category: Optional[str] = Field(default=None, max_length=40)
    unit: Optional[str] = Field(default=None, max_length=10)
    loose: bool = False
    stock: float = Field(default=0, ge=0, le=1_000_000)


class StarterItemsIn(BaseModel):
    items: list[StarterItemIn] = Field(min_length=1, max_length=2000)


class BarcodeIn(BaseModel):
    barcode: Optional[str] = Field(default=None, max_length=32)


class PostBillIn(BaseModel):
    bill: intake.Bill
    matches: list[dict]
    distributor_id: Optional[int] = None


# ---------------------------------------------------------------- routes
@app.get("/health")
def health():
    state()  # first call builds the demo store and trains the forecaster (~1 min)
    return {"ok": True}


@app.get("/api/store", dependencies=Dep)
def store(s: AppState = Depends(S)):
    st = s.repo.store()
    return {"id": st["id"], "is_demo": bool(st["is_demo"]), "products": len(s.repo.products()),
            "name": st["name"], "city": st["city"], "state_code": st["state_code"], "gstin": st["gstin"],
            "cash_on_hand": st["cash_on_hand"], "llm_enabled": s.llm is not None,
            "llm_model": getattr(s.llm, "models", {}).get("smart") if s.llm is not None else None}


@app.get("/api/status", dependencies=Dep)
def status(s: AppState = Depends(S)):
    with s.lock:
        out = s.brain.status()
        out["model"] = s.model_status() if hasattr(s, "model_status") else {}
        return out


@app.post("/api/stock/{product_id}/count", dependencies=Dep)
def stock_count(product_id: int, body: CountIn, s: AppState = Depends(S)):
    """Physical count: set stock to what is actually on the shelf."""
    with s.lock:
        if s.repo.product(product_id) is None:
            raise HTTPException(404, "product not found")
        with s.repo.tx():
            diff = s.repo.adjust_to_count(product_id, body.counted, date.today())
        return {"ok": True, "stock": s.repo.stock(product_id), "difference": diff}


@app.get("/api/briefing", dependencies=Dep)
def briefing(s: AppState = Depends(S)):
    with s.lock:
        return s.brain.morning_briefing()


@app.get("/api/stock", dependencies=Dep)
def stock(low_only: bool = False, s: AppState = Depends(S)):
    with s.lock:
        return s.brain.stock_summary(low_only=low_only)


@app.get("/api/trend", dependencies=Dep)
def trend(s: AppState = Depends(S)):
    """Whole-shop daily units: last 28 days actual, next 14 days forecast with an 80% range."""
    with s.lock:
        return s.brain.shop_trend()


@app.get("/api/products/{product_id}", dependencies=Dep)
def product_detail(product_id: int, s: AppState = Depends(S)):
    """Everything about one product: 56 days of sales, 14-day forecast with a range, batches, reorder point."""
    with s.lock:
        try:
            return s.brain.product_detail(product_id)
        except KeyError:
            raise HTTPException(404, "product not found")


@app.get("/api/products/{product_id}/forecast", dependencies=Dep)
def forecast(product_id: int, s: AppState = Depends(S)):
    with s.lock:
        try:
            return s.brain.forecast_for(product_id)
        except KeyError:
            raise HTTPException(404, "product not found")


@app.post("/api/forecast/refresh", dependencies=Dep)
def refresh(s: AppState = Depends(S)):
    started = s.retrain_async()
    return {"ok": True, "started": started, "note": "Retraining in the background; forecasts update when it finishes."}


@app.post("/api/sales", dependencies=Dep)
def record_sale(body: SaleIn, s: AppState = Depends(S)):
    with s.lock:
        p = s.repo.product(body.product_id)
        if p is None:
            raise HTTPException(404, "product not found")
        try:
            with s.repo.tx():
                s.repo.sell(body.product_id, body.qty, body.unit_price or p["mrp"], date.today(), "app")
                s.repo.audit("owner", "sale", {"product_id": body.product_id, "qty": body.qty})
        except InsufficientStock as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"ok": True, "stock": s.repo.stock(body.product_id)}


@app.get("/api/orders/plan", dependencies=Dep)
def order_plan(s: AppState = Depends(S)):
    with s.lock:
        return s.brain.order_plan().to_dict()


@app.post("/api/orders/draft", dependencies=Dep)
def draft_orders(s: AppState = Depends(S)):
    with s.lock:
        ids = reorder.commit_plan(s.repo, s.brain.order_plan())
        return {"order_ids": ids}


@app.get("/api/orders", dependencies=Dep)
def list_orders(status: Optional[str] = None, s: AppState = Depends(S)):
    with s.lock:
        return s.repo.orders(status)


@app.post("/api/orders/{order_id}/approve", dependencies=Dep)
def approve(order_id: int, body: ApproveIn, s: AppState = Depends(S)):
    with s.lock:
        try:
            return reorder.approve_order(s.repo, order_id, body.edits)
        except KeyError:
            raise HTTPException(404, "order not found")
        except ValueError as e:
            raise HTTPException(409, str(e))


@app.post("/api/orders/{order_id}/reject", dependencies=Dep)
def reject(order_id: int, s: AppState = Depends(S)):
    with s.lock:
        reorder.reject_order(s.repo, order_id)
        return {"ok": True}


@app.get("/api/expiry", dependencies=Dep)
def expiry(s: AppState = Depends(S)):
    with s.lock:
        return s.brain.expiry()


@app.get("/api/dead-stock", dependencies=Dep)
def dead(s: AppState = Depends(S)):
    with s.lock:
        return s.brain.dead_stock()


@app.get("/api/lost-sales", dependencies=Dep)
def lost(days: int = 30, s: AppState = Depends(S)):
    with s.lock:
        return s.brain.lost_sales(days)


@app.get("/api/udhaar", dependencies=Dep)
def udhaar(s: AppState = Depends(S)):
    with s.lock:
        return s.repo.receivables()


@app.post("/api/udhaar", dependencies=Dep)
def add_credit(body: CreditIn, s: AppState = Depends(S)):
    with s.lock, s.repo.tx():
        s.repo.credit(body.customer_id, body.amount, date.today(), body.note)
    return {"ok": True}


@app.post("/api/assistant/ask", dependencies=Dep)
def ask(body: AskIn, s: AppState = Depends(S)):
    with s.lock:
        return s.assistant.ask(body.question, body.language, [t.model_dump() for t in body.history]).to_dict()


@app.post("/api/bills/extract", dependencies=Dep)
async def extract(file: UploadFile = File(...), s: AppState = Depends(S)):
    if s.llm is None:
        raise HTTPException(503, "Set GROQ_API_KEY to read bill photos")
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "image too large (max 8 MB)")
    bill, err = intake.extract_bill(s.llm, data, file.content_type or "image/jpeg")
    if bill is None:
        raise HTTPException(422, err)
    with s.lock:
        return {"bill": bill.model_dump(mode="json"),
                "issues": intake.validate_bill(bill, s.repo.store()["state_code"], s.repo),
                "matches": intake.match_lines(s.repo, bill)}


@app.post("/api/bills/validate", dependencies=Dep)
def validate(bill: intake.Bill, s: AppState = Depends(S)):
    with s.lock:
        issues = intake.validate_bill(bill, s.repo.store()["state_code"], s.repo)
        matches = intake.match_lines(s.repo, bill)
        return {"issues": issues, "matches": matches, "needs_review": intake.review_needed(issues, matches)}


@app.post("/api/bills/post", dependencies=Dep)
def post(body: PostBillIn, s: AppState = Depends(S)):
    with s.lock:
        issues = intake.validate_bill(body.bill, s.repo.store()["state_code"], s.repo)
        if any(i["severity"] == "error" for i in issues):
            raise HTTPException(422, {"issues": issues})
        try:
            pid = intake.post_bill(s.repo, body.bill, body.matches, distributor_id=body.distributor_id)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"purchase_id": pid}


@app.get("/api/barcode/{code}", dependencies=Dep)
def barcode_lookup(code: str):
    try:
        found = barcode.lookup(code)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if found is None:
        raise HTTPException(404, "barcode not found in Open Food Facts")
    return found


@app.get("/api/alerts", dependencies=Dep)
def alerts(s: AppState = Depends(S)):
    """Missed customer requests, stock-count mismatches and udhaar risk."""
    with s.lock:
        return stock_alerts.alerts_summary(s.repo, date.today())


@app.post("/api/requests", dependencies=Dep)
def missed_request(body: RequestIn, s: AppState = Depends(S)):
    """A customer asked for something not on the shelf ("kisi ne maanga")."""
    with s.lock, s.repo.tx():
        return stock_alerts.log_request(s.repo, body.item, date.today(), body.qty)


def _area() -> str:
    return os.getenv("STOCKORA_AREA", "")


@app.get("/api/area/my-report", dependencies=Dep)
def my_area_report(s: AppState = Depends(S)):
    """Exactly what this shop would share: noisy category-level changes, nothing else."""
    if not _area():
        return {"area": None, "reports": [], "note": "Set STOCKORA_AREA (e.g. first 4 digits of your PIN code)"}
    with s.lock:
        reps = area_trends.shop_reports(s.repo, date.today(), _area())
    return {"area": _area(), "reports": [r.__dict__ for r in reps]}


@app.post("/api/area/share", dependencies=Dep)
def share_area_report(s: AppState = Depends(S)):
    """Send this week's anonymous report to the area hub (AREA_HUB_URL)."""
    hub = os.getenv("AREA_HUB_URL")
    if not hub or not _area():
        raise HTTPException(503, "Set STOCKORA_AREA and AREA_HUB_URL to join area trends")
    import requests
    with s.lock:
        reps = [r.__dict__ for r in area_trends.shop_reports(s.repo, date.today(), _area())]
    r = requests.post(hub.rstrip("/") + "/hub/reports", json={"reports": reps}, timeout=15)
    r.raise_for_status()
    return {"sent": len(reps)}


@app.get("/api/area/trends", dependencies=Dep)
def area_trends_for_me():
    hub, area = os.getenv("AREA_HUB_URL"), _area()
    if not hub or not area:
        return {"area": area or None, "trends": [], "joined": False}
    import requests
    r = requests.get(hub.rstrip("/") + "/hub/trends", params={"area": area}, timeout=15)
    r.raise_for_status()
    return {"area": area, "joined": True, "trends": r.json()}


@app.post("/hub/reports")
def hub_receive(body: HubReportsIn, s: AppState = Depends(S)):
    """Hub endpoint (central server): accepts anonymous reports only."""
    with s.lock:
        return {"stored": area_trends.hub_store(s.conn, body.reports)}


@app.get("/hub/trends")
def hub_get(area: str, week: Optional[str] = None, s: AppState = Depends(S)):
    with s.lock:
        ts = area_trends.hub_trends(s.conn, area, week)
    return [{**t.__dict__, "message": t.message()} for t in ts]


MODEL_CARD = Path(__file__).resolve().parents[1] / "reference" / "model_card.json"


@app.get("/api/model-card", dependencies=Dep)
def model_card():
    """How accurate the forecaster was on real public datasets (written by the eval scripts)."""
    import json
    if not MODEL_CARD.exists():
        return {"model": None, "tests": []}
    return json.loads(MODEL_CARD.read_text(encoding="utf-8"))


@app.get("/api/metrics", dependencies=Dep)
def metrics(s: AppState = Depends(S)):
    with s.lock:
        r = s.repo.one("SELECT COUNT(*) n, SUM(cost_usd) cost, AVG(latency_ms) lat, AVG(ok) ok "
                       "FROM llm_traces WHERE store_id=?", s.repo.store_id)
        return {"llm_calls": r["n"], "llm_cost_usd": round(r["cost"] or 0, 4),
                "avg_latency_ms": round(r["lat"] or 0), "success_rate": r["ok"],
                "owner_edit_rate": reorder.edit_rate(s.repo)}


# ---------------------------------------------------------------- counter billing (invoices)
def _billing_errors(fn):
    try:
        return fn()
    except billing.BillingError as e:
        raise HTTPException(422, str(e))
    except InsufficientStock as e:
        raise HTTPException(409, str(e))


def _pdf(content: bytes, filename: str) -> Response:
    return Response(content, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


@app.get("/api/store/settings", dependencies=Dep)
def get_store_settings(s: AppState = Depends(S)):
    with s.lock:
        return billing.store_settings(s.repo)


@app.put("/api/store/settings", dependencies=Dep)
def put_store_settings(body: StoreSettingsIn, s: AppState = Depends(S)):
    with s.lock:
        return _billing_errors(lambda: billing.update_store_settings(s.repo, **body.model_dump(exclude_unset=True)))


@app.get("/api/billing/products", dependencies=Dep)
def billing_products(s: AppState = Depends(S)):
    with s.lock:
        return billing.counter_products(s.repo)


@app.get("/api/billing/barcode/{code}", dependencies=Dep)
def billing_barcode(code: str, s: AppState = Depends(S)):
    with s.lock:
        p = billing.product_by_barcode(s.repo, code)
    if p is None:
        raise HTTPException(404, "no product has this barcode yet")
    return p


@app.post("/api/products/{product_id}/barcode", dependencies=Dep)
def link_barcode(product_id: int, body: BarcodeIn, s: AppState = Depends(S)):
    with s.lock:
        return _billing_errors(lambda: billing.set_barcode(s.repo, product_id, body.barcode))


@app.get("/api/customers", dependencies=Dep)
def customers(q: str = "", s: AppState = Depends(S)):
    with s.lock:
        like = f"%{q.strip()}%"
        rows = s.repo.q("SELECT c.id, c.name, c.phone, COALESCE(SUM(e.amount),0) AS due FROM customers c "
                        "LEFT JOIN credit_entries e ON e.customer_id=c.id WHERE c.store_id=? AND "
                        "(c.name LIKE ? OR COALESCE(c.phone,'') LIKE ?) GROUP BY c.id ORDER BY c.name LIMIT 30",
                        s.repo.store_id, like, like)
        return [dict(r) for r in rows]


@app.post("/api/invoices", dependencies=Dep)
def create_invoice(body: InvoiceIn, s: AppState = Depends(S)):
    with s.lock:
        return _billing_errors(lambda: billing.create_invoice(
            s.repo, [ln.model_dump() for ln in body.lines], body.payment.model_dump(),
            body.customer.model_dump() if body.customer else None, body.bill_discount, date.today()))


@app.get("/api/invoices/summary", dependencies=Dep)
def invoice_summary(day: Optional[date] = None, s: AppState = Depends(S)):
    with s.lock:
        return billing.daily_summary(s.repo, day or date.today())


@app.get("/api/invoices", dependencies=Dep)
def list_invoices(q: str = "", day_from: Optional[str] = Query(default=None, alias="from"),
                  day_to: Optional[str] = Query(default=None, alias="to"), status: Optional[str] = None,
                  limit: int = Query(default=50, ge=1, le=500), s: AppState = Depends(S)):
    with s.lock:
        return billing.list_invoices(s.repo, q, day_from, day_to, status, limit)


def _invoice_or_404(s: AppState, invoice_id: int) -> dict:
    inv = billing.get_invoice(s.repo, invoice_id)
    if inv is None:
        raise HTTPException(404, "bill not found")
    return inv


@app.get("/api/invoices/{invoice_id}", dependencies=Dep)
def get_invoice(invoice_id: int, s: AppState = Depends(S)):
    with s.lock:
        return _invoice_or_404(s, invoice_id)


@app.get("/api/invoices/{invoice_id}/pdf", dependencies=Dep)
def invoice_pdf_file(invoice_id: int, layout: str = Query(default="a4", pattern="^(a4|80mm|58mm)$"),
                     s: AppState = Depends(S)):
    with s.lock:
        inv = _invoice_or_404(s, invoice_id)
        shop = billing.store_settings(s.repo)
    return _pdf(invoice_pdf.render(inv, shop, layout), f"{inv['number'].replace('/', '-')}.pdf")


@app.get("/api/invoices/{invoice_id}/whatsapp", dependencies=Dep)
def invoice_whatsapp(invoice_id: int, s: AppState = Depends(S)):
    from urllib.parse import quote
    with s.lock:
        inv = _invoice_or_404(s, invoice_id)
        text = billing.whatsapp_text(inv, billing.store_settings(s.repo)["name"])
    digits = "".join(ch for ch in (inv.get("customer_phone") or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return {"text": text, "url": f"https://wa.me/{digits}?text={quote(text)}" if digits
            else f"https://wa.me/?text={quote(text)}"}


@app.post("/api/invoices/{invoice_id}/cancel", dependencies=Dep)
def cancel_invoice(invoice_id: int, body: CancelIn, s: AppState = Depends(S)):
    with s.lock:
        return _billing_errors(lambda: billing.cancel_invoice(s.repo, invoice_id, body.reason, date.today()))


@app.post("/api/invoices/{invoice_id}/credit-notes", dependencies=Dep)
def create_credit_note(invoice_id: int, body: CreditNoteIn, s: AppState = Depends(S)):
    with s.lock:
        return _billing_errors(lambda: billing.create_credit_note(
            s.repo, invoice_id, [i.model_dump() for i in body.items], body.reason, body.refund_mode,
            body.restock, date.today()))


@app.get("/api/credit-notes/{cn_id}/pdf", dependencies=Dep)
def credit_note_pdf(cn_id: int, layout: str = Query(default="a4", pattern="^(a4|80mm|58mm)$"),
                    s: AppState = Depends(S)):
    with s.lock:
        cn = billing.get_credit_note(s.repo, cn_id)
        if cn is None:
            raise HTTPException(404, "credit note not found")
        shop = billing.store_settings(s.repo)
    return _pdf(invoice_pdf.render(cn, shop, layout, kind="credit_note"), f"{cn['number'].replace('/', '-')}.pdf")


# ---------------------------------------------------------------- shops and first-run setup
@app.get("/api/shops", dependencies=Dep)
def shops(s: AppState = Depends(S)):
    rows = s.conn.execute("SELECT id, name, city, state_code, size, is_demo FROM stores ORDER BY id").fetchall()
    return [{**dict(r), "is_demo": bool(r["is_demo"]), "active": r["id"] == s.repo.store_id} for r in rows]


@app.post("/api/shops", dependencies=Dep)
def create_shop(body: NewShopIn, s: AppState = Depends(S)):
    from ..india import STATE_CODES
    if body.state_code not in STATE_CODES:
        raise HTTPException(422, "unknown state code")
    with s.lock:
        repo = Repo.create_store(s.conn, body.name.strip(), body.state_code, body.city.strip())
        s.conn.execute("UPDATE stores SET size=?, invoice_prefix=? WHERE id=?",
                       (body.size, billing.clean_prefix("".join(w[0] for w in body.name.split()[:4]) or "INV"),
                        repo.store_id))
        s.conn.commit()
        try:
            billing.update_store_settings(repo, gst_scheme=body.gst_scheme, gstin=body.gstin or "",
                                          phone=body.phone, address=body.address)
        except billing.BillingError as e:
            s.conn.execute("DELETE FROM stores WHERE id=?", (repo.store_id,))
            s.conn.commit()
            raise HTTPException(422, str(e))
        s.open_store(repo.store_id)
    return {"id": repo.store_id}


@app.post("/api/shops/{store_id}/open", dependencies=Dep)
def open_shop(store_id: int, s: AppState = Depends(S)):
    if s.conn.execute("SELECT 1 FROM stores WHERE id=?", (store_id,)).fetchone() is None:
        raise HTTPException(404, "shop not found")
    s.open_store(store_id)
    return {"id": store_id}


@app.get("/api/catalog", dependencies=Dep)
def catalog():
    from ..india import load_catalog
    return [{"sku": c.sku, "name": c.name, "brand": c.brand, "category": c.category, "mrp": c.mrp,
             "gst_rate": c.gst_rate, "unit": c.unit, "pack_size": c.pack_size} for c in load_catalog()]


@app.post("/api/shops/current/items", dependencies=Dep)
def add_starter_items(body: StarterItemsIn, s: AppState = Depends(S)):
    from .. import onboarding
    with s.lock:
        rep = onboarding.add_items(s.repo, [i.model_dump() for i in body.items], date.today())
    s.retrain_async()
    return {"added": rep.added, "updated": rep.updated, "skipped": rep.skipped}


def _upload_to_temp(file: UploadFile, data: bytes) -> Path:
    import tempfile
    suffix = Path(file.filename or "upload.csv").suffix.lower()
    if suffix not in (".csv", ".xlsx", ".xls"):
        raise HTTPException(422, "upload a .csv or .xlsx file")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "file is larger than 20 MB")
    tmp = Path(tempfile.mkstemp(suffix=suffix)[1])
    tmp.write_bytes(data)
    return tmp


@app.post("/api/import/preview", dependencies=Dep)
async def import_preview(kind: str = Query(pattern="^(products|sales)$"), file: UploadFile = File(...)):
    from .. import onboarding
    tmp = _upload_to_temp(file, await file.read())
    try:
        return onboarding.preview(tmp, kind)
    except Exception as e:  # noqa: BLE001 - unreadable spreadsheet
        raise HTTPException(422, f"could not read the file: {str(e)[:150]}")
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/api/import", dependencies=Dep)
async def import_file(kind: str = Query(pattern="^(products|sales)$"), mapping: str = Query(default="{}"),
                      file: UploadFile = File(...), s: AppState = Depends(S)):
    import json as _json

    from .. import onboarding
    try:
        cols = _json.loads(mapping)
        assert isinstance(cols, dict)
    except (ValueError, AssertionError):
        raise HTTPException(422, "mapping must be a JSON object")
    tmp = _upload_to_temp(file, await file.read())
    try:
        with s.lock:
            if kind == "products":
                rep = onboarding.import_products(s.repo, tmp, date.today(), cols)
            else:
                rep = onboarding.import_sales_history(s.repo, tmp, "wizard upload", cols)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"could not import the file: {str(e)[:150]}")
    finally:
        tmp.unlink(missing_ok=True)
    s.retrain_async()
    return {"added": rep.added, "updated": rep.updated, "skipped": rep.skipped[:50], "filled": rep.filled[:50],
            "warnings": rep.warnings[:20], "ignored_columns": rep.unknown_columns}
