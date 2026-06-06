# -*- coding: utf-8 -*-
"""
run_fresno.py
Cafeteria Critic -- discrimination test: Fresno Unified (CA).

Same engine and ledger as run_batch.py, pre-loaded with five Fresno Unified
schools. This is the "does the tool see real differences?" test: Fresno is a
large, high-poverty California Central Valley district -- a totally different
region, demographic, and food operation from central NY. If it ALSO lands near
52, the "NSLP flattens lunch quality everywhere" finding gets much stronger. If
it lands clearly higher or lower, the tool discriminates and that difference is
itself the finding.

HOW TO RUN (in Spyder):
  %runfile C:/Users/josha/OneDrive/Documents/pai789/APAFINAL2/run_fresno.py --wdir

NOTES:
  - REBUILD_CACHE is False on purpose: keep the populated decomposition cache so
    items Fresno SHARES with NY (pizza, hamburger, etc.) decompose identically
    -- that shared error is what makes the comparison valid. Fresno's novel
    items decompose fresh and cache themselves.
  - All five schools use Fresno's district-wide Lunch menu_id = 3546.
  - CALENDAR CAVEAT: the scraper's SCHOOL_YEAR_START/END are set for the NY
    calendar (Sep 2 - Jun 19). California's year differs (earlier start, earlier
    end), so you may see "empty" weeks at the edges -- that just means a partial
    year, which is fine for a comparison. Glance at the per-week output.
  - Results append to the SAME ledger as run_batch.py (hei_results_ledger.csv),
    so Fresno lands in the same growing table as your six NY schools.
"""

import os
import importlib
import statistics
from datetime import datetime

import config
import nutrislice_scraper as ns
import nutrislice_fped_bridge as bridge
import step3b_fped

importlib.reload(step3b_fped)
importlib.reload(bridge)

config.load_keys()


# ── Configuration ────────────────────────────────────────────────────────────

REBUILD_CACHE = False                      # keep populated cache (shared error)
RESULTS_CSV   = "hei_results_ledger.csv"   # same ledger as run_batch.py
FPED_PATH     = "FPED_1718.xlsx"

# Fresno Unified School District (Fresno, CA) -- verified CA in probe.
# All use the district-wide Lunch menu (id=3546).
# (slug, school_id, menu_id, display_name, grade_band)
DISTRICTS = [
    ("fresnounified", 28172, 3546, "Fresno - Addams Elem",    "K-8"),
    ("fresnounified", 28206, 3546, "Fresno - Lincoln Elem",   "K-8"),
    ("fresnounified", 28242, 3546, "Fresno - Fort Miller MS", "6-8"),
    ("fresnounified", 28254, 3546, "Fresno - Bullard HS",     "9-12"),
    ("fresnounified", 28260, 3546, "Fresno - Edison HS",      "9-12"),
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
    except Exception:
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
    print("FRESNO UNIFIED (CA) -- HEI SCORES")
    print(f"{'='*64}")
    for row in rows:
        s = row["total_score"]
        s_str = f"{s:.1f}" if isinstance(s, (int, float)) else str(s)
        print(f"  {row['school']:<28} {s_str}   ({row['n_days']} days, "
              f"{row['calories']} kcal)")
    if len(scores) > 1:
        print(f"\n  spread: {min(scores):.1f} - {max(scores):.1f}  "
              f"(range {max(scores)-min(scores):.1f})")
        print(f"  mean:   {statistics.mean(scores):.2f}   "
              f"SD: {statistics.pstdev(scores):.2f}")
        print(f"\n  Compare to central NY: 52.5 - 53.3 (six schools, two districts)")


if __name__ == "__main__":
    main()
