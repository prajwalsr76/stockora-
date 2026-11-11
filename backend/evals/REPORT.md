# Stockora evaluation report

Generated 2026-11-07 in 244.6 s. Synthetic data is labelled as such; see `evals/run_evals.py`.

## Forecasting (rolling-origin backtest, synthetic demand, 90 real SKUs, Jan 2025 - Nov 2026; scored on true demand)
| Method | WAPE weekly | WAPE daily | Bias |
|---|---|---|---|
| Stockora model | 0.1831 | 0.4919 | -0.0419 |
| 28-day moving average | 0.2138 | 0.4964 | -0.107 |
| Same weekday last week | 0.2668 | 0.6774 | -0.1367 |
| Noise floor (true expected demand) | 0.1751 | 0.4893 | -0.0014 |

The model closes **79%** of the gap between the moving average and the noise floor.

## Ordering policy, closed-loop simulation (2026-08-30 to 2026-11-07, synthetic demand)
| Metric | Gut feel | Stockora | Change | Stockora lean | Change |
|---|---|---|---|---|---|
| Stockout days | 9.0% | 3.0% | -66.8% | 7.9% | -11.5% |
| Fill rate | 92.8% | 97.6% | +5.3% | 92.8% | +0.0% |
| Expired stock | – | – | -12.3% | – | -49.5% |
| Stock held | – | – | +24.7% | – | -11.3% |

## Product matching (450 messy names: caps/units, typos, Hinglish, abbreviations, missing brand)
Top-1 accuracy **100.0%**; auto-accepted 98.9% with precision **100.0%**; 1.1% sent to human review.

## Bill validation (240 bills)
Error detection recall **100.0%**; false alarms on clean bills **0.0%**.
Per type: gstin_typo 100%, line_amount 100%, grand_total 100%, cgst_sgst_mismatch 100%, igst_same_state 100%, invalid_slab 100%, rate_above_mrp 100%, already_expired 100%

## Owner assistant (offline mode)
Intent routing: dev **100.0%** (n=17), held-out **89.3%** (n=28).
Prompt-injection block rate **100%**, benign false refusals **0%**, p50 latency 4.5 ms.
Hallucinated-number guard: catches **86%** of inflated numbers, false alarms 0%.
RAG over distributor documents: hit@1 **100%**, hit@3 **100%**.
