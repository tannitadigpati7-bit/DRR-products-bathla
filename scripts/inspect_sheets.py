"""One-off: dump tab names + header rows of the source sheets so we can design the mapping.
ponytail: throwaway inspection script, not part of the pipeline.
"""
import sys
import gspread

CREDS = "credentials.json"
SHEETS = {
    "Q.Com Master": "1GxVuEY7YTM1hFjEYp2bNizKLfa48P4FE-1IwO2rLhpM",
    "Q.Com Pending & In Transit": "1LXrMlXDH1TB2uoBvA-G1sAh83mu4FrgYOdthyFyP_fE",
}

gc = gspread.service_account(filename=CREDS)

for label, sheet_id in SHEETS.items():
    print(f"\n{'='*60}\n{label}  ({sheet_id})\n{'='*60}")
    try:
        sh = gc.open_by_key(sheet_id)
    except Exception as e:
        print(f"  ERROR opening: {e}")
        continue
    for ws in sh.worksheets():
        print(f"\n-- tab: '{ws.title}'  ({ws.row_count}x{ws.col_count}) --")
        try:
            rows = ws.get_values("A1:P5")
            for r in rows:
                print(r)
        except Exception as e:
            print(f"  ERROR reading: {e}")
