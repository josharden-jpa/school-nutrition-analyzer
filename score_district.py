# -*- coding: utf-8 -*-
"""
score_district.py  (v2 — full HEI-2020)
Scores school lunch quality using the complete USDA Healthy Eating Index 2020.

HEI-2020 has 13 components totaling 100 points:
  9 adequacy components (more = better):
    Total Fruits, Whole Fruits, Total Vegetables, Greens & Beans,
    Whole Grains, Dairy, Total Protein Foods, Seafood & Plant Proteins,
    Fatty Acids (ratio)
  4 moderation components (less = better):
    Refined Grains, Sodium, Saturated Fats, Added Sugars

All components use a DENSITY approach: amounts per 1,000 kcal.
This makes scores comparable across meals of different sizes.

Data sources required
---------------------
  From step3  (USDA FoodData Central nutrients):
    - Calories (kcal)         ← denominator for all density calculations
    - Sodium (mg)             ← moderation component (direct HEI citation)
    - Saturated Fat (g)       ← moderation component (direct HEI citation)
    - Total MUFA (g)          ← fatty acids numerator (if available)
    - Total PUFA (g)          ← fatty acids numerator (if available)

  From step3b (FPED food group equivalents, optional but recommended):
    - hei_components dict with cup/oz equivalents per meal

Scoring standards source
------------------------
  Table 1, HEI-2020 Components and Scoring Standards
  https://epi.grants.cancer.gov/hei/hei-2020-table1.html
  National Cancer Institute / USDA CNPP, updated August 2024

Usage
-----
    from score_district import score_meal, score_district, explain_score

    # Full HEI (requires FPED from step3b):
    result = score_meal(
        nutrient_totals = step3_result["totals"],
        fped_result     = step3b_result,
        meal_name       = "Soft Shell Taco",
    )

    # Partial HEI — nutrient-only fallback (no FPED needed):
    result = score_meal(
        nutrient_totals = step3_result["totals"],
        meal_name       = "Soft Shell Taco",
    )

    explain_score(result)
"""

from __future__ import annotations


# ── NSLP calorie targets (USDA FNS, 7 CFR 210.10, 2022) ──────────────────────
NSLP_CALORIE_TARGETS = {
    "K-5":  (550, 650),
    "6-8":  (600, 700),
    "9-12": (750, 850),
}
NSLP_DEFAULT_BAND = "6-8"


# ── HEI-2020 scoring standards ────────────────────────────────────────────────
# Source: https://epi.grants.cancer.gov/hei/hei-2020-table1.html
#
# ADEQUACY: (max_pts, zero_at, full_at, unit, data_source)
#   score rises linearly from zero_at → full_at
#
# MODERATION: (max_pts, full_pts_at_or_below, zero_pts_at_or_above, unit, data_source)
#   score falls linearly from full_pts_at_or_below → zero_pts_at_or_above
#
# data_source: "fped" = food group data from step3b
#              "nutrient" = nutrient data from step3

HEI_ADEQUACY = {
    "Total Fruits":               (5,  0,    0.8,  "cup eq/1000kcal", "fped"),
    "Whole Fruits":               (5,  0,    0.4,  "cup eq/1000kcal", "fped"),
    "Total Vegetables":           (5,  0,    1.1,  "cup eq/1000kcal", "fped"),
    "Greens and Beans":           (5,  0,    0.2,  "cup eq/1000kcal", "fped"),
    "Whole Grains":               (10, 0,    1.5,  "oz eq/1000kcal",  "fped"),
    "Dairy":                      (10, 0,    1.3,  "cup eq/1000kcal", "fped"),
    "Total Protein Foods":        (5,  0,    2.5,  "oz eq/1000kcal",  "fped"),
    "Seafood and Plant Proteins": (5,  0,    0.8,  "oz eq/1000kcal",  "fped"),
    "Fatty Acids":                (10, 1.2,  2.5,  "(MUFA+PUFA)/SFA", "nutrient"),
}

HEI_MODERATION = {
    "Refined Grains": (10, 1.8,  4.3,  "oz eq/1000kcal", "fped"),
    "Sodium":         (10, 1.1,  2.0,  "g/1000kcal",     "nutrient"),
    "Saturated Fats": (10, 8.0,  16.0, "% of energy",    "nutrient"),
    "Added Sugars":   (10, 6.5,  26.0, "% of energy",    "fped"),
}


# ── Scoring math ──────────────────────────────────────────────────────────────

def _density(amount: float, calories: float) -> float:
    return (amount / calories * 1000.0) if calories > 0 else 0.0


def _pct_of_energy(grams: float, cal_per_gram: float, total_kcal: float) -> float:
    return (grams * cal_per_gram / total_kcal * 100.0) if total_kcal > 0 else 0.0


def _score_adequacy(value, zero_at, full_at, max_pts):
    if full_at <= zero_at:
        return 0.0
    clamped = max(zero_at, min(full_at, value))
    return round(max_pts * (clamped - zero_at) / (full_at - zero_at), 3)


def _score_moderation(value, full_at, zero_at, max_pts):
    if zero_at <= full_at:
        return 0.0
    clamped = max(full_at, min(zero_at, value))
    return round(max_pts * (1.0 - (clamped - full_at) / (zero_at - full_at)), 3)


def _letter_grade(score: float) -> str:
    if score >= 90: return "A"
    if score >= 80: return "B"
    if score >= 70: return "C"
    if score >= 60: return "D"
    return "F"


# ── Main scoring ──────────────────────────────────────────────────────────────

def score_meal(
    nutrient_totals: dict,
    fped_result:     dict = None,
    meal_name:       str  = "Meal",
    grade_band:      str  = NSLP_DEFAULT_BAND,
) -> dict:
    """
    Score a single meal against HEI-2020 standards.

    Parameters
    ----------
    nutrient_totals : dict from step3  { "Calories (kcal)": 415, ... }
    fped_result     : dict from step3b { "hei_components": {...}, ... }
                      Pass None to get a partial score from nutrients only.
    meal_name       : display name
    grade_band      : "K-5", "6-8", "9-12"
    """
    kcal    = nutrient_totals.get("Calories (kcal)", 0) or 0
    sodium  = nutrient_totals.get("Sodium (mg)", 0) or 0
    sat_fat = nutrient_totals.get("Saturated Fat (g)", 0) or 0
    mufa    = nutrient_totals.get("Total MUFA (g)", 0) or 0
    pufa    = nutrient_totals.get("Total PUFA (g)", 0) or 0

    has_fped = fped_result is not None
    hei_fg   = fped_result["hei_components"] if has_fped else {}
    coverage = fped_result.get("coverage", {}) if has_fped else {}

    # Calorie adequacy flag (NSLP target, not part of density score)
    cal_lo, cal_hi = NSLP_CALORIE_TARGETS.get(
        grade_band, NSLP_CALORIE_TARGETS[NSLP_DEFAULT_BAND])
    if kcal < cal_lo:   calorie_flag = "LOW"
    elif kcal > cal_hi: calorie_flag = "HIGH"
    else:               calorie_flag = "OK"

    components = {}
    skipped    = []

    # ── Adequacy ──────────────────────────────────────────────────────────────
    for label, (max_pts, zero_at, full_at, unit, source) in HEI_ADEQUACY.items():

        if source == "fped" and not has_fped:
            skipped.append(label)
            continue

        if label == "Fatty Acids":
            if sat_fat <= 0 or (mufa == 0 and pufa == 0):
                skipped.append(label)
                continue
            ratio   = (mufa + pufa) / sat_fat
            density = ratio
            raw     = ratio
            score   = _score_adequacy(ratio, zero_at, full_at, max_pts)

        else:
            raw     = hei_fg.get(label, 0) or 0
            density = _density(raw, kcal)
            score   = _score_adequacy(density, zero_at, full_at, max_pts)

        components[label] = {
            "type":      "adequacy",
            "source":    source,
            "raw":       round(raw, 4),
            "density":   round(density, 4),
            "unit":      unit,
            "score":     score,
            "max_score": max_pts,
        }

    # ── Moderation ────────────────────────────────────────────────────────────
    for label, (max_pts, full_at, zero_at, unit, source) in HEI_MODERATION.items():

        if source == "fped" and not has_fped:
            skipped.append(label)
            continue

        if label == "Sodium":
            density = _density(sodium / 1000.0, kcal)   # mg → g/1000kcal
            raw     = sodium / 1000.0
            score   = _score_moderation(density, full_at, zero_at, max_pts)

        elif label == "Saturated Fats":
            pct     = _pct_of_energy(sat_fat, 9.0, kcal)
            density = pct
            raw     = sat_fat
            score   = _score_moderation(pct, full_at, zero_at, max_pts)

        elif label == "Added Sugars":
            tsp     = hei_fg.get("Added Sugars", 0) or 0
            pct     = (tsp * 16.0 / kcal * 100.0) if kcal > 0 else 0.0
            density = pct
            raw     = tsp
            score   = _score_moderation(pct, full_at, zero_at, max_pts)

        elif label == "Refined Grains":
            raw     = hei_fg.get("Refined Grains", 0) or 0
            density = _density(raw, kcal)
            score   = _score_moderation(density, full_at, zero_at, max_pts)

        else:
            skipped.append(label)
            continue

        components[label] = {
            "type":      "moderation",
            "source":    source,
            "raw":       round(raw, 4),
            "density":   round(density, 4),
            "unit":      unit,
            "score":     score,
            "max_score": max_pts,
        }

    # ── Totals ────────────────────────────────────────────────────────────────
    scored_max  = sum(c["max_score"] for c in components.values())
    raw_score   = round(sum(c["score"] for c in components.values()), 2)
    total_score = round(raw_score / scored_max * 100, 1) if scored_max > 0 else 0.0

    zero_adeq = [n for n, c in components.items()
                 if c["type"] == "adequacy" and c["score"] == 0]

    summary = (
        f"{meal_name} scores {total_score}/100 ({_letter_grade(total_score)})"
        + (" [partial — load FPED for full score]" if skipped else "") + "."
    )
    if zero_adeq:
        summary += f" Zero on: {', '.join(zero_adeq)}."
    if calorie_flag != "OK":
        summary += (
            f" Calories ({kcal:.0f} kcal) "
            f"{'below' if calorie_flag=='LOW' else 'above'} "
            f"NSLP target ({cal_lo}–{cal_hi} kcal, grades {grade_band})."
        )

    return {
        "meal_name":     meal_name,
        "grade_band":    grade_band,
        "calories":      round(kcal, 1),
        "calorie_flag":  calorie_flag,
        "calorie_target": f"{cal_lo}–{cal_hi} kcal",
        "components":    components,
        "skipped":       skipped,
        "raw_score":     raw_score,
        "scored_max":    scored_max,
        "total_score":   total_score,
        "letter_grade":  _letter_grade(total_score),
        "is_partial":    len(skipped) > 0,
        "fped_coverage": coverage,
        "summary":       summary,
    }


def score_district(
    meals:         list[dict],
    fped_results:  list[dict] = None,
    district_name: str        = "District",
    grade_band:    str        = NSLP_DEFAULT_BAND,
) -> dict:
    """Average nutrients and FPED food groups across meals, then score."""
    if not meals:
        raise ValueError("meals list is empty")

    fped_list = fped_results if fped_results else [None] * len(meals)

    all_keys = set()
    for m in meals:
        all_keys.update(m.keys())
    avg_nutrients = {
        k: round(sum(m.get(k, 0) or 0 for m in meals) / len(meals), 3)
        for k in all_keys
    }

    avg_fped = None
    if any(f is not None for f in fped_list):
        all_fg = set()
        for f in fped_list:
            if f:
                all_fg.update(f.get("hei_components", {}).keys())
        avg_hei = {
            k: round(
                sum((f or {}).get("hei_components", {}).get(k, 0) or 0
                    for f in fped_list) / len(fped_list), 4)
            for k in all_fg
        }
        avg_fped = {
            "hei_components": avg_hei,
            "coverage": {"found": "avg", "total": len(meals), "missing": []},
        }

    result = score_meal(avg_nutrients, avg_fped, district_name, grade_band)

    individual = [
        score_meal(m, f, grade_band=grade_band)["total_score"]
        for m, f in zip(meals, fped_list)
    ]
    result["n_meals"]     = len(meals)
    result["meal_scores"] = individual
    result["score_range"] = (min(individual), max(individual))
    return result


# ── Pretty print ──────────────────────────────────────────────────────────────

def explain_score(result: dict) -> None:
    w = 72
    print(f"\n{'═'*w}")
    print(f"  {'CAFETERIA CRITIC — HEI-2020 NUTRITION SCORE':^{w-4}}")
    print(f"{'═'*w}")
    print(f"  {result['meal_name']}")
    partial_note = "  [PARTIAL — download FPED for full score]" if result["is_partial"] else ""
    print(f"  Score: {result['total_score']}/100  ({result['letter_grade']}){partial_note}")

    if "n_meals" in result:
        lo, hi = result["score_range"]
        print(f"  Based on {result['n_meals']} meals  |  Range: {lo}–{hi}")

    cov = result.get("fped_coverage", {})
    if isinstance(cov.get("found"), int):
        pct = round(cov["found"] / max(cov["total"], 1) * 100)
        print(f"  FPED ingredient coverage: {cov['found']}/{cov['total']} ({pct}%)")

    fi = {"OK": "✅", "LOW": "⚠️ ", "HIGH": "⚠️ "}[result["calorie_flag"]]
    print(f"\n  {fi} Calories: {result['calories']:.0f} kcal  "
          f"(NSLP target {result['calorie_target']}, grades {result['grade_band']})")

    print(f"\n  {'─'*68}")
    print(f"  {'Component':<32} {'Density':>14}  {'Score':>8}  Bar")
    print(f"  {'─'*68}")

    at = mt = 0
    for lbl, c in result["components"].items():
        bar  = "█" * int(c["score"]/c["max_score"]*10) + "░" * (10-int(c["score"]/c["max_score"]*10))
        arr  = "↑" if c["type"] == "adequacy" else "↓"
        src  = "[F]" if c["source"] == "fped" else "[N]"
        dstr = f"{c['density']:.2f} {c['unit'].split('/')[0]}"
        sstr = f"{c['score']:.1f}/{c['max_score']}"
        print(f"  {arr}{src} {lbl:<30} {dstr:>14}  {sstr:>8}  {bar}")
        if c["type"] == "adequacy": at += c["score"]
        else:                       mt += c["score"]

    print(f"  {'─'*68}")
    max_mod = sum(v[0] for v in HEI_MODERATION.values())
    print(f"  {'Adequacy subtotal':<46}  {at:.1f}")
    print(f"  {'Moderation subtotal':<46}  {mt:.1f}")
    print(f"  {'TOTAL (normalized 0–100)':<46}  {result['total_score']:.1f}/100")

    if result["skipped"]:
        print(f"\n  ⚠️  Skipped (need FPED): {', '.join(result['skipped'])}")

    print(f"\n  {result['summary']}")
    print(f"{'═'*w}")
    print("  ↑[F]=adequacy/food-group  ↑[N]=adequacy/nutrient")
    print("  ↓[F]=moderation/food-group  ↓[N]=moderation/nutrient")
    print("  Benchmark: avg U.S. child diet ≈ 50/100 (USDA 2013)")
    print("  Standards: HEI-2020 — epi.grants.cancer.gov/hei/hei-2020-table1.html")
    print()


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    taco = {
        "Calories (kcal)": 415.1, "Protein (g)": 23.3,
        "Total Fat (g)": 23.6,    "Saturated Fat (g)": 8.9,
        "Cholesterol (mg)": 68.8, "Carbohydrates (g)": 26.5,
        "Dietary Fiber (g)": 2.0, "Total Sugars (g)": 0.0,
        "Sodium (mg)": 650.5,     "Calcium (mg)": 208.8,
        "Iron (mg)": 3.4,         "Potassium (mg)": 382.8,
        "Vitamin C (mg)": 3.3,    "Vitamin D (mcg)": 0.0,
    }

    print("── PARTIAL SCORE (nutrient-only, no FPED) ──")
    explain_score(score_meal(taco, meal_name="Soft Shell Taco — no FPED"))

    print("── FULL SCORE (with simulated FPED data) ──")
    mock_fped = {
        "hei_components": {
            "Total Fruits": 0.0,   "Whole Fruits": 0.0,
            "Total Vegetables": 0.09,  "Greens and Beans": 0.04,
            "Whole Grains": 0.0,       "Dairy": 0.21,
            "Total Protein Foods": 2.1, "Seafood and Plant Proteins": 0.0,
            "Refined Grains": 1.1,     "Added Sugars": 0.0,
        },
        "coverage": {"found": 7, "total": 7, "missing": []},
    }
    explain_score(score_meal(taco, mock_fped, meal_name="Soft Shell Taco — full HEI"))
