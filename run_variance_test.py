# -*- coding: utf-8 -*-
"""
run_variance_test.py
Cafeteria Critic -- reproducibility / variance check.

Scores ONE school N times on identical data and reports mean, SD, and range.
Use this to confirm the decomposition cache is holding the score stable
(target: SD 0.00 once all items are cached) or to measure run-to-run noise
if you ever clear the caches.

HOW TO RUN (in Spyder):
  %runfile C:/Users/josha/OneDrive/Documents/pai789/APAFINAL2/run_variance_test.py --wdir

  Edit SCHOOL and N_RUNS below, then run. CLI args don't pass via %runfile.

INTERPRETING RESULTS:
  - SD ~0.00 with all items showing "[decomp cached]" after run 1 = scoring is
    fully reproducible for this fixed dataset. This is REPRODUCIBILITY, not
    total method accuracy -- caching freezes whatever the first decomposition
    produced. Residual uncertainty lives in the decomposition assumptions
    themselves, which are now fixed and documented rather than re-rolled.
  - If SD is large, look at which item's food groups change between runs --
    that's the item driving the noise (historically "Fresh Fruit"). Seed it in
    nutrislice_fped_bridge.DECOMP_SEED to lock it.
"""

import importlib
import statistics

import config
import nutrislice_scraper as ns
import nutrislice_fped_bridge as bridge
import step3b_fped

importlib.reload(step3b_fped)
importlib.reload(bridge)

config.load_keys()


# ── Configuration ────────────────────────────────────────────────────────────

# (slug, school_id, menu_id, display_name, grade_band)
SCHOOL = ("westgenesee", 34082, 9238, "Split Rock", "K-8")
N_RUNS = 4
FPED_PATH = "FPED_1718.xlsx"


# ── Run ──────────────────────────────────────────────────────────────────────

def main():
    slug, sid, mid, name, band = SCHOOL

    # Scrape once; reuse the same data for every run so we isolate scoring noise
    days = ns.scrape_school_year(slug, sid, mid, name)
    avg  = ns.average_nutrition(days)

    scores = []
    for i in range(N_RUNS):
        print(f"\n{'='*30} RUN {i+1} {'='*30}")
        r = bridge.complete_hei_score(
            days=days,
            avg_nutrition=avg,
            district_name=f"{name} RUN {i+1}",
            grade_band=band,
            fped_path=FPED_PATH,
        )
        scores.append(r["total_score"])

    print(f"\n{'='*60}")
    print(f"Scores: {scores}")
    print(f"Mean:   {statistics.mean(scores):.2f}")
    print(f"StdDev: {statistics.pstdev(scores):.2f}")
    print(f"Range:  {min(scores):.1f} - {max(scores):.1f}")


if __name__ == "__main__":
    main()
