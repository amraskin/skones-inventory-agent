"""Build a draft purchase-order workbook from reorder suggestions, optionally
enriched with live vendor pricing/stock/substitutes.

Layout:
  - "All"      every SKU actually being ordered, across all vendors
  - "<vendor>" one sheet per vendor, same rows filtered to that vendor
  - "Watching" low-stock SKUs that are dampened by group-level logic -
    visible for awareness, not on the order
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .vendors.base import VendorProduct

ORDER_COLS = [
    "product",
    "sku",
    "category",
    "brand",
    "quantity_on_hand",
    "baseline_qty",
    "pct_remaining",
    "weekly_sales_velocity",
    "is_outlier_mover",
    "suggested_order_qty",
    "vendor_match",
    "vendor_price",
    "vendor_in_stock",
    "substitute_note",
    "line_total",
    "preferred_vendor",
]

WATCHING_COLS = [
    "product",
    "sku",
    "category",
    "brand",
    "quantity_on_hand",
    "baseline_qty",
    "pct_remaining",
    "weekly_sales_velocity",
    "group_healthy",
]


def _annotate_vendor_columns(df: pd.DataFrame, vendor_lookups: dict[str, VendorProduct]) -> pd.DataFrame:
    df = df.copy()

    def _get(p, attr):
        match = vendor_lookups.get(p)
        return getattr(match, attr) if match else None

    df["vendor_match"] = df["product"].map(lambda p: _get(p, "raw_match_name"))
    df["vendor_price"] = df["product"].map(lambda p: _get(p, "price"))
    df["vendor_in_stock"] = df["product"].map(lambda p: _get(p, "in_stock"))
    df["substitute_note"] = df["product"].map(
        lambda p: f"Substitute for out-of-stock item: {vendor_lookups[p].name}"
        if p in vendor_lookups and vendor_lookups[p].is_substitute
        else None
    )
    df["line_total"] = df["suggested_order_qty"] * df["vendor_price"]
    return df


def build_po_workbook(
    to_order: pd.DataFrame,
    watching: pd.DataFrame,
    vendor_lookups: dict[str, VendorProduct] | None,
    output_path: str | Path,
) -> Path:
    """to_order / watching: the two slices of reorder.compute_reorder_suggestions
    (status == "reorder" vs "watching"). vendor_lookups: optional
    {product_name: VendorProduct} from a vendor client's search_product /
    find_substitute calls.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    vendor_lookups = vendor_lookups or {}
    to_order = _annotate_vendor_columns(to_order, vendor_lookups)
    vendor_col = to_order["preferred_vendor"].fillna("unassigned") if len(to_order) else pd.Series(dtype=object)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        to_order.reindex(columns=ORDER_COLS).to_excel(writer, sheet_name="All", index=False)

        for vendor, group in to_order.groupby(vendor_col):
            sheet_name = str(vendor)[:31]  # Excel sheet name limit
            group.reindex(columns=ORDER_COLS).to_excel(writer, sheet_name=sheet_name, index=False)

        watching.reindex(columns=WATCHING_COLS).to_excel(writer, sheet_name="Watching", index=False)

    return output_path


def default_output_path() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "output" / f"purchase_order_{date.today().isoformat()}.xlsx"
