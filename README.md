# Skones Inventory Agent

Automates the "what should I reorder?" workflow for Skones Dispensary:

1. Parse daily COVA sales and inventory exports.
2. Compute a sales velocity per SKU and decide what's actually worth
   reordering (see "How reorder decisions are made" below) - not just "low
   stock", since a strain being low doesn't matter if its siblings aren't
   moving either.
3. Check live pricing/stock for flagged products on vendor sites (currently
   Nabis), substituting a similar in-stock product when the original is out.
4. Write a draft purchase order (spreadsheet) for you to review and place.

This does **not** place orders automatically - it only produces a suggested
PO for you to check and submit yourself.

## How reorder decisions are made

Full model and the tunable numbers live in
`config/reorder_rules.example.yaml` (copy it to `reorder_rules.yaml` to
edit). Summary:

1. **Velocity gate** - a SKU selling under `min_weekly_velocity` (default 1
   unit/week) is dropped entirely. Not enough sales signal to justify
   reordering it no matter how low it is.
2. **"Low" test** - a SKU counts as low if it's under `reorder_pct_threshold`
   (default 26%) of its own baseline purchase quantity, or under an explicit
   `par_level` if you've set one for it.
3. **Grouping** - SKUs are grouped by Category + Brand (e.g. all of one
   brand's flower strains). A group is "healthy" if its combined stock
   across every SKU in it would last through your lead time + safety stock
   at the group's overall selling pace.
4. **Outlier mover** - within a group, a SKU that accounts for
   `outlier_share_threshold` (default 70%) or more of the group's total
   sales is an outlier: it's the one actually moving while its groupmates
   sit still, so its own low stock is a real signal even if the group looks
   fine in aggregate.
5. **Decision** - a low SKU goes on the actual **purchase order** if its
   group isn't healthy, or it's an outlier mover, or it breached an explicit
   par level. A low SKU that's just noise (healthy group, no outlier) goes
   on a separate **Watching** sheet instead - visible, not urgent.
6. **Substitutes** - if the vendor site shows the ordered item out of stock,
   the tool looks for another SKU in the same Category + Brand line and
   suggests that instead, flagged as a substitute on the PO.

Since COVA exports are just point-in-time snapshots (no purchase-history
field), "baseline purchase quantity" is inferred automatically by
`src/baseline_tracker.py`: it watches for a SKU's on-hand quantity jumping
up between runs (a restock) and treats the new, higher quantity as the
fresh baseline. The first time a SKU is seen, its current quantity is used
as a rough starting baseline until the next real restock.

## Status

Scaffolded and the core pipeline (parsing → reorder math → PO workbook) is
implemented and smoke-tested with synthetic data. Two things still need real
inputs before this is production-ready:

- **COVA column mapping** (`config/column_map.yaml`) - built from guessed
  common header names; verify/adjust against a real COVA export.
- **Nabis selectors** (`config/nabis_selectors.yaml`) - this repo's dev
  environment cannot reach `ny.nabis.com` (network policy), so the catalog
  scraping selectors are unverified placeholders. See "Nabis setup" below.
- **Reorder rules** (`config/reorder_rules.yaml`) - the decision model
  (grouping, thresholds, outlier detection) is implemented and smoke-tested;
  the actual numbers (lead times, thresholds per category, par levels) are
  still generic defaults - tune them for your real products as you go.

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

This writes `output/purchase_order_<date>.xlsx` with an "All" sheet, one
sheet per vendor, and a "Watching" sheet for dampened low-stock SKUs. Add
`--no-vendor-lookup` to skip live pricing and just get the reorder list.

Run it once per day so `src/baseline_tracker.py` sees a steady stream of
snapshots - running it twice against the same inventory numbers is harmless,
but skipping days makes restock detection less precise.

## Project layout

```
config/                     reorder rules, COVA column mapping, vendor selectors
                             (copy each .example.yaml to a real .yaml to edit - not gitignored,
                              since it's business config, not credentials)
data/                       drop COVA exports here; also holds session_state/ and state/
                             (gitignored - real sales/inventory data and sessions never get committed)
output/                     generated purchase orders land here (gitignored)
scripts/nabis_login.py      one-time interactive login to capture a Nabis session
src/parse_cova.py           COVA export parsing
src/baseline_tracker.py     infers each SKU's "original purchase quantity" from restock jumps
src/reorder.py              reorder decision logic (grouping, outlier detection, thresholds)
src/generate_po.py          purchase-order workbook builder
src/vendors/                one module per vendor site (nabis.py so far)
src/run_daily.py            CLI entrypoint tying it all together
```

## Adding another vendor

Implement `VendorClient` (see `src/vendors/base.py`) the same way
`src/vendors/nabis.py` does, and set `preferred_vendor` in
`config/reorder_rules.yaml` for the products that vendor supplies.
