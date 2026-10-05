/**
 * Blinkit DRR auto-refresh -- runs on Google's servers via a time-driven
 * trigger, so it works even if no laptop is on. This is the Apps Script
 * port of scripts/refresh_drr_tracker.py (same logic, same output shape).
 *
 * One-time setup (paste this whole file into the Apps Script editor bound
 * to Bathla_DRR_Tracker, then run once):
 *   1. Open Bathla_DRR_Tracker -> Extensions -> Apps Script.
 *   2. Delete the placeholder Code.gs content, paste this file in, Save.
 *   3. In the function dropdown (top toolbar), select `setupDailyTrigger`,
 *      click Run. Google will ask you to authorize -- approve it (it only
 *      needs access to your own Sheets, since it reads Q.Com Master/Pending
 *      under your own Google account, the same one that owns this sheet).
 *   4. That's it -- it now refreshes itself daily at 7am, no laptop needed.
 *      A "DRR Tracker" menu also appears on the sheet with "Refresh now".
 */

var QCOM_MASTER_ID = '1GxVuEY7YTM1hFjEYp2bNizKLfa48P4FE-1IwO2rLhpM';
var QCOM_PENDING_ID = '1LXrMlXDH1TB2uoBvA-G1sAh83mu4FrgYOdthyFyP_fE';
var TRACK_TAB = 'Blinkit_DRR_Track';

var WINDOW_DAYS = 7;
var SIX_MONTHS_DAYS = 180; // a SKU with no sales AND no stock in this window is dropped for that city

// Exactly these 10 -- in this order (biggest metros, as given).
var TARGET_CITIES = [
  'Delhi NCR', 'Bengaluru', 'Mumbai', 'Hyderabad', 'Pune',
  'Kolkata', 'Lucknow', 'Ahmedabad', 'Jaipur', 'Chennai',
];

// Warehouse feeder hubs that roll into a metro, so pipeline (IT/OO) lines
// up with where the DRR/stock is actually counted.
var FEEDER_TO_METRO = {
  faridabad: 'Delhi NCR', noida: 'Delhi NCR', kundli: 'Delhi NCR',
  gurgaon: 'Delhi NCR', gurugram: 'Delhi NCR', ghaziabad: 'Delhi NCR',
  bhiwandi: 'Mumbai',
};

var SKU_MASTER = [
  ['10160151', 'AL - Ladder', 'Advance 5 Step (Orange)'],
  ['10160152', 'AL - Ladder', 'Advance 4 Step'],
  ['10193800', 'AL - Ladder', 'Advance 6 Step'],
  ['10280885', 'ST - Ladder', 'Ascend 5 Step (Orange & Black)'],
  ['10193793', 'ST - Ladder', 'Ascend 4 Step (Orange & Black)'],
  ['10193815', 'CDS', 'Neo (Orange)'],
  ['10335218', 'CDS', 'Terra Medium (Orange)'],
  ['10335216', 'CDS', 'Neo + (Green & Black)'],
  ['10334669', 'CDS', 'Exa 6ft 6 Pipes'],
  ['10335217', 'CDS', 'Terra Extra Large (Orange)'],
  ['10193818', 'CDS', 'Terra Large (Orange)'],
  ['10193820', 'CDS', 'Aero CDS'],
  ['10193830', 'Dori', 'Dori Ivory (4)'],
  ['10194482', 'Fridge Roll', 'Fridge Roll 45 X 150 (White)'],
  ['10163106', 'Ironing Board', 'X Press Ace'],
  ['10193824', 'Stomo', 'Taro Pearl White'],
];

function normalizeCity_(raw) {
  if (!raw) return null;
  var s = String(raw).replace(/_/g, ' ').split(/\s+/).filter(Boolean).join(' ').trim();
  return s || null;
}

function locationToCity_(loc) {
  var s = (loc || '').toString().trim();
  if (!s || s === '#N/A') return null;
  s = s.replace(/\s+[A-Za-z]{1,2}\d{1,3}$/, '').trim(); // drop " H3", " M12", etc.
  if (!s) return null;
  var metro = FEEDER_TO_METRO[s.toLowerCase()];
  return metro || normalizeCity_(s);
}

function parseDate_(v) {
  if (v instanceof Date && !isNaN(v)) return v;
  if (typeof v !== 'string' || !v.trim()) return null;
  var parts = v.trim().split('-');
  if (parts.length !== 3) return null;
  var d = parseInt(parts[0], 10), m = parseInt(parts[1], 10), y = parseInt(parts[2], 10);
  if (!d || !m || !y) return null;
  var dt = new Date(y, m - 1, d);
  return isNaN(dt) ? null : dt;
}

function daysBetween_(a, b) {
  return Math.round((a - b) / 86400000);
}

function refreshBlinkitDRR() {
  var targetSet = {};
  TARGET_CITIES.forEach(function (c) { targetSet[c] = true; });

  var masterSS = SpreadsheetApp.openById(QCOM_MASTER_ID);

  // ---- sales ----
  var rawSheet = masterSS.getSheetByName('Blinkit_Raw');
  var rawValues = rawSheet.getDataRange().getValues();
  var header = rawValues[0].map(function (h) { return String(h).trim(); });
  var idx = {};
  header.forEach(function (h, i) { idx[h] = i; });

  var maxDate = null;
  for (var r = 1; r < rawValues.length; r++) {
    var d = parseDate_(rawValues[r][idx['date']]);
    if (d && (!maxDate || d > maxDate)) maxDate = d;
  }
  var cutoff = new Date(maxDate);
  cutoff.setDate(cutoff.getDate() - (WINDOW_DAYS - 1));

  var sales = {}; // "itemId|city" -> units in the last WINDOW_DAYS
  var lastSaleDate = {}; // "itemId|city" -> most recent Date with qty_sold > 0, ever
  for (var r = 1; r < rawValues.length; r++) {
    var row = rawValues[r];
    var city = normalizeCity_(row[idx['City_Name-Mapped']]);
    if (!city || !targetSet[city]) continue;
    var itemId = String(row[idx['Item ID']]).trim();
    var qty = parseFloat(row[idx['qty_sold']]) || 0;
    var d = parseDate_(row[idx['date']]);
    if (!d) continue;
    var key = itemId + '|' + city;
    if (qty > 0 && (!lastSaleDate[key] || d > lastSaleDate[key])) lastSaleDate[key] = d;
    if (d < cutoff) continue;
    sales[key] = (sales[key] || 0) + qty;
  }

  // ---- stock ----
  var invSheet = null;
  masterSS.getSheets().forEach(function (sh) {
    if (!invSheet && sh.getName().indexOf('Blinkit_Inventory') === 0) invSheet = sh;
  });
  var invValues = invSheet.getDataRange().getValues();
  var ih = invValues[0].map(function (h) { return String(h).trim(); });
  var iidx = {};
  ih.forEach(function (h, i) { iidx[h] = i; });

  var stock = {};
  for (var r = 1; r < invValues.length; r++) {
    var row = invValues[r];
    var city = normalizeCity_(row[iidx['City Name_Mapped']]);
    if (!city || !targetSet[city]) continue;
    var itemId = String(row[iidx['item_id']]).trim();
    var qty = parseFloat(row[iidx['Total Stock']]) || 0;
    var key = itemId + '|' + city;
    stock[key] = (stock[key] || 0) + qty;
  }

  // ---- pending / in-transit ----
  var pendingSS = SpreadsheetApp.openById(QCOM_PENDING_ID);

  function qtyByItemCity(sheetName, statusFilter) {
    var sh = pendingSS.getSheetByName(sheetName);
    var vals = sh.getDataRange().getValues();
    var h = vals[0].map(function (x) { return String(x).trim(); });
    var ix = {};
    h.forEach(function (x, i) { ix[x] = i; });
    var out = {};
    var skippedQty = 0;
    for (var r = 1; r < vals.length; r++) {
      var row = vals[r];
      if (ix['Quantity Outstanding'] === undefined) continue;
      var qty = parseFloat(row[ix['Quantity Outstanding']]) || 0;
      if (qty <= 0) continue;
      if (statusFilter) {
        var st = ix['PO Status'] !== undefined ? String(row[ix['PO Status']]).trim() : '';
        if (statusFilter.indexOf(st) === -1) continue;
      }
      var loc = ix['Location'] !== undefined ? row[ix['Location']] : '';
      var city = locationToCity_(loc);
      if (!city || !targetSet[city]) { skippedQty += qty; continue; }
      var itemId = String(row[ix['Item Code']]).trim();
      var key = itemId + '|' + city;
      out[key] = (out[key] || 0) + qty;
    }
    Logger.log(sheetName + ': ' + skippedQty + ' units outside the 10 target cities, not counted');
    return out;
  }

  var ooQty = qtyByItemCity('Blinkit Pending', ['Active']);
  var itQty = qtyByItemCity('Blinkit - In Transit', null);

  // ---- build rows ----
  var headerRow = [
    'Item ID', 'Category', 'SKU', 'City',
    'DRR (units/day, last ' + WINDOW_DAYS + 'd)', 'Stock',
    'DOC', 'In Transit', 'Qty', 'Open PO', 'Qty',
  ];
  var calcRows = [];
  var rowNum = 2; // row 1 = banner, row 2 = header, data starts at row 3
  var skippedDead = 0;

  SKU_MASTER.forEach(function (sku) {
    var itemId = sku[0], category = sku[1], name = sku[2];
    TARGET_CITIES.forEach(function (city) {
      var key = itemId + '|' + city;
      var st = stock[key] || 0;
      var lastSale = lastSaleDate[key];
      var soldRecently = lastSale && daysBetween_(maxDate, lastSale) < SIX_MONTHS_DAYS;

      if (!soldRecently && st === 0) { skippedDead++; return; }

      rowNum++;
      var units = sales[key] || 0;
      var drr = Math.round((units / WINDOW_DAYS) * 100) / 100;

      var dormantText;
      if (!lastSale) {
        dormantText = 'Never sold here';
      } else {
        var daysAgo = daysBetween_(maxDate, lastSale);
        dormantText = daysAgo < 60 ? ('No sales in ' + daysAgo + 'd') : ('No sales in ~' + Math.floor(daysAgo / 30) + 'mo');
      }

      var docFormula = '=IF(E' + rowNum + '=0, IF(F' + rowNum + '>0, "No sales (last ' + WINDOW_DAYS + 'd)", "' + dormantText + '"), ROUND(F' + rowNum + '/E' + rowNum + ', 1))';
      var itQ = itQty[key] || 0;
      var ooQ = ooQty[key] || 0;
      calcRows.push([itemId, category, name, city, drr, st, docFormula, itQ > 0 ? 'Y' : 'N', itQ, ooQ > 0 ? 'Y' : 'N', ooQ]);
    });
  });

  Logger.log('Skipped ' + skippedDead + ' SKU x city rows with no sales in ' + SIX_MONTHS_DAYS + 'd and no stock.');

  // ---- write ----
  var tracker = SpreadsheetApp.getActiveSpreadsheet();
  var ws = tracker.getSheetByName(TRACK_TAB);
  ws.clear();

  var now = new Date();
  var refreshedNote = 'Last refreshed: ' + Utilities.formatDate(now, Session.getScriptTimeZone(), 'dd-MMM-yyyy HH:mm')
    + ' | DRR window: last ' + WINDOW_DAYS + ' days | Cities: ' + TARGET_CITIES.length
    + ' | Source: Blinkit_Raw, Blinkit_Inventory, Blinkit Pending, Blinkit - In Transit';
  ws.getRange(1, 1).setValue(refreshedNote);
  ws.getRange(2, 1, 1, headerRow.length).setValues([headerRow]);
  if (calcRows.length > 0) {
    ws.getRange(3, 1, calcRows.length, headerRow.length).setValues(calcRows);
  }
  ws.setFrozenRows(2);

  var existingFilter = ws.getFilter();
  if (existingFilter) existingFilter.remove();
  if (calcRows.length > 0) {
    ws.getRange(2, 1, calcRows.length + 1, headerRow.length).createFilter();
  }

  Logger.log('Done. Wrote ' + calcRows.length + ' active SKU x city rows across the 10 target cities.');
}

/** Run this once by hand to install the daily 7am trigger. */
function setupDailyTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'refreshBlinkitDRR') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('refreshBlinkitDRR').timeBased().atHour(7).everyDays(1).create();
  refreshBlinkitDRR(); // also run once immediately so you see it working
}

function onOpen() {
  SpreadsheetApp.getUi().createMenu('DRR Tracker')
    .addItem('Refresh now', 'refreshBlinkitDRR')
    .addToUi();
}
