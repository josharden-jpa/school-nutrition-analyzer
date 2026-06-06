# -*- coding: utf-8 -*-
"""
run_batch.py
Cafeteria Critic -- batch HEI-2020 scoring driver.

Scrapes a full school year for each school in DISTRICTS, scores it against the
shared FPED + decomposition caches, and appends every result to a growing
CSV ledger (RESULTS_CSV) so you build a permanent comparable dataset across
every district you ever run.

WHY THIS FILE EXISTS:
  This is the *driver* -- the thing that operates the engine. The engine is
  nutrislice_scraper + nutrislice_fped_bridge + step3b_fped. This script just
  loops over a list of schools and collects scores. Keep it; the exact list of
  districts and the loop IS the methodology a reviewer would ask about.

HOW TO RUN (in Spyder):
  %runfile C:/Users/josha/OneDrive/Documents/pai789/APAFINAL2/run_batch.py --wdir

  CLI args don't pass through %runfile, so edit DISTRICTS below and the
  REBUILD_CACHE flag directly, then run.

REPRODUCIBILITY NOTES:
  - Scores are deterministic once decompositions are cached (proven SD 0.00).
  - Set REBUILD_CACHE = True to delete decomp_learned.json first so every
    school in this run decomposes shared items against ONE fresh shared cache
    (apples-to-apples). Set False to keep the existing cache (faster, and fine
    if the cache is already populated for these items).
  - The seed in nutrislice_fped_bridge.DECOMP_SEED stays locked regardless.
"""

import os
import importlib
import statistics
from datetime import datetime

import config
import nutrislice_scraper as ns
import nutrislice_fped_bridge as bridge
import step3b_fped

# Reload engine modules so edits to them take effect without restarting Spyder
importlib.reload(step3b_fped)
importlib.reload(bridge)

config.load_keys()


# ── Configuration ────────────────────────────────────────────────────────────

# Delete the decomposition cache before running so all schools below decompose
# shared items exactly once, against the same fresh cache. Use True when you
# want a clean apples-to-apples comparison set; False to reuse existing cache.
REBUILD_CACHE = False

# Where to append results. One row per school, accumulates across every run.
RESULTS_CSV = "hei_results_ledger.csv"

FPED_PATH = "FPED_1718.xlsx"

# Schools to score this run.
# (slug, school_id, menu_id, display_name, grade_band)
DISTRICTS = [
    ("scarsdaleschools", 4717, 4065, "Scarsdale Middle (spring snapshot)",      "6-8"),
    ("scarsdaleschools", 5908, 2350, "Scarsdale Edgewood El (spring snapshot)", "K-5"),
]

# ── Run ──────────────────────────────────────────────────────────────────────

def main():
    if REBUILD_CACHE and os.path.exists("decomp_learned.json"):
        os.remove("decomp_learned.json")
        print("Cleared decomp_learned.json -- rebuilding shared cache\n")

    rows = []
    run_stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    for slug, sid, mid, name, band in DISTRICTS:
        print(f"\n{'#'*64}\n# {name}\n{'#'*64}")
        days = ns.scrape_school_year(slug, sid, mid, name)
        avg  = ns.average_nutrition(days)
        r = bridge.complete_hei_score(
            days=days,
            avg_nutrition=avg,
            district_name=name,
            grade_band=band,
            fped_path=FPED_PATH,
        )

        # Pull component scores if present; tolerate partial result dicts
        comps = r.get("component_scores", {}) or {}
        rows.append({
            "run_stamp":      run_stamp,
            "district_slug":  slug,
            "school":         name,
            "grade_band":     band,
            "school_id":      sid,
            "menu_id":        mid,
            "n_days":         len(days),
            "calories":       round(avg.get("calories", 0), 1) if isinstance(avg, dict) else "",
            "total_score":    r.get("total_score"),
            "grade":          r.get("letter_grade", r.get("grade", "")),
            "whole_grains":   comps.get("Whole Grains", ""),
            "dairy":          comps.get("Dairy", ""),
            "sodium":         comps.get("Sodium", ""),
            "total_veg":      comps.get("Total Vegetables", ""),
            "total_fruits":   comps.get("Total Fruits", ""),
            "protein_foods":  comps.get("Total Protein Foods", ""),
        })

    # ── Append to the ledger ─────────────────────────────────────────────────
    try:
        import pandas as pd
        df = pd.DataFrame(rows)
        write_header = not os.path.exists(RESULTS_CSV)
        df.to_csv(RESULTS_CSV, mode="a", header=write_header, index=False)
        print(f"\n[ledger] Appended {len(rows)} rows -> {RESULTS_CSV}")
    except Exception as e:
        # Fallback: write a plain CSV without pandas
        import csv
        write_header = not os.path.exists(RESULTS_CSV)
        with open(RESULTS_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            if write_header:
                w.writeheader()
            w.writerows(rows)
        print(f"\n[ledger] (no pandas) Appended {len(rows)} rows -> {RESULTS_CSV}")

    # ── Final block ──────────────────────────────────────────────────────────
    scores = [row["total_score"] for row in rows if row["total_score"] is not None]
    print(f"\n{'='*64}")
    print("FINAL SHARED-CACHE SCORES (all schools, same decompositions)")
    print(f"{'='*64}")
    for row in rows:
        s = row["total_score"]
        s_str = f"{s:.1f}" if isinstance(s, (int, float)) else str(s)
        print(f"  {row['school']:<30} {s_str}")
    if len(scores) > 1:
        print(f"\n  spread: {min(scores):.1f} - {max(scores):.1f}  "
              f"(range {max(scores)-min(scores):.1f})")
        print(f"  mean:   {statistics.mean(scores):.2f}   "
              f"SD: {statistics.pstdev(scores):.2f}")


if __name__ == "__main__":
    main()
