"""Infers each SKU's "original purchase quantity" - the 26%-of-what number
compares against - since COVA exports are just point-in-time snapshots with
no purchase/receiving history attached.

Approach: keep a small local record per SKU of the last on-hand quantity we
saw. A restock is detected when either (a) on-hand went up since last time,
or (b) COVA's own "Last Received Date" for that SKU advanced - whichever
signal is available. When detected, the new on-hand quantity becomes the
fresh baseline. The very first time a SKU is seen, its current quantity
becomes the initial baseline (a rough guess until the next real restock).

State persists in data/state/inventory_baselines.json (gitignored - it's
derived operational state, not something to version).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "data" / "state" / "inventory_baselines.json"


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    with open(STATE_PATH) as f:
        return json.load(f)


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def update_and_get_baselines(inventory_df: pd.DataFrame, as_of: str | None = None) -> pd.DataFrame:
    """inventory_df: output of parse_cova.parse_inventory_export (needs sku, quantity_on_hand).

    Returns inventory_df with an added `baseline_qty` column, and updates the
    persisted state file as a side effect (call this once per real daily run,
    not repeatedly against the same snapshot, or restocks will be missed).
    """
    as_of = as_of or pd.Timestamp.now().isoformat()
    state = _load_state()

    has_received_date = "last_received_date" in inventory_df.columns

    baselines = []
    for _, row in inventory_df.iterrows():
        sku = str(row["sku"])
        current_qty = row["quantity_on_hand"]
        received_date = str(row["last_received_date"]) if has_received_date and pd.notna(row["last_received_date"]) else None
        entry = state.get(sku)

        if entry is None:
            baseline_qty = current_qty
        else:
            qty_increased = current_qty > entry["last_seen_qty"]
            newly_received = (
                received_date is not None
                and received_date != entry.get("last_received_date")
            )
            baseline_qty = current_qty if (qty_increased or newly_received) else entry["baseline_qty"]

        state[sku] = {
            "baseline_qty": baseline_qty,
            "last_seen_qty": current_qty,
            "last_received_date": received_date,
            "last_seen_at": as_of,
        }
        baselines.append(baseline_qty)

    _save_state(state)

    out = inventory_df.copy()
    out["baseline_qty"] = baselines
    return out
