# Skones Inventory Agent

Automates the "what should I reorder?" workflow for Skones Dispensary:

1. Parse daily COVA sales and inventory exports.
2. Compute a sales velocity per product and flag anything running low
   against configurable reorder rules (lead time, safety stock, par levels).
3. Check live pricing/stock for flagged products on vendor sites (currently
   Nabis).
4. Write a draft purchase order (spreadsheet) for you to review and place.

This does **not** place orders automatically - it only produces a suggested
PO for you to check and submit yourself.

## Status

Scaffolded and the core pipeline (parsing → reorder math → PO workbook) is
implemented and smoke-tested with synthetic data. Two things still need real
inputs before this is production-ready:

- **COVA column mapping** (`config/column_map.yaml`) - built from guessed
  common header names; verify/adjust against a real COVA export.
- **Nabis selectors** (`config/nabis_selectors.yaml`) - this repo's dev
  environment cannot reach `ny.nabis.com` (network policy), so the catalog
  scraping selectors are unverified placeholders. See "Nabis setup" below.
- **Reorder rules** (`config/reorder_rules.yaml`) - currently generic
  defaults; replace with your actual lead times / safety stock / par levels
  per category or product.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium   # only needed if not already available
cp .env.example .env                              # fill in if you add password-based vendors later
cp config/reorder_rules.example.yaml config/reorder_rules.yaml
cp config/column_map.example.yaml config/column_map.yaml
cp config/nabis_selectors.example.yaml config/nabis_selectors.yaml
```

## Nabis setup (one-time, and after any session expiry)

Nabis logins go through session cookies rather than scripting a raw
username/password (B2B portals like this typically have bot protection
and/or 2FA, which a headless login script will trip). Run this from a
machine with normal internet access:

```bash
python scripts/nabis_login.py
```

A real browser window opens to Nabis - log in there yourself (including any
verification step), then press Enter in the terminal. Your session is saved
to `data/session_state/nabis.json` (gitignored) and reused by
`src/vendors/nabis.py` on future runs.

Then, with the DOM actually visible, use your browser's "Inspect Element" on
the login and catalog/search pages to fill in the real selectors in
`config/nabis_selectors.yaml` (see comments in the `.example.yaml` for what
each one does).

## Running it

Drop your daily COVA exports into `data/` (or anywhere), then:

```bash
python -m src.run_daily --sales data/sales_export.xlsx --inventory data/inventory_export.xlsx
```

This writes `output/purchase_order_<date>.xlsx` with an "All" sheet plus one
sheet per vendor. Add `--no-vendor-lookup` to skip live pricing and just get
the reorder list.

## Project layout

```
config/            reorder rules, COVA column mapping, vendor selectors (real copies are gitignored)
data/               drop COVA exports here (gitignored - real sales/inventory data never gets committed)
output/             generated purchase orders land here (gitignored)
scripts/nabis_login.py   one-time interactive login to capture a Nabis session
src/parse_cova.py         COVA export parsing
src/reorder.py            reorder decision logic
src/generate_po.py        purchase-order workbook builder
src/vendors/              one module per vendor site (nabis.py so far)
src/run_daily.py          CLI entrypoint tying it all together
```

## Adding another vendor

Implement `VendorClient` (see `src/vendors/base.py`) the same way
`src/vendors/nabis.py` does, and set `preferred_vendor` in
`config/reorder_rules.yaml` for the products that vendor supplies.
