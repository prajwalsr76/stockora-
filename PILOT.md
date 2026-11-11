# Running a real-shop pilot

The goal: one real shop uses Stockora for 4-5 weeks, and you can report what happened, measured from the shop's own records. One honest pilot beats any simulated number on a resume.

> **Status (11 Nov 2026): no pilot has been run.** Real shop data can't be collected for now, so this document is a ready-to-use kit, not a record of a pilot. Do not report any pilot or Indian results until a real shop has completed the schedule below.

## 1. Find a shop (week 0)

**Who to approach first:** shops where you already have trust. A relative's or neighbour's kirana, the shop your family buys from, a medical or general store near your college. A shop with 150-600 items and one or two regular distributors is ideal; a supermarket with billing staff is harder.

**Best shops for a pilot:**
- already uses a billing app (Vyapar, myBillBook, Marg, Tally): you can import months of past sales on day one
- sells dairy or bread (expiry matters, so results show quickly)
- owner is curious and has a smartphone

**When to visit:** 2-4 pm, when shops are quiet. Buy something first. Keep it under 10 minutes.

**What to say (Hinglish):**

> Namaste ji. Main engineering student hoon aur dukaan ke liye ek free app bana raha hoon. Ye batata hai kaunsa maal khatam hone wala hai, kya mangana chahiye, aur kaunsa maal expire hone wala hai. Aapka koi paisa nahi lagega, aur order hamesha aap khud approve karenge. Kya main ek mahina aapki dukaan par try kar sakta hoon? Roz sirf 2-3 minute lagenge.

If they hesitate, offer to do the data entry yourself for the first week.

## 2. Consent (before touching any data)

India's Digital Personal Data Protection Act, 2023 applies to customer names and phone numbers (udhaar) and to the owner's data. Get written consent, collect only what the pilot needs, and delete it when asked. Print this, fill it in, and get it signed:

> **Stockora pilot: consent**
>
> I, ______________________ (owner of ______________________), agree to try Stockora from ___/___/____ to ___/___/____.
>
> - Stockora will store my product list, stock, purchase bills and sales, and (only if I choose) customer credit entries.
> - The data is used only to run the app for my shop and to measure the pilot. Results may be shared without my shop's name ☐ / with my shop's name ☐.
> - Customer names and phone numbers will never be shared.
> - I can stop at any time and ask for all my data to be deleted.
> - No order is placed and no money is paid by the app; I approve everything.
>
> Owner signature: ______________ Date: ________ Student: ______________

Keep the signed copy (a phone photo is fine). If the owner does not want customer names stored, skip udhaar or use initials.

## 3. Onboard the shop (week 0, about 2 hours)

1. Ask for exports from their billing app: **item list** (with stock, MRP, purchase price, GST) and **sales report** (date-wise item sales, as many months as possible). In most apps these are under Reports → Item/Stock report and Item-wise sales → Export to Excel.
2. Without a billing app: photograph their top distributor bills, then list the ~100-150 items they sell most with MRP, purchase price and current stock (count the shelf together).
3. Put the files in a folder named `products`, `distributors`, `prices`, `sales` (.xlsx or .csv). Get blank templates with:
   ```powershell
   cd backend
   .\.venv\Scripts\python -m stockora.onboard templates --folder ..\shop_data
   ```
4. Create the shop:
   ```powershell
   .\.venv\Scripts\python -m stockora.onboard create --db shop.db --name "Patil Kirana" --city Nashik --state 27 --cash 25000 --folder ..\shop_data
   ```
   Read the report: fix skipped rows (unknown items, missing MRP) and run `onboard import --db shop.db --folder ..\shop_data` again. Check every "auto-filled" GST rate and MRP with the owner.
5. In `backend\.env` set `STOCKORA_DB=shop.db`, then start the app. The demo store is no longer used.
6. Install it on the owner's phone: open the site, then "Add to home screen". For the owner's phone to reach it, host it (Railway, Render or a small cloud VM) or run it on a laptop in the shop.

## 4. The 4-week schedule

| Week | Mode | What happens |
|---|---|---|
| 0 | Onboarding | Import data, count stock together, add distributors and delivery days |
| 1 | **Shadow** | Owner orders as usual. Sales and purchases are recorded in Stockora, but suggestions are not used. This is the baseline. |
| 2-4 | **Assisted** | Owner looks at "Aaj ka kaam" each morning and approves or edits the suggested orders |

**Owner's daily routine (2-3 minutes):** record sales (or import the day's billing export), add bills when goods arrive, check Today, approve or edit the order.

**Your weekly routine:** a 15-minute visit or call. Ask what was wrong or annoying, fix the top two issues, count 10-15 fast-moving items together and correct stock in the app.

## 5. Measure

Write down the dates you switch modes, then at the end run:

```powershell
.\.venv\Scripts\python -m stockora.pilot --db shop.db --shadow-start 2026-11-16 --assisted-start 2026-11-23 --out pilot_report.md
```

It compares stockout days, expired units, units sold per day, units held on the shelf (and days of stock), orders approved, and how often the owner edited suggestions.

Also record what software cannot measure:
- the owner's estimate of time spent ordering per day, before and after
- bill photos processed and how many fields needed correcting (bill-reading accuracy on real bills)
- anything the owner says, in their words (a quote is powerful in interviews)

## 6. Report it honestly

- It is a **case study of one shop over a few weeks**, not proof. Say so.
- Periods differ (a festival in one week, rain in another). Mention it.
- Report what went badly too. "Bill reading failed on handwritten bills, so I added a review screen" is a stronger interview answer than a perfect number.

Resume line template (fill in only with numbers you measured):

> Piloted Stockora in a Nashik kirana for 4 weeks: stockout days ___% → ___%, expired units ___ → ___, owner accepted ___% of suggested orders unchanged; bill extraction ___% field accuracy on ___ real bills.

## 7. After the pilot

Ask the owner whether they want to keep using it. Export or delete their data as they choose, and thank them. If it went well, ask for a short recommendation you can quote, and approach a second shop using the first as a reference.
