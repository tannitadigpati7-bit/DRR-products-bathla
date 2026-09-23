"""
Pull real Blinkit data from the Q.Com source sheets, compute DRR/DOC/IT/OO
per SKU x city, and write it into Bathla_DRR_Tracker (SKU_Master + Calc).

Run manually for now: python scripts/refresh_drr_tracker.py
Later: wire this into a scheduled trigger for daily auto-refresh.
"""
import gspread
from collections import defaultdict
from datetime import datetime, timedelta

CREDS = "credentials.json"
QCOM_MASTER = "1GxVuEY7YTM1hFjEYp2bNizKLfa48P4FE-1IwO2rLhpM"
QCOM_PENDING = "1LXrMlXDH1TB2uoBvA-G1sAh83mu4FrgYOdthyFyP_fE"
TRACKER = "1JAyP6i4UmtlgfMTHD6FFe040iQGPnN1D6Oo9l35c5tg"

CITY_MAP = {
    "Bengaluru": "BLR",
    "Delhi NCR": "DEL",
    "Hyderabad": "HYD",
    "Mumbai": "MUM",
}
CITIES = ["BLR", "HYD", "DEL", "MUM"]
WINDOW_DAYS = 7
PLATFORM = "Blinkit"
TRACK_TAB = f"{PLATFORM}_DRR_Track"

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


def parse_date(s):
    try:
        return datetime.strptime(s.strip(), "%d-%m-%Y")
    except Exception:
        return None


def main():
    gc = gspread.service_account(filename=CREDS)

    print("Reading Blinkit_Raw (sales)...")
    master = gc.open_by_key(QCOM_MASTER)
    raw = master.worksheet("Blinkit_Raw").get_all_values()
    header, rows = raw[0], raw[1:]
    idx = {h.strip(): i for i, h in enumerate(header)}

    dates = [parse_date(r[idx["date"]]) for r in rows if len(r) > idx["date"]]
    dates = [d for d in dates if d]
    max_date = max(dates)
    cutoff = max_date - timedelta(days=WINDOW_DAYS - 1)

    sales = defaultdict(int)
    for r in rows:
        if len(r) <= idx["City_Name-Mapped"]:
            continue
        item_id = r[idx["Item ID"]].strip()
        city = CITY_MAP.get(r[idx["City_Name-Mapped"]].strip())
        if not city:
            continue
        d = parse_date(r[idx["date"]])
        if not d or d < cutoff:
            continue
        try:
            qty = int(float(r[idx["qty_sold"]] or 0))
        except ValueError:
            qty = 0
        sales[(item_id, city)] += qty

    print("Reading Blinkit inventory (stock)...")
    inv_ws = next(ws for ws in master.worksheets() if ws.title.startswith("Blinkit_Inventory"))
    inv = inv_ws.get_all_values()
    ih, irows = inv[0], inv[1:]
    iidx = {h.strip(): i for i, h in enumerate(ih)}

    stock = defaultdict(int)
    for r in irows:
        if len(r) <= iidx["City Name_Mapped"]:
            continue
        item_id = r[iidx["item_id"]].strip()
        city = CITY_MAP.get(r[iidx["City Name_Mapped"]].strip())
        if not city:
            continue
        try:
            qty = int(float(r[iidx["Total Stock"]] or 0))
        except ValueError:
            qty = 0
        stock[(item_id, city)] += qty

    print("Reading Q.Com Pending & In Transit...")
    pending_book = gc.open_by_key(QCOM_PENDING)

    def items_with_qty(ws_title, status_filter=None):
        ws = pending_book.worksheet(ws_title)
        vals = ws.get_all_values()
        h, rws = vals[0], vals[1:]
        ix = {c.strip(): i for i, c in enumerate(h)}
        out = set()
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
            out.add(item_id)
        return out

    oo_items = items_with_qty("Blinkit Pending", status_filter={"Active"})
    it_items = items_with_qty("Blinkit - In Transit")

    # ---- build output rows ----
    # DOC is a live formula (Stock / DRR), not a python-computed value, so it stays
    # correct if DRR or Stock ever gets hand-edited in the sheet.
    calc_rows = [[
        "Item ID", "Category", "SKU", "City",
        f"DRR (units/day, last {WINDOW_DAYS}d)", "Stock",
        f"DOC (days, based on last {WINDOW_DAYS}d DRR)", "In Transit", "Open PO",
    ]]
    row_num = 1  # header is row 1; data starts at row 2
    for item_id, category, name in SKU_MASTER:
        for city in CITIES:
            row_num += 1
            units = sales.get((item_id, city), 0)
            drr = round(units / WINDOW_DAYS, 2)
            st = stock.get((item_id, city), 0)
            doc_formula = f'=IF(E{row_num}=0, IF(F{row_num}>0, "No sales (last {WINDOW_DAYS}d)", 0), ROUND(F{row_num}/E{row_num}, 1))'
            it_tick = "Y" if item_id in it_items else ""
            oo_tick = "Y" if item_id in oo_items else ""
            calc_rows.append([item_id, category, name, city, drr, st, doc_formula, it_tick, oo_tick])

    sku_rows = [["Item ID", "Category", "Simple Name"]] + [list(t) for t in SKU_MASTER]

    print("Writing Bathla_DRR_Tracker...")
    tracker = gc.open_by_key(TRACKER)

    sku_ws = tracker.worksheet("SKU_Master")
    sku_ws.clear()
    sku_ws.update(values=sku_rows, range_name="A1")

    calc_ws = tracker.worksheet(TRACK_TAB)
    calc_ws.clear()
    calc_ws.update(values=calc_rows, range_name="A1", value_input_option="USER_ENTERED")
    calc_ws.freeze(rows=1)
    calc_ws.update(
        values=[[f"Last refreshed: {datetime.now().strftime('%d-%b-%Y %H:%M')} | DRR window: last {WINDOW_DAYS} days | Source: Blinkit_Raw, Blinkit_Inventory, Blinkit Pending, Blinkit - In Transit"]],
        range_name=f"A{len(calc_rows) + 2}",
    )

    print(f"Done. Wrote {len(sku_rows) - 1} SKUs, {len(calc_rows) - 1} SKU x city rows.")


if __name__ == "__main__":
    main()
