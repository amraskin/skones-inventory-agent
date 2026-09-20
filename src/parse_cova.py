"""Parse the two COVA scheduled reports this pipeline runs on:

- "Reorder" report: per-SKU sales at four windows (7/14/30/60 days), COVA's
  own days-of-stock-left / expected-stock projections, current on-hand,
  on-order, minimum stock, last received date, days since last sold. This
  is the primary input - one file, no manual export wrangling.
- "Inventory On Hand by Product" report: used only to join in Brand (and
  Manufacturer), which the Reorder report doesn't include. Brand is what
  reorder.py groups SKUs by for the dampening/outlier logic - "Supplier" in
  the Reorder report is the distributor, not the brand, and one distributor
  commonly carries several different brands.

Both are native COVA exports with a fixed schema (unlike a bank/POS export
you'd customize), so columns are addressed directly rather than through a
configurable header-matching layer.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent

REORDER_COLUMNS = {
    "Product": "product",
    "SKU": "sku",
    "Classification": "category",
    "Supplier": "supplier",
    "Minimum Stock": "minimum_stock",
    "In Stock Qty": "quantity_on_hand",
    "On Order": "on_order",
    "Avg Unit Cost": "avg_unit_cost",
    "Price": "price",
    "Last Received Date": "last_received_date",
    "Days Since Last Sold": "days_since_last_sold",
    "Days Out of Stock": "days_out_of_stock",
}

CATALOG_COLUMNS = {
    "SKU": "sku",
    "Brand": "brand",
    "Manufacturer": "manufacturer",
}

DEFAULT_VELOCITY_WINDOW_DAYS = 30
DEFAULT_EXCLUDED_CLASSIFICATIONS_CONTAINING = ["fee"]


def parse_reorder_report(path: str | Path, velocity_window_days: int = DEFAULT_VELOCITY_WINDOW_DAYS) -> pd.DataFrame:
    """Returns one row per SKU with product/sku/category/supplier,
    quantity_on_hand, on_order, minimum_stock, daily_sales_velocity (from
    the chosen window), days_of_stock_left (COVA's own figure for that same
    window, kept for reference), last_received_date, days_since_last_sold,
    days_out_of_stock, avg_unit_cost, price.
    """
    if velocity_window_days not in (7, 14, 30, 60):
        raise ValueError("velocity_window_days must be one of 7, 14, 30, 60 (COVA's report windows)")

    df = pd.read_excel(path, sheet_name="Reorder")
    out = df.rename(columns=REORDER_COLUMNS)[list(REORDER_COLUMNS.values())].copy()

    sales_col = f"Sales ({velocity_window_days} Days)"
    stock_left_col = f"Days of Stock Left ({velocity_window_days} Days)"
    out["daily_sales_velocity"] = pd.to_numeric(df[sales_col], errors="coerce").fillna(0) / velocity_window_days
    out["days_of_stock_left"] = pd.to_numeric(df[stock_left_col], errors="coerce")

    for col in ["quantity_on_hand", "on_order", "minimum_stock", "avg_unit_cost", "price"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["quantity_on_hand"] = out["quantity_on_hand"].fillna(0)
    out["on_order"] = out["on_order"].fillna(0)

    return out


def parse_inventory_catalog(path: str | Path) -> pd.DataFrame:
    """Returns sku/brand/manufacturer, for joining brand into the reorder report."""
    df = pd.read_excel(path, sheet_name="Inventory by Product")
    return df.rename(columns=CATALOG_COLUMNS)[list(CATALOG_COLUMNS.values())].copy()


def join_brand(reorder_df: pd.DataFrame, catalog_df: pd.DataFrame) -> pd.DataFrame:
    """Left-joins brand/manufacturer onto the reorder report by SKU. SKUs
    with no brand on file (common for accessories/glass) get None - reorder.py
    falls back to grouping by (category, supplier) for those."""
    merged = reorder_df.merge(catalog_df, on="sku", how="left")
    unmatched = merged["brand"].isna().sum()
    if unmatched:
        print(f"Note: {unmatched} SKU(s) had no match in the inventory catalog / no brand on file.")
    return merged


def exclude_non_reorderable(df: pd.DataFrame, contains: list[str] | None = None) -> pd.DataFrame:
    """Drops rows whose category looks like a non-product line item (service
    fees, etc.) rather than something you'd actually reorder."""
    contains = contains or DEFAULT_EXCLUDED_CLASSIFICATIONS_CONTAINING
    pattern = "|".join(contains)
    mask = df["category"].fillna("").str.contains(pattern, case=False, regex=True)
    return df[~mask].copy()
