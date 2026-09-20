"""Daily entrypoint: parse today's COVA exports, decide what needs reordering,
optionally check live vendor pricing, and write a draft purchase order.

Usage:
    python -m src.run_daily \\
        --sales data/sales_export.xlsx \\
        --inventory data/inventory_export.xlsx \\
        [--no-vendor-lookup] [--output output/purchase_order.xlsx]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import generate_po, parse_cova, reorder


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sales", required=True, help="Path to COVA sales export (xlsx/csv)")
    parser.add_argument("--inventory", required=True, help="Path to COVA inventory/stock export (xlsx/csv)")
    parser.add_argument("--output", default=None, help="Output PO workbook path")
    parser.add_argument(
        "--no-vendor-lookup",
        action="store_true",
        help="Skip live vendor pricing lookups; just produce the reorder list",
    )
    args = parser.parse_args()

    print(f"Parsing sales export: {args.sales}")
    sales_df = parse_cova.parse_sales_export(args.sales)
    sales_summary = parse_cova.summarize_sales(sales_df)

    print(f"Parsing inventory export: {args.inventory}")
    inventory_df = parse_cova.parse_inventory_export(args.inventory)

    print("Computing reorder suggestions...")
    suggestions = reorder.compute_reorder_suggestions(sales_summary, inventory_df)
    if suggestions.empty:
        print("Nothing is low enough to reorder or watch right now.")
        return
    to_order = suggestions[suggestions["needs_reorder"]]
    watching = suggestions[~suggestions["needs_reorder"]]
    print(f"{len(to_order)} product(s) need reordering, {len(watching)} on the watch list.")

    vendor_lookups = {}
    if not args.no_vendor_lookup and len(to_order) > 0:
        nabis_rows = to_order[to_order["preferred_vendor"] == "nabis"]
        if len(nabis_rows) > 0:
            try:
                from .vendors.nabis import NabisClient

                print(f"Looking up {len(nabis_rows)} product(s) on Nabis...")
                with NabisClient(headless=True) as client:
                    client.login()
                    for _, row in nabis_rows.iterrows():
                        product_name = row["product"]
                        result = client.search_product(product_name)
                        if result is None or result.in_stock is False:
                            print(f"  {product_name} unavailable on Nabis, looking for a substitute...", file=sys.stderr)
                            result = client.find_substitute(row.get("category"), row.get("brand"), product_name)
                            if result:
                                print(f"    substitute found: {result.name}", file=sys.stderr)
                        if result:
                            vendor_lookups[product_name] = result
                        else:
                            print(f"  no match or substitute found on Nabis for: {product_name}", file=sys.stderr)
            except Exception as e:
                print(f"Vendor lookup skipped/failed: {e}", file=sys.stderr)

    output_path = args.output or generate_po.default_output_path()
    path = generate_po.build_po_workbook(to_order, watching, vendor_lookups, output_path)
    print(f"Draft purchase order written to: {path}")


if __name__ == "__main__":
    main()
