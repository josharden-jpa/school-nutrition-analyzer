# -*- coding: utf-8 -*-
"""
audit_reconstruction.py
========================
Measures how faithfully the AI decomposition reconstructs the chemistry of a
real school-lunch item -- by comparing it against that item's OWN published
Nutrislice nutrition label.

WHY THIS EXISTS (read before running)
-------------------------------------
HEI scores diet quality through food-group servings (cups of fruit, oz-eq of
whole grain, per 1,000 kcal). NO published school data contains food-group
servings -- they live only in the district's unpublished production records.
So Cafeteria Critic RECONSTRUCTS them: it asks Claude to decompose a menu name
into ingredients-with-grams, then runs those through USDA FoodData Central.
That reconstruction is an ESTIMATE of the recipe. A registered dietitian
scoring these menus by hand would also be estimating it -- there is no public
ground truth for food groups, for anyone.

So we CANNOT audit "did Claude get the food groups right." Nothing to check
against. What we CAN audit is the one thing that IS published: the Nutrition
Facts panel (kcal, sodium, sat fat, sugar, protein). For each item:

    published label  <--->  decomposition -> USDA -> summed nutrients

If the reconstruction's chemistry lands NEAR the real label, that is evidence
the recipe guess is in the right ballpark -- which means the food-group
estimates riding on that same recipe are trustworthy. If the chemistry is way
off, the food groups built on it are suspect. This converts the worry
"could the failing be the AI, not the food?" into a measured number with a bound.

WHAT IT REPORTS
---------------
Per item and in aggregate, the signed and absolute % gap between reconstruction
and published label for: Calories, Sodium, Saturated Fat, Total Sugars, Protein.
A small median absolute gap (say <25%) means faithful reconstruction. A large or
systematically-signed gap (e.g. reconstruction always HIGHER on calories) is
itself a finding -- it tells you the DIRECTION of the bias (e.g. the oil-adding
behavior inflating calories), which is exactly what you suspected.

This is NOT scoring. It does not compute HEI. It only diffs chemistry.

USAGE
-----
    import audit_reconstruction as audit

    # Audit the top N most-frequent items of one Fresno school
    audit.run(
        district_slug = "fresnounified",
        school_id     = 28172,          # Addams Elementary
        menu_type_id  = 3546,
        school_name   = "Addams Elementary",
        top_n         = 10,             # audit 10 most-frequent items
        full_year     = True,
    )

    # Quick test on just the current week (fast, no full-year scrape)
    audit.run("fresnounified", 28172, 3546, "Addams Elementary",
              top_n=10, full_year=False)

Saves a per-item CSV you can drop straight into a slide.
"""

from __future__ import annotations

import csv
import json
import os
import statistics
from collections import defaultdict
from datetime import date

import config
config.load_keys()

import nutrislice_scraper as ns       # the real-label scraper (ground truth)
import step2_csv                       # writes the ingredient CSV
import step3_usda                      # USDA lookup -> nutrient totals
import nutrislice_fped_bridge as bridge  # has decompose_item() + decomp cache


# The five published nutrients we can actually check against ground truth.
# These come straight off the Nutrition Facts panel, so they ARE measurable.
# (Everything HEI does with food groups is NOT here -- that's the whole point.)
AUDIT_NUTRIENTS = [
    "Calories (kcal)",
    "Sodium (mg)",
    "Saturated Fat (g)",
    "Total Sugars (g)",
    "Protein (g)",
]


# -----------------------------------------------------------------------------
# Step 1: collect the published label for each unique item
# -----------------------------------------------------------------------------

def collect_published_items(days: list[dict], top_n: int) -> list[dict]:
    """
    From scraped days, build the per-item published label for the top_n
    most-frequently-served items that actually have nutrition data.

    Returns list of:
        {"name": str, "frequency": int, "published": {nutrient_label: value}}

    The published values are the item's OWN rounded_nutrition_info, averaged
    across every appearance (they're usually identical each time, but averaging
    is harmless and handles the rare reformulation).
    """
    # accumulate published nutrition per item name
    sums   = defaultdict(lambda: defaultdict(float))
    counts = defaultdict(int)
    display_name = {}

    for day in days:
        for item in day.get("items", []):
            if not item.get("has_data"):
                continue
            name = item["name"].strip()
            key  = name.lower()
            nut  = item.get("nutrition") or {}
            # only count an appearance if it carried real nutrition
            counts[key] += 1
            display_name.setdefault(key, name)
            for label in AUDIT_NUTRIENTS:
                if label in nut and nut[label] is not None:
                    sums[key][label] += float(nut[label])

    # rank by frequency, take top_n
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:top_n]

    out = []
    for key, freq in ranked:
        published = {}
        for label in AUDIT_NUTRIENTS:
            total = sums[key].get(label, 0.0)
            published[label] = round(total / freq, 2) if freq else 0.0
        out.append({
            "name":      display_name[key],
            "frequency": freq,
            "published": published,
        })
    return out


# -----------------------------------------------------------------------------
# Step 2: reconstruct each item via the SAME decomposition path the scorer uses
# -----------------------------------------------------------------------------

def reconstruct_item(item_name: str, anthropic_key: str, usda_key: str) -> dict:
    """
    Run one item name through the exact reconstruction path the HEI scorer uses:
        name -> Claude decomposition (ingredients + grams)
             -> CSV -> USDA FoodData Central
             -> summed nutrient totals for the meal.

    Returns {nutrient_label: reconstructed_value} restricted to AUDIT_NUTRIENTS,
    plus a "_ingredients" key listing what the decomposition produced (so a bad
    chemistry match can be traced to the ingredient that caused it).

    Uses bridge.decompose_item so the decomposition cache is shared with real
    runs -- the reconstruction here is byte-identical to what the score used.
    """
    ingredients = bridge.decompose_item(item_name, anthropic_key)
    if not ingredients:
        return {"_error": "decomposition returned nothing", "_ingredients": []}

    # Write the ingredient list to a temp CSV in the format step3 expects
    tmp_csv = "_audit_tmp_ingredients.csv"
    with open(tmp_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ingredient_name", "grams"])
        w.writeheader()
        for ing in ingredients:
            w.writerow({
                "ingredient_name": ing.get("name", "").strip(),
                "grams":           ing.get("grams", 0),
            })

    try:
        usda = step3_usda.nutrients_from_csv(tmp_csv, api_key=usda_key)
        totals = usda.get("totals", {})
    finally:
        if os.path.exists(tmp_csv):
            os.remove(tmp_csv)

    recon = {label: round(totals.get(label, 0.0), 2) for label in AUDIT_NUTRIENTS}
    recon["_ingredients"] = [
        f"{i.get('name','?')} ({i.get('grams','?')}g)" for i in ingredients
    ]
    return recon


# -----------------------------------------------------------------------------
# Step 3: diff reconstruction vs published, per nutrient
# -----------------------------------------------------------------------------

def _pct_gap(recon: float, published: float) -> float | None:
    """
    Signed % gap of reconstruction relative to the published label.
    Positive = reconstruction is HIGHER than the real label (over-estimate).
    Negative = reconstruction is LOWER (under-estimate).
    Returns None when the published value is 0 (can't take a ratio).
    """
    if published is None or published == 0:
        return None
    return round((recon - published) / published * 100.0, 1)


def audit_one_item(item: dict, anthropic_key: str, usda_key: str) -> dict:
    """Reconstruct one item and compute per-nutrient gaps vs its published label."""
    name      = item["name"]
    published = item["published"]
    recon     = reconstruct_item(name, anthropic_key, usda_key)

    row = {
        "item":        name,
        "frequency":   item["frequency"],
        "ingredients": " + ".join(recon.get("_ingredients", []))[:300],
    }

    for label in AUDIT_NUTRIENTS:
        pub = published.get(label, 0.0)
        rec = recon.get(label, 0.0) if "_error" not in recon else 0.0
        gap = _pct_gap(rec, pub)
        short = label.split(" (")[0]            # "Calories", "Sodium", ...
        row[f"{short}_label"]  = pub
        row[f"{short}_recon"]  = rec
        row[f"{short}_gap_pct"] = gap if gap is not None else ""

    row["_error"] = recon.get("_error", "")
    return row


# -----------------------------------------------------------------------------
# Step 4: aggregate + report
# -----------------------------------------------------------------------------

def summarize(rows: list[dict]) -> dict:
    """
    Aggregate the per-item gaps into median signed and median absolute gaps
    per nutrient. Median (not mean) so one wild mismatch doesn't dominate.

    The two numbers say different things:
      - median ABSOLUTE gap  = how close the reconstruction lands (precision)
      - median SIGNED gap     = which DIRECTION it's biased (e.g. +calories
                                from oil-adding, -protein from undercounting)
    """
    summary = {}
    for label in AUDIT_NUTRIENTS:
        short = label.split(" (")[0]
        signed = [r[f"{short}_gap_pct"] for r in rows
                  if isinstance(r.get(f"{short}_gap_pct"), (int, float))]
        if not signed:
            summary[short] = {"n": 0}
            continue
        summary[short] = {
            "n":               len(signed),
            "median_signed":   round(statistics.median(signed), 1),
            "median_absolute": round(statistics.median([abs(x) for x in signed]), 1),
            "worst_over":      round(max(signed), 1),
            "worst_under":     round(min(signed), 1),
        }
    return summary


def print_report(rows: list[dict], summary: dict, label: str) -> None:
    print(f"\n{'='*72}")
    print(f"  RECONSTRUCTION-vs-PUBLISHED-LABEL AUDIT")
    print(f"  {label}")
    print(f"  Items audited: {len(rows)}")
    print(f"{'='*72}")
    print("  Checking only PUBLISHED nutrients (Nutrition Facts panel).")
    print("  Food groups are NOT here -- they are unpublished by design,")
    print("  so the AI estimate of them cannot be checked against any source.")
    print("  This measures: does the reconstructed chemistry match the real label?")

    # Per-item table (compact -- just the calorie + sodium gap, the two that matter most)
    print(f"\n  {'Item':<34} {'freq':>4} {'kcal gap':>9} {'Na gap':>8} {'SatFat gap':>11}")
    print(f"  {'-'*34} {'-'*4} {'-'*9} {'-'*8} {'-'*11}")
    for r in rows:
        kcal = r.get("Calories_gap_pct", "")
        na   = r.get("Sodium_gap_pct", "")
        sf   = r.get("Saturated Fat_gap_pct", r.get("Saturated_gap_pct", ""))
        # label key is "Saturated Fat" -> short already handled below
        sf   = r.get("Saturated Fat_gap_pct", "")
        kcal_s = f"{kcal:+.0f}%" if isinstance(kcal, (int, float)) else "  n/a"
        na_s   = f"{na:+.0f}%"   if isinstance(na, (int, float))   else "  n/a"
        sf_s   = f"{sf:+.0f}%"   if isinstance(sf, (int, float))   else "  n/a"
        print(f"  {r['item'][:34]:<34} {r['frequency']:>4} "
              f"{kcal_s:>9} {na_s:>8} {sf_s:>11}")

    print(f"\n  {'-'*72}")
    print(f"  AGGREGATE (median across items)")
    print(f"  {'Nutrient':<16} {'n':>3} {'median |gap|':>13} {'median signed':>15}  bias")
    print(f"  {'-'*16} {'-'*3} {'-'*13} {'-'*15}  {'-'*20}")
    for short, s in summary.items():
        if s.get("n", 0) == 0:
            print(f"  {short:<16} {0:>3}   (no published values to compare)")
            continue
        signed = s["median_signed"]
        if   signed > 10:  bias = "recon OVER-estimates"
        elif signed < -10: bias = "recon UNDER-estimates"
        else:              bias = "~unbiased"
        print(f"  {short:<16} {s['n']:>3} {s['median_absolute']:>12.1f}% "
              f"{signed:>+14.1f}%  {bias}")

    print(f"\n  {'-'*72}")
    print("  HOW TO READ THIS:")
    print("   - median |gap| small (<~25%) => reconstruction is faithful;")
    print("       trust the food-group estimates riding on the same recipe.")
    print("   - a large SIGNED bias on Calories (positive) is the oil-adding")
    print("       behavior showing up; on Protein (negative) is undercounting.")
    print("   - Sodium/SatFat/Sugars track the published panel closely if the")
    print("       reconstruction picked the right base foods.")
    print(f"{'='*72}\n")


# -----------------------------------------------------------------------------
# Orchestrator
# -----------------------------------------------------------------------------

def run(
    district_slug: str,
    school_id:     int,
    menu_type_id:  int,
    school_name:   str = "",
    top_n:         int = 10,
    full_year:     bool = True,
    save_csv:      bool = True,
) -> dict:
    """
    Full audit: scrape real labels -> pick top_n items -> reconstruct each ->
    diff vs published -> aggregate -> print + save CSV.

    Returns {"rows": [...], "summary": {...}, "csv_path": str}.
    """
    # Anthropic key comes from anthropicapikey.txt (same path the bridge/step1 use).
    # USDA key is loaded into config by config.load_keys() at import time.
    from step1_recipe import load_anthropic_key
    anthropic_key = load_anthropic_key()
    usda_key      = config.USDA_API_KEY or config.load_keys()

    label = f"{school_name or school_id} ({district_slug})"
    print(f"\n  [audit] Scraping real labels for {label} ...")

    if full_year:
        days = ns.scrape_school_year(
            district_slug, school_id, menu_type_id, school_name)
    else:
        data = ns.get_week_data(district_slug, school_id, menu_type_id, date.today())
        days = ns.parse_week(data) if data else []

    if not days:
        print("  [audit] No data scraped -- check slug/ids.")
        return {}

    items = collect_published_items(days, top_n)
    print(f"  [audit] Auditing top {len(items)} items by frequency.\n")

    rows = []
    for i, item in enumerate(items, 1):
        print(f"  [audit] ({i}/{len(items)}) reconstructing: {item['name']}")
        rows.append(audit_one_item(item, anthropic_key, usda_key))

    summary = summarize(rows)
    print_report(rows, summary, label)

    csv_path = ""
    if save_csv:
        safe = "".join(c if c.isalnum() else "_"
                       for c in (school_name or str(school_id))).strip("_")[:50]
        csv_path = f"audit_reconstruction_{safe}.csv"
        # build a flat fieldname list
        base = ["item", "frequency"]
        per  = []
        for label_n in AUDIT_NUTRIENTS:
            short = label_n.split(" (")[0]
            per += [f"{short}_label", f"{short}_recon", f"{short}_gap_pct"]
        fields = base + per + ["ingredients", "_error"]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"  [audit] Per-item CSV saved -> {csv_path}")

    return {"rows": rows, "summary": summary, "csv_path": csv_path}


if __name__ == "__main__":
    # Default demo: Addams Elementary, current week only (fast).
    # Switch full_year=True for the real audit.
    run(
        district_slug = "fresnounified",
        school_id     = 28172,
        menu_type_id  = 3546,
        school_name   = "Addams Elementary",
        top_n         = 10,
        full_year     = False,
    )
