"""Daily entrypoint: parse the COVA "Reorder" report (joined with "Inventory
On Hand by Product" for Brand), decide what needs reordering, optionally
check live vendor pricing, and write a draft purchase order.

Usage:
    python -m src.run_daily \\
        --reorder-report data/Reorder.xlsx \\
        --inventory-catalog data/InventoryOnHandByProduct.xlsx \\
        [--velocity-window 30] [--no-vendor-lookup] [--output output/purchase_order.xlsx]
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
    parser.add_argument("--reorder-report", required=True, help="Path to COVA 'Reorder' scheduled report (xlsx)")
    parser.add_argument(
        "--inventory-catalog",
        required=True,
        help="Path to COVA 'Inventory On Hand by Product' report (xlsx) - used to join in Brand",
    )
    parser.add_argument(
        "--velocity-window",
        type=int,
        default=parse_cova.DEFAULT_VELOCITY_WINDOW_DAYS,
        choices=[7, 14, 30, 60],
        help="Which of COVA's sales windows to use for daily velocity (default: 30)",
    )
    parser.add_argument("--output", default=None, help="Output PO workbook path")
    parser.add_argument(
        "--no-vendor-lookup",
        action="store_true",
        help="Skip live vendor pricing lookups; just produce the reorder list",
    )
    args = parser.parse_args()

    print(f"Parsing reorder report: {args.reorder_report}")
    reorder_df = parse_cova.parse_reorder_report(args.reorder_report, velocity_window_days=args.velocity_window)

    print(f"Parsing inventory catalog for brand: {args.inventory_catalog}")
    catalog_df = parse_cova.parse_inventory_catalog(args.inventory_catalog)

    merged = parse_cova.join_brand(reorder_df, catalog_df)
    merged = parse_cova.exclude_non_reorderable(merged)

    print("Computing reorder suggestions...")
    suggestions = reorder.compute_reorder_suggestions(merged)
    if suggestions.empty:
        print("Nothing is low enough to reorder or watch right now.")
        return

    to_order = suggestions[suggestions["status"] == "reorder"]
    watching = suggestions[suggestions["status"] == "watching"]
    already_on_order = suggestions[suggestions["status"] == "on_order"]
    print(
        f"{len(to_order)} product(s) need reordering, {len(watching)} on the watch list, "
        f"{len(already_on_order)} already covered by an existing order."
    )

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
    path = generate_po.build_po_workbook(to_order, watching, already_on_order, vendor_lookups, output_path)
    print(f"Draft purchase order written to: {path}")


if __name__ == "__main__":
    main()
