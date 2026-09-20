"""Decide what needs reordering, given sales velocity, current stock, and
each SKU's inferred baseline purchase quantity.

Rules live in config/reorder_rules.yaml (see reorder_rules.example.yaml for
the full explanation of the model) so they can be tuned without touching
this code.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import yaml

from . import baseline_tracker

REPO_ROOT = Path(__file__).resolve().parent.parent
LARGE_DAYS_OF_STOCK = 10_000  # sentinel for "not selling, no urgency"


def load_rules() -> dict:
    path = REPO_ROOT / "config" / "reorder_rules.yaml"
    if not path.exists():
        path = REPO_ROOT / "config" / "reorder_rules.example.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def _rule_for(rules: dict, product: str, category: str | None) -> dict:
    merged = dict(rules["defaults"])
    if category and category in rules.get("categories", {}):
        merged.update(rules["categories"][category])
    if product in rules.get("products", {}):
        merged.update(rules["products"][product])
    return merged


def _group_key(category, brand, supplier) -> tuple:
    # Brand is the real grouping signal; fall back to supplier for the SKUs
    # (mostly accessories/glass) that have no brand on file.
    if pd.notna(brand) and brand:
        return (category, "brand", brand)
    if pd.notna(supplier) and supplier:
        return (category, "supplier", supplier)
    return (category, "none", None)


def compute_reorder_suggestions(inventory_df: pd.DataFrame) -> pd.DataFrame:
    """inventory_df: output of parse_cova.join_brand(parse_reorder_report(...), parse_inventory_catalog(...))
    - needs product, sku, category, brand, supplier, quantity_on_hand,
      on_order, minimum_stock, daily_sales_velocity, days_of_stock_left.

    Returns one row per reorder-eligible SKU (velocity-gated SKUs are
    dropped) with a status of "reorder", "watching", or "on_order" and a
    suggested order quantity.
    """
    rules = load_rules()

    merged = baseline_tracker.update_and_get_baselines(inventory_df)
    merged["group_key"] = [
        _group_key(c, b, s) for c, b, s in zip(merged["category"], merged["brand"], merged["supplier"])
    ]

    # Group aggregates use ALL SKUs regardless of the velocity gate below -
    # a slow-moving groupmate's stock still counts toward "is this product
    # line as a whole running low."
    group_totals = (
        merged.groupby("group_key")
        .agg(group_qty_on_hand=("quantity_on_hand", "sum"), group_velocity=("daily_sales_velocity", "sum"))
        .to_dict("index")
    )

    records = []
    for _, row in merged.iterrows():
        rule = _rule_for(rules, row["product"], row.get("category"))
        if rule.get("do_not_reorder"):
            continue

        weekly_velocity = row["daily_sales_velocity"] * 7
        if weekly_velocity < rule["min_weekly_velocity"]:
            continue  # not enough sales signal either way

        baseline_qty = row["baseline_qty"] or 0
        # baseline_qty can legitimately be 0 (a SKU first seen already out of
        # stock, before any restock has been observed) - that's still selling
        # at least min_weekly_velocity/week per the gate above, so it's a
        # real "out of stock" case, not a "0 of 0, so 100% fine" one.
        out_of_stock = row["quantity_on_hand"] <= 0
        pct_remaining = 0.0 if out_of_stock else (row["quantity_on_hand"] / baseline_qty if baseline_qty > 0 else 1.0)

        # An explicit override in reorder_rules.yaml wins; otherwise fall
        # back to whatever Minimum Stock is set to in COVA itself.
        par_level = rule.get("par_level")
        if par_level is None and pd.notna(row.get("minimum_stock")):
            par_level = row["minimum_stock"]
        par_breach = par_level is not None and row["quantity_on_hand"] < par_level
        is_low = out_of_stock or pct_remaining < rule["reorder_pct_threshold"] or par_breach

        if not is_low:
            continue

        group = group_totals.get(row["group_key"], {"group_qty_on_hand": row["quantity_on_hand"], "group_velocity": row["daily_sales_velocity"]})
        group_velocity = group["group_velocity"]
        group_threshold_days = rule["lead_time_days"] + rule["safety_stock_days"]
        group_days_of_stock = (
            group["group_qty_on_hand"] / group_velocity if group_velocity > 0 else LARGE_DAYS_OF_STOCK
        )
        group_healthy = group_days_of_stock > group_threshold_days

        sku_share_of_group = (row["daily_sales_velocity"] / group_velocity) if group_velocity > 0 else 1.0
        is_outlier_mover = sku_share_of_group >= rule["outlier_share_threshold"]

        would_reorder = par_breach or not group_healthy or is_outlier_mover
        status = "reorder" if would_reorder else "watching"

        if baseline_qty > 0:
            target_qty = max(baseline_qty, par_level or 0)
        else:
            # No restock has been observed yet for this SKU, so there's no
            # baseline to replenish toward - fall back to a classic
            # reorder-point estimate (enough to cover lead time + safety
            # stock at its selling pace) until a real baseline exists.
            target_qty = max(
                row["daily_sales_velocity"] * group_threshold_days,
                par_level or 0,
                rule.get("min_order_qty", 1),
            )
        shortfall = math.ceil(target_qty - row["quantity_on_hand"])
        shortfall = max(shortfall, 0)
        on_order = row.get("on_order") or 0

        if would_reorder and shortfall > 0 and on_order >= shortfall:
            status = "on_order"  # already covered by an order already placed

        suggested_qty = max(shortfall - on_order, 0)
        if suggested_qty > 0:
            suggested_qty = max(suggested_qty, rule.get("min_order_qty", 1))
        if rule.get("max_order_qty"):
            suggested_qty = min(suggested_qty, rule["max_order_qty"])

        records.append(
            {
                "product": row["product"],
                "sku": row["sku"],
                "category": row.get("category"),
                "brand": row.get("brand"),
                "supplier": row.get("supplier"),
                "quantity_on_hand": row["quantity_on_hand"],
                "on_order": on_order,
                "baseline_qty": baseline_qty,
                "pct_remaining": round(pct_remaining, 3),
                "daily_sales_velocity": round(row["daily_sales_velocity"], 2),
                "weekly_sales_velocity": round(weekly_velocity, 2),
                "days_of_stock_left": row.get("days_of_stock_left"),
                "group_healthy": bool(group_healthy),
                "is_outlier_mover": bool(is_outlier_mover),
                "status": status,  # "reorder", "watching", or "on_order"
                "needs_reorder": status == "reorder",
                "suggested_order_qty": int(suggested_qty),
                "preferred_vendor": rule.get("preferred_vendor"),
            }
        )

    result = pd.DataFrame.from_records(records)
    if result.empty:
        return result
    status_order = {"reorder": 0, "watching": 1, "on_order": 2}
    result["_status_order"] = result["status"].map(status_order)
    result = result.sort_values(["_status_order", "pct_remaining"], ascending=[True, True]).drop(columns="_status_order")
    return result
