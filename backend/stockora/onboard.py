"""Command line onboarding for a real shop.

  # 1. Blank templates to fill in (or drop in billing-app exports with these names)
  python -m stockora.onboard templates --folder shop_data

  # 2. Create the shop and import everything in the folder
  python -m stockora.onboard create --db shop.db --name "Patil Kirana" --city Nashik --state 27 ^
         --cash 25000 --folder shop_data

  # 3. Import more later (e.g. a new sales export)
  python -m stockora.onboard import --db shop.db --folder new_exports

  # 4. Check what Stockora knows
  python -m stockora.onboard status --db shop.db

Then start the app on this shop:  set STOCKORA_DB=shop.db  (or put it in backend/.env)
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from .db import Repo, connect
from .india import STATE_CODES, validate_gstin
from .onboarding import onboard_folder, write_templates
from .service import StoreBrain


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m stockora.onboard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("templates", help="write blank CSV templates")
    t.add_argument("--folder", required=True)
    c = sub.add_parser("create", help="create a shop and import a folder of files")
    c.add_argument("--db", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--city", default="")
    c.add_argument("--state", required=True, help="2-digit GST state code, e.g. 27 for Maharashtra")
    c.add_argument("--gstin", default=None)
    c.add_argument("--cash", type=float, default=0, help="cash available for purchases (Rs)")
    c.add_argument("--folder", default=None)
    i = sub.add_parser("import", help="import a folder into an existing shop")
    i.add_argument("--db", required=True)
    i.add_argument("--store", type=int, default=1)
    i.add_argument("--folder", required=True)
    s = sub.add_parser("status", help="show what is loaded and forecasting mode")
    s.add_argument("--db", required=True)
    s.add_argument("--store", type=int, default=1)
    a = ap.parse_args(argv)

    if a.cmd == "templates":
        made = write_templates(a.folder)
        print(f"Wrote {len(made)} templates to {a.folder}: products, distributors, prices, sales (.csv)")
        return 0

    conn = connect(a.db)
    today = date.today()
    if a.cmd == "create":
        if a.state not in STATE_CODES:
            print(f"Unknown state code {a.state}. Examples: 27 Maharashtra, 29 Karnataka, 07 Delhi, 24 Gujarat")
            return 2
        if a.gstin and not validate_gstin(a.gstin).valid:
            print(f"GSTIN {a.gstin} is invalid: {validate_gstin(a.gstin).reason}")
            return 2
        repo = Repo.create_store(conn, a.name, a.state, a.city, a.gstin, a.cash)
        print(f"Created shop #{repo.store_id}: {a.name} ({STATE_CODES[a.state]}) in {a.db}")
        folder = a.folder
    else:
        repo = Repo(conn, a.store)
        if repo.store() is None:
            print(f"No shop #{a.store} in {a.db}")
            return 2
        folder = getattr(a, "folder", None)

    if folder:
        for r in onboard_folder(repo, folder, today):
            print(r.summary())
        print(f"Full report: {folder}/onboarding_report.json")

    prods = repo.products()
    brain = StoreBrain(repo, today).refresh()
    st = brain.status()
    print(f"\nShop: {repo.store()['name']} | products {len(prods)} | distributors {len(repo.distributors())} | "
          f"days of sales history {st['history_days']}")
    if st["mode"] == "learning":
        print(f"Forecasting mode: learning ({st['history_days']} of {st['model_min_days']} days; "
              "uses recent sales + typical demand for similar products until then)")
    else:
        print("Forecasting mode: trained model")
    no_price = [p["name"] for p in prods if not repo.prices_for(p["id"])]
    if no_price:
        print(f"{len(no_price)} products have no distributor price yet, so they cannot be ordered: "
              + ", ".join(no_price[:8]) + (" ..." if len(no_price) > 8 else ""))
    print(f"\nStart the app on this shop with STOCKORA_DB={a.db}" + (f" STOCKORA_STORE_ID={repo.store_id}"
                                                                     if repo.store_id != 1 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
