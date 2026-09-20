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


def _group_key(category, brand) -> tuple:
    # Falls back to category-only grouping if brand wasn't in the export.
    return (category, brand) if pd.notna(brand) and brand else (category, None)


def compute_reorder_suggestions(sales_summary: pd.DataFrame, inventory_df: pd.DataFrame) -> pd.DataFrame:
    """sales_summary: output of parse_cova.summarize_sales (product, sku, category, brand, daily_sales_velocity)
    inventory_df: output of parse_cova.parse_inventory_export (product, sku, category, brand, quantity_on_hand)

    Returns one row per reorder-eligible SKU (velocity-gated SKUs are
    dropped) with a status of "reorder" or "watching" and a suggested
    order quantity.
    """
    rules = load_rules()

    inventory_with_baseline = baseline_tracker.update_and_get_baselines(inventory_df)

    merged = pd.merge(
        inventory_with_baseline,
        sales_summary[["product", "sku", "daily_sales_velocity"]],
        on=["product", "sku"],
        how="left",
    )
    merged["daily_sales_velocity"] = merged["daily_sales_velocity"].fillna(0)
    merged["group_key"] = [
        _group_key(c, b) for c, b in zip(merged.get("category"), merged.get("brand"))
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
        pct_remaining = (row["quantity_on_hand"] / baseline_qty) if baseline_qty > 0 else 1.0
        par_level = rule.get("par_level")
        par_breach = par_level is not None and row["quantity_on_hand"] < par_level
        is_low = pct_remaining < rule["reorder_pct_threshold"] or par_breach

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

        status = "reorder" if (par_breach or not group_healthy or is_outlier_mover) else "watching"

        target_qty = max(baseline_qty, par_level or 0)
        suggested_qty = math.ceil(target_qty - row["quantity_on_hand"])
        suggested_qty = max(suggested_qty, 0)
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
                "quantity_on_hand": row["quantity_on_hand"],
                "baseline_qty": baseline_qty,
                "pct_remaining": round(pct_remaining, 3),
                "daily_sales_velocity": round(row["daily_sales_velocity"], 2),
                "weekly_sales_velocity": round(weekly_velocity, 2),
                "group_healthy": bool(group_healthy),
                "is_outlier_mover": bool(is_outlier_mover),
                "status": status,  # "reorder" or "watching"
                "needs_reorder": status == "reorder",
                "suggested_order_qty": int(suggested_qty),
                "preferred_vendor": rule.get("preferred_vendor"),
            }
        )

    result = pd.DataFrame.from_records(records)
    if result.empty:
        return result
    return result.sort_values(["status", "pct_remaining"], ascending=[True, True])
