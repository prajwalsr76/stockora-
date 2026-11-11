# Stockora on real retail data

**Dataset:** Corporación Favorita, Kaggle "Store Sales – Time Series Forecasting". Real daily sales from 54 grocery stores in 22 Ecuadorian cities, 33 product families, 1 Jan 2013 – 15 Aug 2017, with promotions and national, regional and local holidays.

**Second dataset (item level):** Walmart M5, Kaggle "M5 Forecasting – Accuracy". Real daily sales of individual products in 10 stores (California, Texas, Wisconsin), Jan 2011 – Jun 2016, with weekly shelf prices, US events and SNAP days. See section 4.

**What they are not:** Indian or kirana. Favorita is store × product-family per day (e.g. "DAIRY at store 44"); M5 is item level but American. No public real item-level Indian kirana dataset exists, so nothing in this report is evidence of accuracy in Indian shops. That needs a real-shop pilot, which is on hold until shop data can be collected (`PILOT.md` is the ready kit).

Reproduce: `python -m evals.favorita_benchmark <folder>`, `python -m evals.real_policy <folder>`, `python -m evals.area_eval <folder>`, `python -m evals.m5_benchmark <folder>`, `python -m evals.m5_policy <folder>`.

## 1. Forecast accuracy

Three test periods (origins 31 May, 30 Jun and 30 Jul 2017). Each time the model trains only on data before the origin and forecasts the next 16 days (Kaggle's horizon) for all 1,782 series. Closed days are not scored.

| Method | Daily WAPE | Weekly WAPE | RMSLE | Bias | WAPE on holidays |
|---|---|---|---|---|---|
| **Stockora model** | **0.132** | **0.088** | **0.436** | +4.1% | **0.200** |
| Same weekday, last 4 weeks | 0.152 | 0.111 | 0.483 | −2.5% | 0.210 |

13% lower daily error and 21% lower weekly error than the strongest simple baseline. On the single latest period, a 28-day moving average scored 0.208 daily and "same weekday last week" 0.207.

**Feature ablation** (same three periods):

| Variant | Daily WAPE | Weekly WAPE | Holiday-day WAPE |
|---|---|---|---|
| Final features | 0.132 | 0.088 | 0.200 |
| + yesterday and 3-day features | 0.132 | 0.089 | 0.202 |
| − last-year seasonal feature | 0.136 | 0.092 | 0.278 |

Last year's pattern for the same weeks is what handles holidays. Yesterday's sales add nothing, so they are not used.

**Bugs found on real data and fixed:** forecasts overflowed (10^38) for series that restarted after a dead month (robust scale, capped targets and forecasts); safety stock was sized from in-sample errors (now out-of-sample).

## 2. Ordering on real demand

Real daily demand for 162 series (12 stores × kirana-type families: groceries, beverages, dairy, bread, eggs, produce, cleaning, personal care, school supplies, prepared and frozen foods), 21 Jun – 15 Aug 2017. Both rules see the same demand, starting stock, delivery times (1 day fresh, 2 days others) and shelf lives (bread 4, produce 5, dairy 10, eggs 14 days). Favorita has no prices, so results are in units.

| | Gut feel | Stockora (safety 2.5) | Stockora (safety 3.0) |
|---|---|---|---|
| Days an item was out of stock | 4.3% | 2.5% | **1.8% (−57%)** |
| Demand served | 98.4% | 99.4% | **99.6%** |
| Stock held, days of sales | 3.43 | **2.85 (−17%)** | 3.01 (−12%) |

Perishables only: gut feel 1.6% stockout days with 1.41 days of stock; Stockora (3.0) 1.8% with **0.78 days (−45% stock)**.

Expired stock was negligible for both once fresh items are ordered daily (under 0.03% of demand), so this test does not show a waste difference.

**Bug found and fixed:** dairy (10-day shelf life) and eggs (14) were on a weekly order cycle, which their shelf life cannot support; Stockora could never order enough. Fresh items now reorder daily (≤7 days shelf life) or every 3 days (≤21). The same fix applies to the app's reorder agent.

## 3. Area trends with privacy

Cities as areas, stores as shops, 26 weeks, kirana-type families. Each shop sends only a clipped, noisy, category-level change; an area is published only if at least k shops reported.

| k | Privacy ε | Area/category/weeks published | Direction correct on clear moves |
|---|---|---|---|
| 5 | 2 (default) | 714 of 7,719 (9%) | 75% |
| 5 | 4 | 714 | 88% |
| 5 | no noise | 714 | 99.5% |
| 3 | 2 | 1,397 (18%) | 70% |

Only dense areas qualify at k = 5 (Quito, 18 stores; Guayaquil, 8). The area trend correlates with a shop's next-week change about as much as the shop's own last week does (0.19–0.22 at light noise): a useful heads-up, not a strong predictor.

## 4. Individual items: Walmart M5

24,840 series: every FOODS and HOUSEHOLD item in all 10 stores (toys and crafts left out, a kirana does not sell them). Two test periods of 28 days (M5's horizon): 25 Apr – 22 May 2016 (the competition's validation window) and 23 May – 19 Jun 2016 (the official final test window, scored against the truth released after the competition). Each time the model trains only on earlier data. One model per store, trained across all of that store's items, as each shop would run it. Days an item was not yet listed or had no shelf price are not scored.

| Method | Daily WAPE | Weekly WAPE | RMSLE | Bias | RMSSE (M5 metric) | RMSSE, revenue-weighted |
|---|---|---|---|---|---|---|
| **Stockora model** | **0.713** | **0.374** | **0.548** | −1.0% | **0.788** | **0.869** |
| Average of last 28 days | 0.731 | 0.395 | 0.564 | −1.8% | 0.801 | 0.901 |
| Same weekday, last 4 weeks | 0.758 | 0.395 | 0.599 | −1.8% | 0.865 | 0.956 |
| Same weekday last week | 0.856 | 0.429 | 0.703 | −4.5% | 1.046 | 1.141 |

The model has the lowest weekly error in **all 10 stores** (0.364–0.410 vs 0.375–0.422 for the best baseline). The margin is smaller than on Favorita (5% vs 21% less weekly error): a single item sells 0–2 units on most days, so most of the daily variation is noise no model can predict. RMSSE is M5's own per-series metric at item level; it is not comparable to the competition leaderboard score, which averages 12 aggregation levels (most of them much easier than single items).

**Feature ablation on store CA_1** (both test periods, weekly WAPE):

| Variant | Weekly WAPE |
|---|---|
| Base features (as on Favorita) | 0.3729 |
| + shelf-price features (price change ahead, price vs 13-week level) | 0.3749 |
| + 91-day level and days since last sale | **0.3715** (used) |

Shelf prices did not help: within a 28-day window prices rarely change. "Days since last sale" helps slow, intermittent items, so it is used for item-level data.

**Problems found on M5 and fixed:** training one model across a whole state ran out of memory (7 GB), so the benchmark trains one model per store; the Nixtla mirror of M5 has no `d` column in calendar.csv (the loader rebuilds it).

Sections 1 and 4 used the earlier single boosted model; section 5 is how the current model was chosen.

## 5. Model competition: choosing one forecaster for every dataset

`python -m evals.model_competition <favorita> <m5>` (resumable; about 1 hour). 17 candidates, all given exactly the same history at each forecast origin:

- **Classical, per product:** 28-day moving average; same weekday, last 4 weeks; weekday-adjusted exponential smoothing (smoothing constant chosen per product); TSB for intermittent demand.
- **Global boosted trees (one model across all products):** the previous Stockora model; + 91-day level and days since last sale; squared-error loss; larger trees; 3 feature-subsampled models averaged.
- **Ensembles:** 7 blends of the above.

**Protocol, so the choice is not tuned to one dataset:** the winner must be a single configuration for both datasets. It is picked on validation windows (Favorita 14 and 30 Jun 2017; M5 25 Apr–22 May 2016) by mean weekly WAPE relative to the moving average, and reported on later test windows that were never used for choosing (Favorita 15 and 30 Jul 2017; M5's official final window 23 May–19 Jun 2016). M5 uses 3 stores, one per state (7,452 items), one model per store.

**Winner (picked on validation, confirmed on test, on both datasets): 75% average of 3 boosted models + 25% per-product exponential smoothing.** It is now Stockora's default forecaster.

Held-out test windows, weekly WAPE (lower is better):

| Model | Favorita (store x family) | M5 (single items) |
|---|---|---|
| **Winner: 3 boosted models + 25% smoothing** | **0.0858** | **0.3891** |
| Previous Stockora model (1 boosted model) | 0.0873 | 0.3937 |
| 3 boosted models, no smoothing | 0.0875 | 0.3920 |
| TSB (best classical method) | 0.0973 | 0.4008 |
| Exponential smoothing, weekday-adjusted | 0.1015 | 0.4020 |
| Same weekday, last 4 weeks | 0.1019 | 0.4054 |
| 28-day moving average | 0.1113 | 0.4054 |

The winner also has the lowest daily WAPE (0.134 / 0.742), the lowest RMSSE (0.694 / 0.773) and the smallest bias (+0.1% on both) on the test windows. Full numbers: `evals/model_competition.json`.

**What the competition showed:**
- Blending a global model with a local one is the most reliable gain: the global model learns shared patterns (festivals, weekdays, promotions), smoothing tracks each product's own recent level. The blend wins on both datasets; neither part does alone.
- More model capacity did not help: larger trees and a squared-error loss were worse than the default on both datasets.
- The gain over the previous model is small (1–2%) but consistent across all 4 dataset × split combinations. The big gap is between learned models and simple rules: 12% less error than the best classical method on Favorita, 3% on single items.

**Superseded (Nov 2026):** the competition was re-run with LightGBM, XGBoost and a third dataset (Rohlik); the new winner is 75% XGBoost + 25% smoothing. See `COMPETITION_REPORT.md`. This section is kept as the history of the earlier, two-dataset run. Pretrained models (Chronos, TimesFM) are still not tested. The harness takes a new candidate in a few lines. The claim is "best of 17 tested candidates on two real datasets", not "state of the art".

