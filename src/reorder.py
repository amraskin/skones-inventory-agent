"""Decide what needs reordering, given sales velocity + current stock levels.

Rules live in config/reorder_rules.yaml (see reorder_rules.example.yaml for
the schema) so they can be tuned without touching this code.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import yaml

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


def compute_reorder_suggestions(sales_summary: pd.DataFrame, inventory_df: pd.DataFrame) -> pd.DataFrame:
    """sales_summary: output of parse_cova.summarize_sales (product, sku, category, daily_sales_velocity)
    inventory_df: output of parse_cova.parse_inventory_export (product, sku, category, quantity_on_hand)

    Returns one row per product with reorder decision + suggested quantity.
    """
    rules = load_rules()

    merged = pd.merge(
        inventory_df,
        sales_summary[["product", "sku", "daily_sales_velocity"]],
        on=["product", "sku"],
        how="left",
    )
    merged["daily_sales_velocity"] = merged["daily_sales_velocity"].fillna(0)

    records = []
    for _, row in merged.iterrows():
        rule = _rule_for(rules, row["product"], row.get("category"))
        if rule.get("do_not_reorder"):
            continue

        velocity = row["daily_sales_velocity"]
        threshold_days = rule["lead_time_days"] + rule["safety_stock_days"]

        if velocity > 0:
            days_of_stock_remaining = row["quantity_on_hand"] / velocity
        else:
            days_of_stock_remaining = LARGE_DAYS_OF_STOCK

        par_level = rule.get("par_level")
        needs_reorder = days_of_stock_remaining <= threshold_days or (
            par_level is not None and row["quantity_on_hand"] < par_level
        )

        target_days = threshold_days + rule.get("reorder_cycle_days", 0)
        suggested_qty = math.ceil(velocity * target_days - row["quantity_on_hand"])
        if par_level is not None:
            suggested_qty = max(suggested_qty, par_level - row["quantity_on_hand"])
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
                "quantity_on_hand": row["quantity_on_hand"],
                "daily_sales_velocity": round(velocity, 2),
                "days_of_stock_remaining": round(min(days_of_stock_remaining, LARGE_DAYS_OF_STOCK), 1),
                "needs_reorder": bool(needs_reorder),
                "suggested_order_qty": int(suggested_qty),
                "preferred_vendor": rule.get("preferred_vendor"),
            }
        )

    result = pd.DataFrame.from_records(records)
    return result.sort_values("days_of_stock_remaining")
