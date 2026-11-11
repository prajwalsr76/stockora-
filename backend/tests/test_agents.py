from datetime import timedelta

import pytest

from stockora.agents import reorder
from stockora.agents.inventory_health import dead_stock, expiry_alerts, lost_sales
from stockora.agents.workflows import daily_ops, resume_daily_ops
from stockora.matching import ProductMatcher, normalize, size_in_base

from .conftest import TODAY

MESSY = {
    "PARLE G GLUCOSE BISC 250GM": "Parle-G Glucose Biscuits 250g",
    "fortune sunflwr refnd oil 1 ltr": "Fortune Sunflower Refined Oil 1L",
    "Tata namak 1kg": "Tata Salt 1kg",
    "maggi masala 70 gm": "Maggi 2-Minute Masala Noodles 70g",
    "amul butr 100g": "Amul Butter 100g",
    "Surf exl easy wash 1 kg": "Surf Excel Easy Wash 1kg",
    "dairy milk 23gm": "Cadbury Dairy Milk 23g",
    "thumsup 750 ml": "Thums Up 750ml",
    "aashirwad atta 5 kg": "Aashirvaad Shudh Chakki Atta 5kg",
}


class TestMatching:
    def test_normalization(self):
        assert normalize("Fortune Oil 1 LTR") == "fortune oil 1l" and size_in_base("Atta 5 KG") == 5000

    def test_messy_names(self, seeded):
        repo = seeded[0].repo
        m = ProductMatcher([(r["product_id"], r["alias"]) for r in repo.aliases()])
        for raw, expected in MESSY.items():
            hit, _ = m.resolve(raw)
            assert hit is not None and repo.product(hit.product_id)["name"] == expected, raw

    def test_unknown_goes_to_review(self, seeded):
        repo = seeded[0].repo
        m = ProductMatcher([(r["product_id"], r["alias"]) for r in repo.aliases()])
        hit, cands = m.resolve("xyz random item 3")
        assert hit is None and cands


class TestReorder:
    @staticmethod
    def cash_plan(b):
        return reorder.plan_orders(b.repo, b.forecasts, b.sigma, b.today, b.observed, cash_aware=True)

    def test_plan_respects_cash_budget(self, seeded):
        plan = self.cash_plan(seeded[0])
        assert plan.total <= plan.budget + 1e-6 and plan.lines
        assert all(l.qty > 0 and l.line_cost > 0 for l in plan.lines)

    def test_cash_shortfall_defers_and_names_customers(self, fresh):
        fresh.repo.set_cash(2000)
        plan = self.cash_plan(fresh)
        assert plan.deferred and plan.collect_from and plan.total <= plan.budget + 1e-6

    def test_app_plan_restocks_everything_needed(self, fresh):
        fresh.repo.set_cash(2000)                         # the app ignores cash: availability first
        plan = fresh.order_plan()
        assert plan.lines and not plan.deferred and not plan.below_minimum

    def test_perishable_goes_to_fastest_supplier_with_short_cover(self, tiny):
        repo, ids = tiny
        plan = reorder.plan_orders(repo, {ids["milk"]: [20.0] * 14}, {ids["milk"]: 3.0}, TODAY)
        milk = next(l for l in plan.lines if l.product_id == ids["milk"])
        assert milk.distributor == "Fast Traders"       # cheaper supplier is 3 days away: milk would spoil
        assert milk.qty <= 20 * 2                       # never more than its 2-day shelf life

    def test_scheme_bump_to_unlock_free_units(self, tiny):
        repo, ids = tiny
        repo.receive(ids["salt"], 10, 24, TODAY)          # 3+ days of stock: not urgent
        plan = reorder.plan_orders(repo, {ids["salt"]: [3.0] * 14}, {ids["salt"]: 0.5}, TODAY)
        salt = next(l for l in plan.lines if l.product_id == ids["salt"])
        assert salt.distributor == "Cheap Wholesale"
        assert salt.qty == 25 and salt.free_units == 1 and "bumped" in salt.scheme_note

    def test_urgent_item_ignores_slow_scheme_supplier(self, tiny):
        repo, ids = tiny                                   # zero stock: must arrive fastest
        plan = reorder.plan_orders(repo, {ids["salt"]: [3.0] * 14}, {ids["salt"]: 0.5}, TODAY)
        salt = next(l for l in plan.lines if l.product_id == ids["salt"])
        assert salt.distributor == "Fast Traders" and "urgent" in salt.reason

    def test_shelf_life_caps_quantity(self, tiny):
        repo, ids = tiny
        plan = reorder.plan_orders(repo, {ids["milk"]: [100.0] * 14}, {ids["milk"]: 50.0}, TODAY)
        milk = next(l for l in plan.lines if l.product_id == ids["milk"])
        assert milk.qty <= 200

    def test_approval_gate_and_preference_learning(self, fresh):
        plan = fresh.order_plan()
        ids = reorder.commit_plan(fresh.repo, plan)
        assert all(fresh.repo.order(i)["status"] == "draft" for i in ids)
        order = fresh.repo.order(ids[0])
        line = order["lines"][0]
        reorder.approve_order(fresh.repo, ids[0], {line["product_id"]: line["qty_suggested"] * 2})
        assert fresh.repo.order(ids[0])["status"] == "approved"
        assert fresh.repo.prefs()[line["product_id"]]["qty_bias"] > 1.0
        assert 0 < reorder.edit_rate(fresh.repo) <= 1
        with pytest.raises(ValueError):
            reorder.approve_order(fresh.repo, ids[0])          # cannot approve twice
        assert fresh.repo.pending_order_qty()[line["product_id"]] == line["qty_suggested"] * 2

    def test_messages_are_drafted_per_distributor(self, seeded):
        plan = seeded[0].order_plan()
        assert plan.messages and all("Namaste" in m and "Rs" in m for m in plan.messages.values())


class TestInventoryHealth:
    def test_expiry_alerts_find_overstocked_perishables(self, seeded):
        alerts = seeded[0].expiry()
        assert alerts and all(a["leftover"] > 0 and a["days_left"] <= 10 for a in alerts)
        assert alerts == sorted(alerts, key=lambda a: (a["days_left"], -a["leftover"]))
        assert all("value_at_risk" not in a for a in alerts)

    def test_expiry_respects_fefo(self, tiny):
        repo, ids = tiny
        repo.receive(ids["salt"], 10, 24, TODAY, expiry=TODAY + timedelta(days=3))
        repo.receive(ids["salt"], 10, 24, TODAY, expiry=TODAY + timedelta(days=5))
        alerts = expiry_alerts(repo, {ids["salt"]: [3.0] * 14}, TODAY)
        by_days = {a["days_left"]: a["leftover"] for a in alerts}
        assert by_days[3] == 1.0 and by_days[5] == 4.0   # 9 sold from first, 6 more from second

    def test_dead_stock(self, seeded):
        assert any(d["name"] == "Dabur Chyawanprash 1kg" for d in dead_stock(seeded[0].repo, TODAY))

    def test_lost_sales_positive_and_consistent(self, seeded):
        ls = lost_sales(seeded[0].repo, TODAY)
        assert ls["stockout_days"] > 0 and ls["lost_units"] > 0 and "lost_revenue" not in ls
        assert ls["stockout_days"] == sum(t["days_out"] for t in ls["top_items"]) or ls["items_affected"] > 10


class TestWorkflow:
    def test_daily_ops_pauses_for_approval(self, fresh):
        state, paused = daily_ops(fresh)
        assert paused == "commit" and "order_ids" not in state
        rejected = resume_daily_ops(state, approved=False)
        assert "order_ids" not in rejected and fresh.repo.orders() == []
        done = resume_daily_ops(state, approved=True)
        assert done["order_ids"] and all(fresh.repo.order(i)["status"] == "draft" for i in done["order_ids"])
