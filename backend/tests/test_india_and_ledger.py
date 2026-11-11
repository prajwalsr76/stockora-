from datetime import date, timedelta
from decimal import Decimal

import pytest

from stockora.db import InsufficientStock, Repo, connect
from stockora.india import (festival_multiplier, gst_split, gstin_checksum, is_valid_rate, load_catalog,
                              load_festivals, mrp_violation, validate_gstin)

D = date(2026, 11, 7)


class TestGstin:
    def test_real_gstin_is_valid(self):
        r = validate_gstin("27AAPFU0939F1ZV")
        assert r.valid and r.state == "Maharashtra" and r.pan == "AAPFU0939F"

    @pytest.mark.parametrize("bad,reason", [
        ("27AAPFU0939F1ZX", "checksum"), ("27AAPFU0939F1Z", "length"),
        ("99AAPFU0939F1ZV", "state"), ("27AAPF00939F1ZV", "format"), ("", "missing")])
    def test_invalid(self, bad, reason):
        r = validate_gstin(bad)
        assert not r.valid and reason in r.reason

    def test_checksum_roundtrip(self):
        first14 = "29ABCDE1234F1Z"
        assert validate_gstin(first14 + gstin_checksum(first14)).valid

    def test_lowercase_and_spaces_accepted(self):
        assert validate_gstin(" 27aapfu0939f1zv ").valid


class TestTax:
    def test_intra_state_splits_cgst_sgst(self):
        t = gst_split(1000, 18, "27", "27")
        assert (t.cgst, t.sgst, t.igst) == (Decimal("90.00"), Decimal("90.00"), Decimal("0.00"))

    def test_inter_state_is_igst(self):
        t = gst_split(1000, 5, "27", "29")
        assert t.igst == Decimal("50.00") and t.cgst == 0

    def test_odd_paise_split_sums_exactly(self):
        t = gst_split(10.01, 5, "27", "27")
        assert t.cgst + t.sgst == t.total_tax == Decimal("0.50")

    def test_slabs_respect_reform_date(self):
        assert is_valid_rate(5) and is_valid_rate(40) and not is_valid_rate(12)
        assert is_valid_rate(12, date(2025, 8, 1)) and not is_valid_rate(12, date(2025, 10, 1))

    def test_mrp_rule(self):
        assert mrp_violation(30.01, 30) and not mrp_violation(30, 30)


class TestReference:
    def test_catalog_integrity(self):
        cat = load_catalog()
        assert len(cat) >= 90 and len({c.sku for c in cat}) == len(cat)
        for c in cat:
            assert c.gst_rate in {0, 5, 18, 40} and c.mrp > 0 and c.cost_price < c.mrp and len(c.hsn) == 4

    def test_festival_ramp_peaks_on_the_day(self):
        diwali = next(f for f in load_festivals() if f.name == "Diwali" and f.day.year == 2026)
        before = festival_multiplier(diwali.day - timedelta(days=10), "festive")
        on = festival_multiplier(diwali.day, "festive")
        after = festival_multiplier(diwali.day + timedelta(days=20), "festive")
        assert 1 < before < on and after == 1.0
        assert festival_multiplier(diwali.day, "stationery") == 1.0


class TestLedger:
    def _store(self):
        conn = connect()
        repo = Repo.create_store(conn, "A", "27")
        pid = repo.add_product(sku="X", name="Amul Butter 100g", category="dairy", mrp=58, gst_rate=5,
                               shelf_life_days=180)
        return conn, repo, pid

    def test_fefo_consumes_earliest_expiry_first(self):
        _, repo, pid = self._store()
        late = repo.receive(pid, 10, 50, D, expiry=D + timedelta(days=60))
        early = repo.receive(pid, 5, 50, D, expiry=D + timedelta(days=10))
        repo.sell(pid, 7, 58, D)
        rem = {b["id"]: b["remaining"] for b in repo.open_batches(pid)}
        assert early not in rem and rem[late] == 8 and repo.stock(pid) == 8

    def test_cannot_oversell_or_exceed_mrp(self):
        _, repo, pid = self._store()
        repo.receive(pid, 3, 50, D)
        with pytest.raises(InsufficientStock):
            repo.sell(pid, 4, 58, D)
        with pytest.raises(ValueError):
            repo.sell(pid, 1, 60, D)
        assert repo.stock(pid) == 3

    def test_stockout_is_recorded(self):
        _, repo, pid = self._store()
        repo.receive(pid, 2, 50, D)
        repo.sell(pid, 2, 58, D)
        assert (pid, D.isoformat()) in repo.stockout_set()

    def test_tenant_isolation(self):
        conn, a, pid = self._store()
        b = Repo.create_store(conn, "B", "27")
        a.receive(pid, 5, 50, D)
        assert b.product(pid) is None and b.stock_all() == {} and b.products() == []
        with pytest.raises(KeyError):
            b.sell(pid, 1, 58, D)

    def test_receivables_net_of_payments(self):
        _, repo, _ = self._store()
        c = repo.add_customer("Ramesh")
        repo.credit(c, 1000, D - timedelta(days=20))
        repo.credit(c, -400, D)
        rec = repo.receivables()
        assert rec[0]["due"] == 600 and rec[0]["oldest"] == (D - timedelta(days=20)).isoformat()
