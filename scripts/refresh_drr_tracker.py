"""
Pull real Blinkit data from the Q.Com source sheets, compute DRR/DOC/IT/OO
per SKU x city, and write it into Bathla_DRR_Tracker (Blinkit_DRR_Track).

Tracks exactly the 10 cities in TARGET_CITIES -- not every city Blinkit
ever sold in. Change that list to change scope.

Run manually for now: python scripts/refresh_drr_tracker.py
Later: wire this into a scheduled trigger for daily auto-refresh.
"""
import json
import re
import gspread
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

CREDS = "credentials.json"
DASHBOARD_TEMPLATE = Path(__file__).parent.parent / "dashboard" / "template.html"
DASHBOARD_OUTPUT = Path(__file__).parent.parent / "dashboard" / "index.html"
QCOM_MASTER = "1GxVuEY7YTM1hFjEYp2bNizKLfa48P4FE-1IwO2rLhpM"
QCOM_PENDING = "1LXrMlXDH1TB2uoBvA-G1sAh83mu4FrgYOdthyFyP_fE"
TRACKER = "1JAyP6i4UmtlgfMTHD6FFe040iQGPnN1D6Oo9l35c5tg"

WINDOW_DAYS = 7
SIX_MONTHS_DAYS = 180  # a SKU with no sales AND no stock in this window is dropped for that city
LOW_SALES_DRR = 0.2  # below this (under ~1-2 units/week), DOC shows "Low sales" instead of a misleading huge number
PLATFORM = "Blinkit"
TRACK_TAB = f"{PLATFORM}_DRR_Track"

# Exactly these 10 -- in this order (biggest metros, as given).
TARGET_CITIES = [
    "Delhi NCR", "Bengaluru", "Mumbai", "Hyderabad", "Pune",
    "Kolkata", "Lucknow", "Ahmedabad", "Jaipur", "Chennai",
]

# A few Q.Com Pending/In-Transit "Location" values are warehouse feeder hubs
# for a metro, not demand cities in their own right -- roll those into the
# metro so pipeline (IT/OO) lines up with where the DRR/stock is actually counted.
FEEDER_TO_METRO = {
    "faridabad": "Delhi NCR", "noida": "Delhi NCR", "kundli": "Delhi NCR",
    "gurgaon": "Delhi NCR", "gurugram": "Delhi NCR", "ghaziabad": "Delhi NCR",
    "bhiwandi": "Mumbai",
}

SKU_MASTER = [
    ("10160151", "AL - Ladder", "Advance 5 Step (Orange)"),
    ("10160152", "AL - Ladder", "Advance 4 Step"),
    ("10193800", "AL - Ladder", "Advance 6 Step"),
    ("10280885", "ST - Ladder", "Ascend 5 Step (Orange & Black)"),
    ("10193793", "ST - Ladder", "Ascend 4 Step (Orange & Black)"),
    ("10193815", "CDS", "Neo (Orange)"),
    ("10335218", "CDS", "Terra Medium (Orange)"),
    ("10335216", "CDS", "Neo + (Green & Black)"),
    ("10334669", "CDS", "Exa 6ft 6 Pipes"),
    ("10335217", "CDS", "Terra Extra Large (Orange)"),
    ("10193818", "CDS", "Terra Large (Orange)"),
    ("10193820", "CDS", "Aero CDS"),
    ("10193830", "Dori", "Dori Ivory (4)"),
    ("10194482", "Fridge Roll", "Fridge Roll 45 X 150 (White)"),
    ("10163106", "Ironing Board", "X Press Ace"),
    ("10193824", "Stomo", "Taro Pearl White"),
]


def normalize_city(raw):
    """Collapse separators/spacing so 'Delhi_NCR' / 'Delhi  NCR' etc. all
    match the exact TARGET_CITIES spelling."""
    if not raw:
        return None
    s = " ".join(raw.replace("_", " ").split()).strip()
    return s if s else None


def location_to_city(loc):
    """Blinkit Pending/In-Transit 'Location' is a warehouse name like
    'Hyderabad H3' or 'Faridabad' -- strip the trailing warehouse code,
    roll known feeder hubs into their metro, otherwise use the place name
    itself (which then has to match one of TARGET_CITIES to count)."""
    s = (loc or "").strip()
    if not s or s == "#N/A":
        return None
    s = re.sub(r"\s+[A-Za-z]{1,2}\d{1,3}$", "", s).strip()  # drop " H3", " M12", etc.
    if not s:
        return None
    metro = FEEDER_TO_METRO.get(s.lower())
    return metro if metro else normalize_city(s)


def parse_date(s):
    try:
        return datetime.strptime(s.strip(), "%d-%m-%Y")
    except Exception:
        return None


def main():
    gc = gspread.service_account(filename=CREDS)
    target_set = set(TARGET_CITIES)

    print("Reading Blinkit_Raw (sales)...")
    master = gc.open_by_key(QCOM_MASTER)
    raw = master.worksheet("Blinkit_Raw").get_all_values()
    header, rows = raw[0], raw[1:]
    idx = {h.strip(): i for i, h in enumerate(header)}

    dates = [parse_date(r[idx["date"]]) for r in rows if len(r) > idx["date"]]
    dates = [d for d in dates if d]
    max_date = max(dates)
    cutoff = max_date - timedelta(days=WINDOW_DAYS - 1)

    # Identity text (Simple Name / Catagory / Sub- Cat) for each Item ID, read
    # live from Blinkit_Raw's own columns R / S / T -- not hardcoded -- so it
    # tracks whatever Blinkit_Raw currently says, not a string that can go
    # stale. Falls back to SKU_MASTER below for an item ID with no rows yet.
    # Sub Category isn't written to the tracker (no column for it there) --
    # captured here in case that's wanted later.
    sku_identity = {}

    sales = defaultdict(int)
    last_sale_date = {}  # (item_id, city) -> most recent date with qty_sold > 0, ever
    for r in rows:
        if len(r) <= idx["City_Name-Mapped"]:
            continue
        city = normalize_city(r[idx["City_Name-Mapped"]].strip())
        if city not in target_set:
            continue
        item_id = r[idx["Item ID"]].strip()
        if item_id not in sku_identity:
            simple_name = r[idx["Simple Name"]].strip() if len(r) > idx["Simple Name"] else ""
            category = r[idx["Catagory"]].strip() if len(r) > idx["Catagory"] else ""
            sub_category = r[idx["Sub- Cat"]].strip() if len(r) > idx["Sub- Cat"] else ""
            if simple_name and category:
                sku_identity[item_id] = (category, simple_name, sub_category)
        try:
            qty_all_time = int(float(r[idx["qty_sold"]] or 0))
        except ValueError:
            qty_all_time = 0
        d = parse_date(r[idx["date"]])
        if not d:
            continue
        if qty_all_time > 0:
            key = (item_id, city)
            if key not in last_sale_date or d > last_sale_date[key]:
                last_sale_date[key] = d
        if d < cutoff:
            continue
        sales[(item_id, city)] += qty_all_time

    print("Reading Blinkit inventory (stock)...")
    inv_ws = next(ws for ws in master.worksheets() if ws.title.startswith("Blinkit_Inventory"))
    inv = inv_ws.get_all_values()
    ih, irows = inv[0], inv[1:]
    iidx = {h.strip(): i for i, h in enumerate(ih)}

    def inv_qty(r, col):
        try:
            return int(float(r[iidx[col]] or 0))
        except ValueError:
            return 0

    stock = defaultdict(int)
    backend_inv = defaultdict(int)   # warehouse stock
    frontend_inv = defaultdict(int)  # dark-store (shelf) stock
    for r in irows:
        if len(r) <= iidx["City Name_Mapped"]:
            continue
        city = normalize_city(r[iidx["City Name_Mapped"]].strip())
        if city not in target_set:
            continue
        item_id = r[iidx["item_id"]].strip()
        stock[(item_id, city)] += inv_qty(r, "Total Stock")
        backend_inv[(item_id, city)] += inv_qty(r, "backend_inv_qty")
        frontend_inv[(item_id, city)] += inv_qty(r, "frontend_inv_qty")

    print("Reading Q.Com Pending & In Transit...")
    pending_book = gc.open_by_key(QCOM_PENDING)

    def qty_by_item_city(ws_title, status_filter=None):
        """Sum Quantity Outstanding per (Item Code, City), matched from the
        Location column. Only counts locations that resolve to one of the
        10 target cities."""
        ws = pending_book.worksheet(ws_title)
        vals = ws.get_all_values()
        h, rws = vals[0], vals[1:]
        ix = {c.strip(): i for i, c in enumerate(h)}
        out = defaultdict(int)
        skipped_qty = 0
        for r in rws:
            if len(r) <= ix["Quantity Outstanding"]:
                continue
            item_id = r[ix["Item Code"]].strip()
            try:
                qty = int(float(r[ix["Quantity Outstanding"]] or 0))
            except ValueError:
                qty = 0
            if qty <= 0:
                continue
            if status_filter and r[ix.get("PO Status", -1)].strip() not in status_filter:
                continue
            city = location_to_city(r[ix.get("Location", -1)] if ix.get("Location", -1) >= 0 else "")
            if city not in target_set:
                skipped_qty += qty
                continue
            out[(item_id, city)] += qty
        print(f"  {ws_title}: {skipped_qty} units outside the 10 target cities, not counted")
        return out

    oo_qty = qty_by_item_city("Blinkit Pending", status_filter={"Active"})
    it_qty = qty_by_item_city("Blinkit - In Transit")

    # ---- build output rows ----
    # DOC is a live formula (Stock / DRR), not a python-computed value, so it stays
    # correct if DRR or Stock ever gets hand-edited in the sheet.
    refreshed_note = (
        f"Last refreshed: {datetime.now().strftime('%d-%b-%Y %H:%M')} | "
        f"DRR window: last {WINDOW_DAYS} days | Cities: {len(TARGET_CITIES)} | "
        f"Source: Blinkit_Raw, Blinkit_Inventory, Blinkit Pending, Blinkit - In Transit"
    )
    calc_rows = [
        [refreshed_note],
        [
            "Item ID", "Category", "SKU", "City",
            f"DRR (units/day, last {WINDOW_DAYS}d)", "Stock",
            "DOC",
            "In Transit", "Qty", "Open PO", "Qty",
            # appended at the end so the existing H:K columns (and the
            # conditional formatting on them) stay exactly where they were
            "Backend Inv", "Frontend Inv",
        ],
    ]
    row_num = 2  # row 1 = refresh banner, row 2 = header, data starts at row 3
    skipped_dead_combos = 0
    dashboard_rows = []  # same rows, JSON-friendly, for the dashboard
    for item_id, fallback_category, fallback_name in SKU_MASTER:
        category, name, sub_category = sku_identity.get(item_id, (fallback_category, fallback_name, ""))
        for city in TARGET_CITIES:
            st = stock.get((item_id, city), 0)
            last_sale = last_sale_date.get((item_id, city))
            sold_recently = last_sale is not None and (max_date - last_sale).days < SIX_MONTHS_DAYS

            # Skip this SKU in this city if it has no recent sales and no
            # current stock -- being sold elsewhere doesn't earn it a row here.
            if not sold_recently and st == 0:
                skipped_dead_combos += 1
                continue

            row_num += 1
            units = sales.get((item_id, city), 0)
            drr = round(units / WINDOW_DAYS, 2)

            if last_sale is None:
                dormant_text = "Never sold here"
            else:
                days_ago = (max_date - last_sale).days
                dormant_text = f"No sales in {days_ago}d" if days_ago < 60 else f"No sales in ~{days_ago // 30}mo"

            doc_formula = (
                f'=IF(E{row_num}=0, '
                f'IF(F{row_num}>0, "No sales (last {WINDOW_DAYS}d)", "{dormant_text}"), '
                f'IF(E{row_num}<{LOW_SALES_DRR}, "Low sales ("&E{row_num}&"/d)", '
                f'ROUND(F{row_num}/E{row_num}, 1)))'
            )
            it_q = it_qty.get((item_id, city), 0)
            oo_q = oo_qty.get((item_id, city), 0)
            it_tick = "Y" if it_q > 0 else "N"
            oo_tick = "Y" if oo_q > 0 else "N"
            calc_rows.append([
                item_id, category, name, city, drr, st, doc_formula, it_tick, it_q, oo_tick, oo_q,
                backend_inv.get((item_id, city), 0), frontend_inv.get((item_id, city), 0),
            ])

            # DOC as an actual value for the dashboard (the sheet gets a live
            # formula instead, computed the same way).
            if drr == 0:
                doc_numeric, doc_label = None, ("No sales (last 7d)" if st > 0 else dormant_text)
            elif drr < LOW_SALES_DRR:
                doc_numeric, doc_label = None, f"Low sales ({drr}/d)"
            else:
                doc_numeric, doc_label = round(st / drr, 1), None
            dashboard_rows.append({
                "sku": name, "category": category, "city": city,
                "drr": drr, "stock": st,
                "doc_numeric": doc_numeric, "doc_label": doc_label,
                "in_transit": it_tick, "it_qty": it_q,
                "open_po": oo_tick, "oo_qty": oo_q,
            })
    print(f"  Skipped {skipped_dead_combos} SKU x city rows with no sales in {SIX_MONTHS_DAYS}d and no stock.")

    print("Writing Bathla_DRR_Tracker...")
    tracker = gc.open_by_key(TRACKER)

    calc_ws = tracker.worksheet(TRACK_TAB)
    calc_ws.clear()
    calc_ws.update(values=calc_rows, range_name="A1", value_input_option="USER_ENTERED")
    calc_ws.freeze(rows=2)
    # Re-apply the filter to the full current range each run -- otherwise it
    # stays pinned to whatever row count existed when it was first created,
    # and silently hides newer rows from the filter dropdowns.
    calc_ws.clear_basic_filter()
    calc_ws.set_basic_filter(name=f"A2:M{len(calc_rows)}")  # header is row 2, not row 1 (banner)

    print(f"Done. Wrote {len(calc_rows) - 2} active SKU x city rows across the 10 target cities.")

    print("Writing dashboard/index.html...")
    dashboard_data = {
        "generated_at": datetime.now().strftime("%d-%b-%Y %H:%M"),
        "window_days": WINDOW_DAYS,
        "cities": TARGET_CITIES,
        "rows": dashboard_rows,
    }
    template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    page = template.replace("__DRR_DATA_JSON__", json.dumps(dashboard_data))
    DASHBOARD_OUTPUT.write_text(page, encoding="utf-8")
    print(f"  Wrote {DASHBOARD_OUTPUT}")


if __name__ == "__main__":
    main()
