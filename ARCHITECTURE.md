# DRR Automation: Architecture Plan

Status: planning. Nothing is built yet. Platform order: Blinkit first, then Swiggy, Zepto, Amazon, Flipkart, BigBasket.

## Goal

Give management one view of **DRR** (daily run rate), **DOC** (days of cover), **IT** (in transit) and **OO** (open order) for each product, in each of the 4 top cities (BLR, HYD, DEL, MUM), per platform.

DOC = (current stock + in-transit) / DRR

## How it works

```
Source sheets (yours)                 New Google Sheet (ours)                Output
+------------------+   Apps Script    +-----------------------+
| DRR sheet        | ---------------> | raw_DRR               |
| DOC sheet        |  (IMPORTRANGE    | raw_DOC               |   +--------------+
| In-transit / OO  |   or scheduled   | raw_IT_OO             |-->| Dashboard    |
| SKU master (15)  |   copy)          | SKU_Master (15 items) |   | tab + web    |
+------------------+                  |        |              |   | view (later) |
                                      |        v              |   +--------------+
                                      | calc: one row per     |
                                      | platform x SKU x city |
                                      +-----------------------+
```

1. **Raw tabs**: untouched copies of the source sheets. Nobody edits them, so they can be refreshed safely.
2. **SKU_Master**: the 15 starting items. Item ID is the join key (kept as text). Category and Simple Name come from here.
3. **Calc tab**: one long table with columns `Platform | Item ID | City | DRR | DOC | In-transit | Open order`. Blinkit fills it first; other platforms are just more rows with a different Platform value, so nothing needs rebuilding later.
4. **Dashboard**: pivot-style view over the calc tab, filtered by platform, with platforms side by side later.
5. **Refresh**: daily Apps Script trigger plus a manual "Refresh now" menu item.

DOC is read from the provided DOC sheet, not recomputed. A check flags any row where it differs from (stock + in-transit) / DRR.

## How it looks

Blinkit is the top group, with cities as rows under each SKU:

```
                          |        BLINKIT          |  (Swiggy / Zepto ... later)
SKU                       | DRR | DOC | IT  | OO    |
--------------------------+-----+-----+-----+-------+
Advance 5 Step (Orange)   |     |     |     |       |
   BLR                    | 4.2 |  11 |  40 |  120  |
   HYD                    | 2.1 |   6 |   0 |   60  |
   DEL                    | ... |
   MUM                    | ... |
Advance 4 Step ...
```

- DOC cells use muted colour coding: sage = healthy, sand = watch, dusty red = low. No bright gradients.
- Rows group by Category (Ladder, CDS, Dori, Fridge Roll, Ironing Board, Stomo) and can be collapsed.
- A top strip counts SKU x city pairs below the DOC threshold, so problem items show first.
- Optional read-only web view (phone friendly) once the sheet works.

## Starting SKUs

15 items across AL - Ladder, ST - Ladder, CDS, Dori, Fridge Roll, Ironing Board and Stomo, keyed by Item ID (for example 10160151 Advance 5 Step (Orange)).

## Open decisions

1. Sheet tab only, or also a web view? (Proposal: sheet first, web view later.)
2. DOC thresholds (for example red under 7 days, amber under 15).
3. Do the DRR and DOC sheets already have Item ID and city columns?
4. Is in-transit / open order one sheet or two?
5. Repo visibility: currently public, consider private.
6. How Blinkit outlets or warehouses roll up into BLR, HYD, DEL, MUM.
7. DRR window: 7 days, 30 days, or both.

## Rules

- Raw data files (`*.csv`, `*.xlsx`) and `.env` are gitignored so sales and stock data is not published.
