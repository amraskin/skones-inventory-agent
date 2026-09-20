"""Build a draft purchase-order workbook (one sheet per vendor) from reorder
suggestions, optionally enriched with live vendor pricing/stock."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .vendors.base import VendorProduct


def build_po_workbook(
    suggestions: pd.DataFrame,
    vendor_lookups: dict[str, VendorProduct] | None,
    output_path: str | Path,
) -> Path:
    """suggestions: rows from reorder.compute_reorder_suggestions where needs_reorder is True.
    vendor_lookups: optional {product_name: VendorProduct} from a vendor client's search_product calls.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    to_order = suggestions[suggestions["needs_reorder"]].copy()
    vendor_lookups = vendor_lookups or {}

    to_order["vendor_price"] = to_order["product"].map(
        lambda p: vendor_lookups[p].price if p in vendor_lookups else None
    )
    to_order["vendor_in_stock"] = to_order["product"].map(
        lambda p: vendor_lookups[p].in_stock if p in vendor_lookups else None
    )
    to_order["line_total"] = to_order["suggested_order_qty"] * to_order["vendor_price"]

    vendor_col = to_order["preferred_vendor"].fillna("unassigned")

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_cols = [
            "product",
            "sku",
            "category",
            "quantity_on_hand",
            "daily_sales_velocity",
            "days_of_stock_remaining",
            "suggested_order_qty",
            "vendor_price",
            "vendor_in_stock",
            "line_total",
            "preferred_vendor",
        ]
        to_order[summary_cols].to_excel(writer, sheet_name="All", index=False)

        for vendor, group in to_order.groupby(vendor_col):
            sheet_name = str(vendor)[:31]  # Excel sheet name limit
            group[summary_cols].to_excel(writer, sheet_name=sheet_name, index=False)

    return output_path


def default_output_path() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "output" / f"purchase_order_{date.today().isoformat()}.xlsx"
