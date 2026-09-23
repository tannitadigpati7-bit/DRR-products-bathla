@echo off
cd /d "C:\Users\Bathla\Documents\DRR-products-bathla"
if not exist logs mkdir logs
"C:\Users\Bathla\AppData\Local\Python\bin\python.exe" scripts\refresh_drr_tracker.py >> logs\refresh.log 2>&1
