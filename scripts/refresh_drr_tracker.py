"""
Pull real Blinkit data from the Q.Com source sheets, compute DRR/DOC/IT/OO
per SKU x city, and write it into Bathla_DRR_Tracker (Blinkit_DRR_Track).

Cities are derived directly from the source data (Blinkit_Raw's own
City_Name-Mapped column), not hand-listed -- Blinkit sells in ~250 towns,
and hardcoding that list would go stale immediately.

Run manually for now: python scripts/refresh_drr_tracker.py
Later: wire this into a scheduled trigger for daily auto-refresh.
"""
import re
import gspread
from collections import defaultdict
from datetime import datetime, timedelta

CREDS = "credentials.json"
QCOM_MASTER = "1GxVuEY7YTM1hFjEYp2bNizKLfa48P4FE-1IwO2rLhpM"
QCOM_PENDING = "1LXrMlXDH1TB2uoBvA-G1sAh83mu4FrgYOdthyFyP_fE"
TRACKER = "1JAyP6i4UmtlgfMTHD6FFe040iQGPnN1D6Oo9l35c5tg"

WINDOW_DAYS = 7
SIX_MONTHS_DAYS = 180  # a city with no sales AND no stock in this window is dropped entirely
PLATFORM = "Blinkit"
TRACK_TAB = f"{PLATFORM}_DRR_Track"

# Blinkit's own City_Name-Mapped column already spells most cities consistently,
# but a handful of the ~250 towns are the same place spelled two ways
# (casing, underscore vs space, or a "(State)" qualifier). Fold those into one
# canonical name; everything else is trusted as-is rather than hand-listing
# all ~250 cities here.
# ponytail: a short manual alias list, not a full geo-normalization library --
# extend this dict if a new duplicate spelling shows up later.
CITY_ALIASES = {
    "bikaner": "Bikaner",
    "newchandigarh": "New Chandigarh",
    "muzzaffarnagar": "Muzaffarnagar",
    "sri ganganagar": "Sri Ganganagar",
    "sundar nagar": "Sundar Nagar",
    "udaipur (rajasthan)": "Udaipur",
    "kharagpur (west bengal)": "Kharagpur",
    "bijapur (karnataka)": "Bijapur",
    "aurangabad (maharashtra)": "Aurangabad",
    "hamirpur (himachal pradesh)": "Hamirpur",
    "nizamabad (telangana)": "Nizamabad",
    "pali (rajasthan)": "Pali",
    "bilaspur (chhattisgarh)": "Bilaspur",
}

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
    """Clean up one city string: collapse separators/spacing, fold known
    duplicate spellings into one canonical name via CITY_ALIASES."""
    if not raw:
        return None
    s = " ".join(raw.replace("_", " ").split()).strip()
    if not s:
        return None
    alias = CITY_ALIASES.get(s.lower())
    return alias if alias else s


def location_to_city(loc):
    """Blinkit Pending/In-Transit 'Location' is a warehouse name like
    'Hyderabad H3' or 'Rajpura R2' -- strip the trailing warehouse code,
    roll known feeder hubs into their metro, otherwise use the place name
    itself as the city."""
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

    print("Reading Blinkit_Raw (sales)...")
    master = gc.open_by_key(QCOM_MASTER)
    raw = master.worksheet("Blinkit_Raw").get_all_values()
    header, rows = raw[0], raw[1:]
    idx = {h.strip(): i for i, h in enumerate(header)}

    dates = [parse_date(r[idx["date"]]) for r in rows if len(r) > idx["date"]]
    dates = [d for d in dates if d]
    max_date = max(dates)
    cutoff = max_date - timedelta(days=WINDOW_DAYS - 1)
    six_months_cutoff = max_date - timedelta(days=SIX_MONTHS_DAYS)

    sales = defaultdict(int)
    all_cities = set()
    city_volume = defaultdict(int)  # for sorting cities by size in the output
    cities_with_recent_sales = set()  # any sale in the last 6 months
    last_sale_date = {}  # (item_id, city) -> most recent date with qty_sold > 0, ever
    for r in rows:
        if len(r) <= idx["City_Name-Mapped"]:
            continue
        item_id = r[idx["Item ID"]].strip()
        city = normalize_city(r[idx["City_Name-Mapped"]].strip())
        if not city:
            continue
        all_cities.add(city)
        try:
            qty_all_time = int(float(r[idx["qty_sold"]] or 0))
        except ValueError:
            qty_all_time = 0
        city_volume[city] += qty_all_time
        d = parse_date(r[idx["date"]])
        if not d:
            continue
        if qty_all_time > 0:
            key = (item_id, city)
            if key not in last_sale_date or d > last_sale_date[key]:
                last_sale_date[key] = d
        if qty_all_time > 0 and d >= six_months_cutoff:
            cities_with_recent_sales.add(city)
        if d < cutoff:
            continue
        sales[(item_id, city)] += qty_all_time

    print("Reading Blinkit inventory (stock)...")
    inv_ws = next(ws for ws in master.worksheets() if ws.title.startswith("Blinkit_Inventory"))
    inv = inv_ws.get_all_values()
    ih, irows = inv[0], inv[1:]
    iidx = {h.strip(): i for i, h in enumerate(ih)}

    stock = defaultdict(int)
    cities_with_stock = set()  # any current stock right now
    for r in irows:
        if len(r) <= iidx["City Name_Mapped"]:
            continue
        item_id = r[iidx["item_id"]].strip()
        city = normalize_city(r[iidx["City Name_Mapped"]].strip())
        if not city:
            continue
        all_cities.add(city)
        try:
            qty = int(float(r[iidx["Total Stock"]] or 0))
        except ValueError:
            qty = 0
        if qty > 0:
            cities_with_stock.add(city)
        stock[(item_id, city)] += qty

    # Drop any city with zero sales in the last 6 months AND zero stock right
    # now -- a dead city, not worth a row for every SKU.
    active_cities = cities_with_recent_sales | cities_with_stock
    dropped = all_cities - active_cities
    all_cities = active_cities
    print(f"  Dropping {len(dropped)} cities with no sales in {SIX_MONTHS_DAYS} days and no current stock.")

    print("Reading Q.Com Pending & In Transit...")
    pending_book = gc.open_by_key(QCOM_PENDING)

    def qty_by_item_city(ws_title, status_filter=None):
        """Sum Quantity Outstanding per (Item Code, City), matched from the
        Location column. Only genuinely blank/#N/A rows are skipped now --
        every resolvable location becomes its own tracked city."""
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
            if not city or city not in all_cities:
                # no usable location, or a city already dropped for having no
                # recent sales/stock -- don't resurrect it just for a PO/transit row
                skipped_qty += qty
                continue
            out[(item_id, city)] += qty
        print(f"  {ws_title}: {skipped_qty} units with no usable/active-city location, not counted")
        return out

    oo_qty = qty_by_item_city("Blinkit Pending", status_filter={"Active"})
    it_qty = qty_by_item_city("Blinkit - In Transit")

    # Biggest cities first, alphabetical within same volume, so the sheet is
    # useful to scan top-down instead of a random ~250-city jumble.
    cities_sorted = sorted(all_cities, key=lambda c: (-city_volume.get(c, 0), c))
    print(f"Tracking {len(cities_sorted)} cities (was hardcoded to 4 before).")

    # ---- build output rows ----
    # DOC is a live formula (Stock / DRR), not a python-computed value, so it stays
    # correct if DRR or Stock ever gets hand-edited in the sheet.
    refreshed_note = (
        f"Last refreshed: {datetime.now().strftime('%d-%b-%Y %H:%M')} | "
        f"DRR window: last {WINDOW_DAYS} days | Cities: {len(cities_sorted)} | "
        f"Source: Blinkit_Raw, Blinkit_Inventory, Blinkit Pending, Blinkit - In Transit"
    )
    calc_rows = [
        [refreshed_note],
        [
            "Item ID", "Category", "SKU", "City",
            f"DRR (units/day, last {WINDOW_DAYS}d)", "Stock",
            "DOC",
            "In Transit", "Qty", "Open PO", "Qty",
        ],
    ]
    row_num = 2  # row 1 = refresh banner, row 2 = header, data starts at row 3
    skipped_dead_combos = 0
    for item_id, category, name in SKU_MASTER:
        for city in cities_sorted:
            st = stock.get((item_id, city), 0)
            last_sale = last_sale_date.get((item_id, city))
            sold_recently = last_sale is not None and (max_date - last_sale).days < SIX_MONTHS_DAYS

            # Skip this SKU in this city entirely if IT specifically has no
            # recent sales and no current stock -- being sold in some OTHER
            # city doesn't earn it a row everywhere.
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
                f'IF(F{row_num}=0, "0 - OUT OF STOCK", ROUND(F{row_num}/E{row_num}, 1)))'
            )
            it_q = it_qty.get((item_id, city), 0)
            oo_q = oo_qty.get((item_id, city), 0)
            it_tick = "Y" if it_q > 0 else "N"
            oo_tick = "Y" if oo_q > 0 else "N"
            calc_rows.append([item_id, category, name, city, drr, st, doc_formula, it_tick, it_q, oo_tick, oo_q])
    print(f"  Skipped {skipped_dead_combos} SKU x city rows with no sales in {SIX_MONTHS_DAYS}d and no stock for that SKU specifically.")

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
    calc_ws.set_basic_filter(name=f"A2:K{len(calc_rows)}")  # header is row 2, not row 1 (banner)

    print(f"Done. Wrote {len(calc_rows) - 2} active SKU x city rows ({len(SKU_MASTER)} SKUs, {len(cities_sorted)} cities considered).")


if __name__ == "__main__":
    main()
