"""
Build the Blinkit DRR/DOC/IT/OO table from the real source sheets.
Dry-run: prints the computed table. Writing it into Bathla_DRR_Tracker's
Calc tab is a separate step once we have edit access there.
"""
import gspread
from collections import defaultdict
from datetime import datetime

CREDS = "credentials.json"
QCOM_MASTER = "1GxVuEY7YTM1hFjEYp2bNizKLfa48P4FE-1IwO2rLhpM"
QCOM_PENDING = "1LXrMlXDH1TB2uoBvA-G1sAh83mu4FrgYOdthyFyP_fE"

CITY_MAP = {
    "Bengaluru": "BLR",
    "Delhi NCR": "DEL",
    "Hyderabad": "HYD",
    "Mumbai": "MUM",
}
WINDOW_DAYS = 7

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

gc = gspread.service_account(filename=CREDS)

print("Loading Blinkit_Raw (sales)...")
master = gc.open_by_key(QCOM_MASTER)
raw = master.worksheet("Blinkit_Raw").get_all_values()
header, rows = raw[0], raw[1:]
idx = {h.strip(): i for i, h in enumerate(header)}

def parse_date(s):
    try:
        return datetime.strptime(s.strip(), "%d-%m-%Y")
    except Exception:
        return None

dates = [parse_date(r[idx["date"]]) for r in rows if len(r) > idx["date"]]
dates = [d for d in dates if d]
max_date = max(dates)
cutoff = max_date.replace(hour=0, minute=0, second=0)
from datetime import timedelta
cutoff = max_date - timedelta(days=WINDOW_DAYS - 1)
print(f"Latest date in Blinkit_Raw: {max_date.date()}  -> using window {cutoff.date()} to {max_date.date()}")

sales = defaultdict(int)  # (item_id, city) -> units in window
for r in rows:
    if len(r) <= idx["City_Name-Mapped"]:
        continue
    item_id = r[idx["Item ID"]].strip()
    city_raw = r[idx["City_Name-Mapped"]].strip()
    city = CITY_MAP.get(city_raw)
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

print("Loading Blinkit_Inventory (stock)...")
inv_ws = None
for ws in master.worksheets():
    if ws.title.startswith("Blinkit_Inventory"):
        inv_ws = ws
        break
inv = inv_ws.get_all_values()
ihead, irows = inv[0], inv[1:]
iidx = {h.strip(): i for i, h in enumerate(ihead)}

stock = defaultdict(int)
unmatched_stock_qty = 0
for r in irows:
    if len(r) <= iidx["City Name_Mapped"]:
        continue
    item_id = r[iidx["item_id"]].strip()
    city_raw = r[iidx["City Name_Mapped"]].strip()
    city = CITY_MAP.get(city_raw)
    try:
        qty = int(float(r[iidx["Total Stock"]] or 0))
    except ValueError:
        qty = 0
    if not city:
        unmatched_stock_qty += qty
        continue
    stock[(item_id, city)] += qty

print(f"  (stock in warehouses outside BLR/HYD/DEL/MUM, not counted: {unmatched_stock_qty} units)")

print("Loading Q.Com Pending & In Transit workbook...")
pending_book = gc.open_by_key(QCOM_PENDING)

def item_set_with_qty(ws_title, status_filter=None):
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

oo_items = item_set_with_qty("Blinkit Pending", status_filter={"Active"})
it_items = item_set_with_qty("Blinkit - In Transit")  # status column is #N/A, so no filter

print("\n" + "=" * 95)
print(f"{'SKU':<32}{'City':<6}{'DRR/day':>9}{'Stock':>8}{'DOC(d)':>9}{'IT':>4}{'OO':>4}")
print("=" * 95)
for item_id, category, name in SKU_MASTER:
    for city in ["BLR", "HYD", "DEL", "MUM"]:
        units = sales.get((item_id, city), 0)
        drr = round(units / WINDOW_DAYS, 2)
        st = stock.get((item_id, city), 0)
        doc = round(st / drr, 1) if drr > 0 else ("inf" if st > 0 else 0)
        it_tick = "Y" if item_id in it_items else ""
        oo_tick = "Y" if item_id in oo_items else ""
        print(f"{name:<32}{city:<6}{drr:>9}{st:>8}{str(doc):>9}{it_tick:>4}{oo_tick:>4}")
