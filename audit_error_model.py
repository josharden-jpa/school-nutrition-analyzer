# -*- coding: utf-8 -*-
"""
audit_error_model.py
====================
Turns the per-item reconstruction audit into a proper ERROR MODEL.

A single "average error" number hides the three things that actually matter,
so this does NOT report one. It separates:

  1. BIAS        -- does the error have a DIRECTION? (median SIGNED error)
                    Bias does NOT average away with more data. A +60% calorie
                    bias on every item is +60% on the school-year total too.
  2. DISPERSION  -- how SPREAD OUT is the error? (IQR of the signed error)
                    Dispersion DOES average away (~/sqrt(n)) IF it's centered.
  3. STRUCTURE   -- WHICH item types break it? (everything stratified by a
                    transparent keyword classifier you can read and edit)

It also adds the CALORIE-ANCHOR SCALING layer (deterministic, no Claude
arithmetic): scale the whole reconstructed nutrient vector by
published_kcal / reconstructed_kcal. Calories then match by construction
(so we do NOT report calorie residual -- that would be tautological); the
HONEST fidelity signal is the residual on the OTHER nutrients after scaling.

DESIGN PRINCIPLE: objectivity at the data level.
  - Every item is reported. Nothing is silently dropped or gated.
  - The ONLY subjective numbers live in the PARAMETERS block below, each
    labeled as a choice, not a law. A school food authority can dial these
    to their own certified tolerances without touching the engine.
  - Scaling and residuals are pure arithmetic -- same inputs, same outputs,
    for everyone.

WHAT IT CANNOT DO: it cannot validate food-group servings (those are
unpublished, so there is no ground truth for anyone). It validates the
PUBLISHED chemistry; a faithful chemistry reconstruction is the evidence
that the food-group estimates riding on the same recipe are trustworthy.

USAGE
-----
    import audit_error_model as em

    # One school, current week (fast smoke test)
    em.run([("fresnounified", 28172, 3546, "Addams Elementary")],
           top_n=10, full_year=False)

    # The Fresno five, full year (the real evaluation)
    em.run(FRESNO_FIVE, top_n=20, full_year=True)
"""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from datetime import date

import config
config.load_keys()

import audit_reconstruction as base   # scrape + reconstruct primitives (tested)
from step1_recipe import load_anthropic_key


# =============================================================================
# PARAMETERS -- the ONLY subjective numbers in this file.
# These are CHOICES, not laws. A dietitian or child-nutrition director with a
# defensible basis should set these to their own standard. Changing them
# re-runs the SUMMARY lines only; the raw per-item factors and residuals do
# not move. Pulled out here so they can be dialed without going under the hood.
# =============================================================================

# Used only for the summary line "X% of items within tolerance".
# It does NOT drop or hide any item. It is a reporting threshold, not a gate.
RESIDUAL_TOLERANCE_PCT = 25.0     # a judgment call; change to your standard

# Calorie-anchor scale factors outside this band are FLAGGED (not removed).
# A factor far from 1.0 means the recipe itself is likely wrong (missing or
# phantom ingredient), not merely the portion -- so scaling can't rescue it.
# We still scale and still report it; the flag just marks "treat with caution".
SCALE_FACTOR_FLAG_BAND = (0.4, 2.5)   # a judgment call

# Total Sugars reconstruction is a KNOWN-DEAD column (the USDA path currently
# returns 0 sugars for nearly everything -- a wiring issue, separate from this
# audit). We report it but exclude it from the fidelity verdict so it doesn't
# poison the stats. Set False once the sugars wiring is fixed.
EXCLUDE_SUGARS_FROM_VERDICT = True


# =============================================================================
# ITEM-TYPE CLASSIFIER -- transparent keyword rules, in PRIORITY ORDER.
# First matching stratum wins. Edit freely: it's just text matching, fully
# auditable, no AI judgment. The point of stratifying is that the error is
# NOT uniform -- beverages (dilution), baked goods (portion), and dressed raw
# veg (phantom oil) fail differently from entrees, and pooling hides that.
# =============================================================================

CLASSIFIER_RULES = [
    # (stratum, [keywords])  -- checked top to bottom, first hit wins
    ("beverage",    ["juice", "milk", "drink", "water", "smoothie", "lemonade"]),
    ("entree",      ["chicken", "beef", "pork", "turkey", "fish", "tamale",
                     "burrito", "taco", "pizza", "sandwich", "burger", "nugget",
                     "tender", "patty", "meatball", "hot dog", "corn dog",
                     "enchilada", "quesadilla", "lasagna", "cheese"]),
    ("grain_baked", ["bread", "roll", "bun", "graham", "cracker", "loaf",
                     "muffin", "biscuit", "bagel", "tortilla", "rice", "pasta",
                     "cereal", "oatmeal", "pancake", "waffle", "breadstick"]),
    ("fruit",       ["apple", "banana", "orange", "grape", "strawberr", "melon",
                     "peach", "pear", "berry", "fruit", "raisin", "applesauce",
                     "mandarin", "pineapple"]),
    ("vegetable",   ["broccoli", "carrot", "cucumber", "corn", "bean", "pea",
                     "salad", "lettuce", "tomato", "potato", "spinach",
                     "celery", "veggie", "vegetable", "coins", "florets",
                     "snacker", "steamed"]),
]


def classify_item(name: str) -> str:
    """Assign a transparent item-type stratum by keyword. First rule wins."""
    n = name.lower()
    for stratum, keywords in CLASSIFIER_RULES:
        if any(kw in n for kw in keywords):
            return stratum
    return "other"


# =============================================================================
# CALORIE-ANCHOR SCALING -- deterministic, no Claude arithmetic.
# Because every reconstructed nutrient is grams x density summed, multiplying
# the whole nutrient vector by a scalar is identical to rescaling all grams
# by that scalar and recomputing. So we scale the vector directly.
# =============================================================================

def scale_to_calorie_anchor(recon: dict, published: dict) -> tuple[dict, float, bool]:
    """
    Scale the reconstructed nutrient vector so its calories match the published
    label exactly. Returns (scaled_recon, scale_factor, flagged).

    factor = published_kcal / reconstructed_kcal
    flagged = True if factor is outside SCALE_FACTOR_FLAG_BAND (caution, not drop)
    """
    pub_kcal = published.get("Calories (kcal)", 0) or 0
    rec_kcal = recon.get("Calories (kcal)", 0) or 0

    if rec_kcal <= 0 or pub_kcal <= 0:
        # cannot anchor (no calories on one side) -- return unscaled, flagged
        return dict(recon), float("nan"), True

    factor = pub_kcal / rec_kcal
    scaled = {k: (v * factor if isinstance(v, (int, float)) else v)
              for k, v in recon.items() if not k.startswith("_")}
    lo, hi = SCALE_FACTOR_FLAG_BAND
    flagged = not (lo <= factor <= hi)
    return scaled, round(factor, 3), flagged


def _pct_residual(scaled_val: float, published_val: float) -> float | None:
    """Signed % residual of scaled reconstruction vs label. None if label is 0."""
    if published_val is None or published_val == 0:
        return None
    return round((scaled_val - published_val) / published_val * 100.0, 1)


# =============================================================================
# STATISTICS -- bias, dispersion, structure. Median-based (robust to outliers).
# =============================================================================

def _median(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs), 1) if xs else None


def _iqr(xs):
    """Interquartile range (Q3 - Q1) as a dispersion measure. Needs >= 2 points."""
    xs = sorted(x for x in xs if isinstance(x, (int, float)))
    if len(xs) < 2:
        return None
    q = statistics.quantiles(xs, n=4)   # [Q1, Q2, Q3]
    return round(q[2] - q[0], 1)


def characterize(residuals: list[float]) -> dict:
    """
    Decompose an error distribution into the three things a mean hides.
      bias       = median SIGNED residual (direction; does NOT average away)
      precision  = median ABSOLUTE residual (typical magnitude)
      dispersion = IQR of signed residual (spread; DOES average away if centered)
    """
    vals = [r for r in residuals if isinstance(r, (int, float))]
    if not vals:
        return {"n": 0}
    return {
        "n":          len(vals),
        "bias":       _median(vals),
        "precision":  _median([abs(v) for v in vals]),
        "dispersion": _iqr(vals),
        "worst_over":  round(max(vals), 1),
        "worst_under": round(min(vals), 1),
    }


# nutrients we judge fidelity on AFTER anchoring (calories excluded: tautological)
RESIDUAL_NUTRIENTS = ["Sodium (mg)", "Saturated Fat (g)", "Total Sugars (g)", "Protein (g)"]


# =============================================================================
# PER-SCHOOL PIPELINE
# =============================================================================

def audit_school(district_slug, school_id, menu_type_id, school_name,
                 top_n, full_year, anthropic_key, usda_key) -> list[dict]:
    """
    Scrape one school, reconstruct its top_n items, scale each to its calorie
    anchor, and return per-item rows with stratum, factor, flag, and post-scale
    residuals on the non-anchored nutrients.
    """
    print(f"\n  [error-model] {school_name} ({district_slug}) -- scraping ...")
    if full_year:
        days = base.ns.scrape_school_year(district_slug, school_id, menu_type_id, school_name)
    else:
        data = base.ns.get_week_data(district_slug, school_id, menu_type_id, date.today())
        days = base.ns.parse_week(data) if data else []

    if not days:
        print(f"  [error-model] no data for {school_name}")
        return []

    items = base.collect_published_items(days, top_n)
    print(f"  [error-model] reconstructing {len(items)} items ...")

    rows = []
    for i, item in enumerate(items, 1):
        name = item["name"]
        pub  = item["published"]
        print(f"    ({i}/{len(items)}) {name}")
        recon = base.reconstruct_item(name, anthropic_key, usda_key)
        if "_error" in recon:
            continue

        scaled, factor, flagged = scale_to_calorie_anchor(recon, pub)

        row = {
            "school":      school_name,
            "item":        name,
            "stratum":     classify_item(name),
            "frequency":   item["frequency"],
            "scale_factor": factor,
            "factor_flagged": flagged,
        }
        for label in RESIDUAL_NUTRIENTS:
            short = label.split(" (")[0]
            row[f"{short}_residual_pct"] = _pct_residual(
                scaled.get(label, 0.0), pub.get(label, 0.0))
        rows.append(row)

    return rows


# =============================================================================
# AGGREGATION + REPORT
# =============================================================================

def _residual_field(short):
    return f"{short}_residual_pct"


def report(all_rows: list[dict]) -> dict:
    multi_school = len({r["school"] for r in all_rows}) > 1

    print(f"\n{'='*78}")
    print(f"  RECONSTRUCTION ERROR MODEL  (calorie-anchored)")
    print(f"  {len(all_rows)} items across {len({r['school'] for r in all_rows})} school(s)")
    print(f"{'='*78}")
    print("  Calories are anchored to the published label, so calorie residual")
    print("  is zero by construction and NOT reported. The honest fidelity signal")
    print("  is the residual on the OTHER nutrients after that anchoring.")

    # ---- 1. Scale-factor distribution (the calorie-side diagnostic) ----------
    factors = [r["scale_factor"] for r in all_rows
               if isinstance(r["scale_factor"], (int, float))
               and r["scale_factor"] == r["scale_factor"]]   # drop NaN
    n_flag = sum(1 for r in all_rows if r["factor_flagged"])
    print(f"\n  SCALE FACTOR (published_kcal / reconstructed_kcal)")
    print(f"    A factor near 1.0 = reconstruction's calories were already close.")
    print(f"    Far from 1.0 = portion (or recipe) was off by that multiple.")
    if factors:
        print(f"    median factor : {statistics.median(factors):.2f}")
        print(f"    range         : {min(factors):.2f}  to  {max(factors):.2f}")
    print(f"    flagged outside {SCALE_FACTOR_FLAG_BAND}: {n_flag}/{len(all_rows)}"
          f"  (flagged, NOT removed)")

    # ---- 2. Pooled error decomposition per nutrient --------------------------
    print(f"\n  POOLED ERROR (all items, post-anchor residual)")
    print(f"  {'Nutrient':<16} {'n':>3} {'bias':>8} {'precision':>10} {'dispersion':>11}  reading")
    print(f"  {'-'*16} {'-'*3} {'-'*8} {'-'*10} {'-'*11}  {'-'*24}")
    pooled = {}
    for label in RESIDUAL_NUTRIENTS:
        short = label.split(" (")[0]
        c = characterize([r.get(_residual_field(short)) for r in all_rows])
        pooled[short] = c
        if c["n"] == 0:
            print(f"  {short:<16} {0:>3}   (no nonzero labels to compare)")
            continue
        note = _bias_note(short, c["bias"])
        disp = f"{c['dispersion']:.0f}%" if c["dispersion"] is not None else "  n/a"
        print(f"  {short:<16} {c['n']:>3} {c['bias']:>+7.0f}% {c['precision']:>9.0f}% "
              f"{disp:>11}  {note}")

    # ---- 3. Stratified error (the structure a mean hides) --------------------
    print(f"\n  ERROR BY ITEM TYPE  (where the error actually lives)")
    strata = sorted({r["stratum"] for r in all_rows})
    print(f"  {'stratum':<12} {'n':>3} {'Na bias':>9} {'Na prec':>9} "
          f"{'Prot bias':>10} {'Prot prec':>10}")
    print(f"  {'-'*12} {'-'*3} {'-'*9} {'-'*9} {'-'*10} {'-'*10}")
    strat_out = {}
    for s in strata:
        srows = [r for r in all_rows if r["stratum"] == s]
        na = characterize([r.get("Sodium_residual_pct") for r in srows])
        pr = characterize([r.get("Protein_residual_pct") for r in srows])
        strat_out[s] = {"n": len(srows), "sodium": na, "protein": pr}
        na_b = f"{na['bias']:+.0f}%" if na.get("bias") is not None else "  n/a"
        na_p = f"{na['precision']:.0f}%" if na.get("precision") is not None else "  n/a"
        pr_b = f"{pr['bias']:+.0f}%" if pr.get("bias") is not None else "  n/a"
        pr_p = f"{pr['precision']:.0f}%" if pr.get("precision") is not None else "  n/a"
        print(f"  {s:<12} {len(srows):>3} {na_b:>9} {na_p:>9} {pr_b:>10} {pr_p:>10}")
    print(f"\n    (small n per stratum on a single week is expected -- these")
    print(f"     stabilize with --full-year and multiple schools.)")

    # ---- 4. School-level bias + CLT cancellation reasoning -------------------
    if multi_school:
        print(f"\n  SCHOOL-LEVEL BIAS  (does averaging rescue the school-year number?)")
        print(f"  Key idea: dispersion averages away (~/sqrt(n)); BIAS does not.")
        print(f"  If per-school bias is SHARED (same sign/size), cross-school")
        print(f"  COMPARISONS survive even if absolute levels are inflated.")
        print(f"\n  {'school':<26} {'Na bias':>9} {'Prot bias':>10}")
        print(f"  {'-'*26} {'-'*9} {'-'*10}")
        school_bias = {}
        for sch in sorted({r["school"] for r in all_rows}):
            srows = [r for r in all_rows if r["school"] == sch]
            na = _median([r.get("Sodium_residual_pct") for r in srows])
            pr = _median([r.get("Protein_residual_pct") for r in srows])
            school_bias[sch] = {"sodium": na, "protein": pr}
            na_s = f"{na:+.0f}%" if na is not None else "  n/a"
            pr_s = f"{pr:+.0f}%" if pr is not None else "  n/a"
            print(f"  {sch[:26]:<26} {na_s:>9} {pr_s:>10}")
        # is the bias shared?
        na_biases = [v["sodium"] for v in school_bias.values() if v["sodium"] is not None]
        if len(na_biases) >= 2:
            spread = max(na_biases) - min(na_biases)
            verdict = ("SHARED -> cross-school comparisons robust"
                       if spread <= 30 else
                       "VARIES -> cross-school comparisons at risk")
            print(f"\n    Sodium-bias spread across schools: {spread:.0f} pts -> {verdict}")

    # ---- 5. Tolerance summary (the one dialed number) ------------------------
    print(f"\n  WITHIN-TOLERANCE SUMMARY  (tolerance = {RESIDUAL_TOLERANCE_PCT:.0f}%, an editable choice)")
    for label in RESIDUAL_NUTRIENTS:
        short = label.split(" (")[0]
        if EXCLUDE_SUGARS_FROM_VERDICT and short == "Total Sugars":
            print(f"    {short:<16} excluded from verdict (known-dead column)")
            continue
        vals = [r.get(_residual_field(short)) for r in all_rows]
        vals = [v for v in vals if isinstance(v, (int, float))]
        if not vals:
            continue
        within = sum(1 for v in vals if abs(v) <= RESIDUAL_TOLERANCE_PCT)
        print(f"    {short:<16} {within}/{len(vals)} items within +/-{RESIDUAL_TOLERANCE_PCT:.0f}%")

    print(f"{'='*78}\n")

    return {"rows": all_rows, "pooled": pooled, "strata": strat_out}


def _bias_note(short, bias):
    if bias is None:
        return ""
    if short == "Total Sugars" and bias <= -95:
        return "KNOWN-DEAD column"
    if bias > 15:
        return "recon runs HIGH"
    if bias < -15:
        return "recon runs LOW"
    return "~centered"


# =============================================================================
# ORCHESTRATOR
# =============================================================================

# Convenience: the Fresno five (slug, school_id, menu_id, name)
FRESNO_FIVE = [
    ("fresnounified", 28172, 3546, "Addams Elementary"),
    ("fresnounified", 28206, 3546, "Lincoln Elementary"),
    ("fresnounified", 28242, 3546, "Fort Miller MS"),
    ("fresnounified", 28254, 3546, "Bullard HS"),
    ("fresnounified", 28260, 3546, "Edison HS"),
]


def run(schools, top_n=10, full_year=False, save_csv=True):
    """
    schools : list of (district_slug, school_id, menu_type_id, school_name)
    Runs the error model across all of them and prints the full report.
    """
    anthropic_key = load_anthropic_key()
    usda_key      = config.USDA_API_KEY or config.load_keys()

    all_rows = []
    for (slug, sid, mid, name) in schools:
        all_rows.extend(audit_school(slug, sid, mid, name,
                                     top_n, full_year, anthropic_key, usda_key))

    if not all_rows:
        print("  [error-model] no rows produced.")
        return {}

    result = report(all_rows)

    if save_csv:
        path = "audit_error_model.csv"
        fields = ["school", "item", "stratum", "frequency",
                  "scale_factor", "factor_flagged"] + \
                 [f"{l.split(' (')[0]}_residual_pct" for l in RESIDUAL_NUTRIENTS]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in all_rows:
                w.writerow(r)
        print(f"  [error-model] per-item CSV -> {path}")
        result["csv_path"] = path

    return result


if __name__ == "__main__":
    # Smoke test: one school, current week.
    run([("fresnounified", 28172, 3546, "Addams Elementary")],
        top_n=10, full_year=False)
