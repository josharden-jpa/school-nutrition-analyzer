# -*- coding: utf-8 -*-
"""
district_menu.py
Batch-processes a full week (or more) of school lunch menu items for one
district, runs each through the full nutrition pipeline, and outputs:
  - Per-meal nutrient totals and HEI scores
  - District-level averaged HEI score
  - A summary CSV and JSON for downstream use (map visualization, etc.)

This is the module that takes Cafeteria Critic from a single-meal analyzer
to a district comparison tool.

HOW IT WORKS
------------
1. You provide a list of menu items for a district -- meal names and optional
   sides, exactly like you'd enter at the main.py prompt.
2. Each item runs through step1 (Claude recipe estimation), step3 (USDA
   nutrients), and step3b (FPED food groups).
3. Nutrient totals and food group equivalents are averaged across all meals.
4. score_district() produces one HEI score representing the district's
   typical lunch quality.
5. Results are saved to JSON and CSV for the map layer.

DATA COLLECTION NOTE
--------------------
This module accepts menu data as a Python list -- you populate it manually
or via a future scraping layer. The structure is simple enough that a
volunteer with a district's PDF menu can enter it in 10 minutes.

Usage
-----
    # Run interactively:
    python district_menu.py

    # Or import and call programmatically:
    from district_menu import analyze_district
    result = analyze_district(
        district_name = "Springfield USD",
        grade_band    = "6-8",
        menu_items    = [
            {"meal": "Cheese Pizza",      "sides": ["apple", "milk"]},
            {"meal": "Soft Shell Taco",   "sides": ["corn and bean salsa"]},
            {"meal": "Chicken Nuggets",   "sides": ["green beans", "milk"]},
            {"meal": "Hamburger",         "sides": ["french fries", "milk"]},
            {"meal": "Mac and Cheese",    "sides": ["peach cup", "milk"]},
        ],
    )
"""

import json
import os
import csv
import time
import config

config.load_keys()

import step1_recipe
import step2_csv
import step3_usda
import step3b_fped
import score_district as scorer


# -----------------------------------------------------------------------------
# Core analysis function
# -----------------------------------------------------------------------------

def analyze_district(
    district_name: str,
    menu_items:    list[dict],
    grade_band:    str  = "6-8",
    fped_path:     str  = "FPED_1718.xlsx",
    output_dir:    str  = ".",
    save_outputs:  bool = True,
) -> dict:
    """
    Run the full nutrition pipeline for a district's weekly menu.

    Parameters
    ----------
    district_name : display name (e.g. "Springfield USD")
    menu_items    : list of dicts, each with:
                      "meal"  : str  -- main dish name
                      "sides" : list[str]  -- side dish names (optional)
    grade_band    : "K-5", "6-8", or "9-12"
    fped_path     : path to FPED_1718.xlsx
    output_dir    : where to save JSON/CSV results
    save_outputs  : if False, skip file writing (useful for programmatic use)

    Returns
    -------
    {
      "district_name":  str,
      "grade_band":     str,
      "n_meals":        int,
      "district_score": dict,   # full score_district() output
      "meal_results":   list,   # one entry per menu item
      "summary_csv":    str,    # path to summary CSV (if save_outputs)
      "summary_json":   str,    # path to summary JSON (if save_outputs)
    }
    """
    print(f"\n{'='*64}")
    print(f"  Cafeteria Critic -- District Analysis")
    print(f"  District : {district_name}")
    print(f"  Grade band: {grade_band}")
    print(f"  Menu items: {len(menu_items)}")
    print(f"{'='*64}\n")

    # Load FPED once for the whole run
    fped = None
    if os.path.exists(fped_path):
        try:
            fped = step3b_fped.FPEDLookup(fped_path)
        except Exception as e:
            print(f"[district] FPED load failed: {e} -- food group scoring disabled")
    else:
        print(f"[district] FPED file not found at '{fped_path}' -- nutrient-only scoring")

    meal_results     = []
    all_nutrient_totals = []
    all_fped_results    = []

    for i, item in enumerate(menu_items):
        meal_name  = item["meal"]
        side_names = item.get("sides", [])

        print(f"\n{'─'*64}")
        print(f"  MEAL {i+1}/{len(menu_items)}: {meal_name}"
              + (f" + {side_names}" if side_names else ""))
        print(f"{'─'*64}")

        meal_result = _process_meal(
            meal_name  = meal_name,
            side_names = side_names,
            fped       = fped,
            grade_band = grade_band,
        )

        if meal_result is None:
            print(f"  [district] Skipping '{meal_name}' -- pipeline failed")
            continue

        meal_results.append(meal_result)
        all_nutrient_totals.append(meal_result["nutrient_totals"])
        if meal_result["fped_result"] is not None:
            all_fped_results.append(meal_result["fped_result"])

        # Brief pause between meals to be polite to APIs
        time.sleep(1.0)

    if not meal_results:
        print("\n[district] No meals processed successfully.")
        return {}

    # Score the district
    print(f"\n{'='*64}")
    print(f"  DISTRICT SCORE: {district_name}")
    print(f"{'='*64}")

    fped_list = all_fped_results if all_fped_results else None
    if fped_list and len(fped_list) != len(all_nutrient_totals):
        # Some meals missing FPED -- pad with None
        fped_list = None

    district_score = scorer.score_district(
        meals         = all_nutrient_totals,
        fped_results  = fped_list,
        district_name = district_name,
        grade_band    = grade_band,
    )
    scorer.explain_score(district_score)

    # Add individual meal scores to results
    for j, meal_result in enumerate(meal_results):
        meal_result["hei_score"] = scorer.score_meal(
            nutrient_totals = meal_result["nutrient_totals"],
            fped_result     = meal_result["fped_result"],
            meal_name       = meal_result["meal_name"],
            grade_band      = grade_band,
        )

    result = {
        "district_name":  district_name,
        "grade_band":     grade_band,
        "n_meals":        len(meal_results),
        "district_score": district_score,
        "meal_results":   meal_results,
    }

    if save_outputs:
        safe_name = district_name.lower().replace(" ", "_").replace("/", "-")
        json_path = os.path.join(output_dir, f"{safe_name}_analysis.json")
        csv_path  = os.path.join(output_dir, f"{safe_name}_summary.csv")

        _save_json(result, json_path)
        _save_csv(result, csv_path)

        result["summary_json"] = json_path
        result["summary_csv"]  = csv_path

        print(f"\n[district] Results saved:")
        print(f"  JSON: {json_path}")
        print(f"  CSV:  {csv_path}")

    if fped:
        fped.save()

    return result


# -----------------------------------------------------------------------------
# Per-meal pipeline
# -----------------------------------------------------------------------------

def _process_meal(
    meal_name:  str,
    side_names: list[str],
    fped:       step3b_fped.FPEDLookup | None,
    grade_band: str,
) -> dict | None:
    """
    Run steps 1-3b for one meal + sides. Returns a result dict or None on failure.
    """
    try:
        # Step 1: Claude estimates recipe
        print(f"  [1] Estimating recipe for '{meal_name}' ...")
        recipe = step1_recipe.get_recipe(meal_name, "")

        # Step 2: Recipe -> CSV
        csv_path = step2_csv.recipe_to_csv(recipe)

        # Step 3: USDA nutrient lookup
        print(f"  [3] USDA nutrient lookup ...")
        usda_result = step3_usda.nutrients_from_csv(csv_path)
        nutrient_totals = usda_result["totals"]
        ingredients     = usda_result["ingredients"]

        # Process sides
        tray_items = [{"meal_name": meal_name, "ingredients": ingredients}]

        for side_name in side_names:
            try:
                print(f"  [3] Side: '{side_name}' ...")
                side_recipe = step1_recipe.get_recipe(side_name, "")
                side_csv    = step2_csv.recipe_to_csv(side_recipe)
                side_usda   = step3_usda.nutrients_from_csv(side_csv)

                # Add side nutrients to meal totals
                for k, v in side_usda["totals"].items():
                    nutrient_totals[k] = nutrient_totals.get(k, 0) + (v or 0)

                tray_items.append({
                    "meal_name":   side_name,
                    "ingredients": side_usda["ingredients"],
                })
            except Exception as e:
                print(f"  [district] Side '{side_name}' failed: {e} -- skipping")

        # Step 3b: FPED food group lookup
        fped_result = None
        if fped is not None:
            try:
                if len(tray_items) > 1:
                    fped_result = fped.lookup_tray(tray_items)
                else:
                    fped_result = fped.lookup_meal(ingredients)
            except Exception as e:
                print(f"  [district] FPED lookup failed: {e} -- skipping food groups")

        kcal = nutrient_totals.get("Calories (kcal)", 0)
        print(f"  -> {kcal:.0f} kcal | "
              f"Protein {nutrient_totals.get('Protein (g)', 0):.1f}g | "
              f"Fat {nutrient_totals.get('Total Fat (g)', 0):.1f}g | "
              f"Carbs {nutrient_totals.get('Carbohydrates (g)', 0):.1f}g")

        return {
            "meal_name":       meal_name + (f" + {side_names}" if side_names else ""),
            "sides":           side_names,
            "nutrient_totals": nutrient_totals,
            "fped_result":     fped_result,
            "hei_score":       None,   # filled in after district scoring
        }

    except Exception as e:
        print(f"  [district] Pipeline failed for '{meal_name}': {e}")
        return None


# -----------------------------------------------------------------------------
# Output helpers
# -----------------------------------------------------------------------------

def _save_json(result: dict, path: str) -> None:
    """Save full analysis result to JSON, handling non-serializable objects."""
    def _clean(obj):
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()
                    if k not in ("fped_result",)}  # skip large FPED dicts
        if isinstance(obj, list):
            return [_clean(v) for v in obj]
        if isinstance(obj, (int, float, str, bool, type(None))):
            return obj
        return str(obj)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(_clean(result), f, indent=2, ensure_ascii=False)


def _save_csv(result: dict, path: str) -> None:
    """Save per-meal summary to CSV for map visualization."""
    rows = []
    district_score = result["district_score"]

    for meal_result in result["meal_results"]:
        score = meal_result.get("hei_score") or {}
        nt    = meal_result.get("nutrient_totals", {})
        rows.append({
            "district":      result["district_name"],
            "grade_band":    result["grade_band"],
            "meal":          meal_result["meal_name"],
            "calories":      round(nt.get("Calories (kcal)", 0), 1),
            "protein_g":     round(nt.get("Protein (g)", 0), 1),
            "fat_g":         round(nt.get("Total Fat (g)", 0), 1),
            "sat_fat_g":     round(nt.get("Saturated Fat (g)", 0), 1),
            "sodium_mg":     round(nt.get("Sodium (mg)", 0), 1),
            "carbs_g":       round(nt.get("Carbohydrates (g)", 0), 1),
            "fiber_g":       round(nt.get("Dietary Fiber (g)", 0), 1),
            "hei_score":     score.get("total_score", ""),
            "hei_grade":     score.get("letter_grade", ""),
            "calorie_flag":  score.get("calorie_flag", ""),
            "is_partial":    score.get("is_partial", ""),
        })

    # Add district summary row
    rows.append({
        "district":      result["district_name"],
        "grade_band":    result["grade_band"],
        "meal":          "DISTRICT AVERAGE",
        "calories":      "",
        "protein_g":     "",
        "fat_g":         "",
        "sat_fat_g":     "",
        "sodium_mg":     "",
        "carbs_g":       "",
        "fiber_g":       "",
        "hei_score":     district_score.get("total_score", ""),
        "hei_grade":     district_score.get("letter_grade", ""),
        "calorie_flag":  "",
        "is_partial":    district_score.get("is_partial", ""),
    })

    if not rows:
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


# -----------------------------------------------------------------------------
# Interactive mode
# -----------------------------------------------------------------------------

def _run_interactive() -> None:
    """Collect district info and menu items interactively from the user."""
    print("\n" + "="*64)
    print("  Cafeteria Critic -- District Menu Analyzer")
    print("="*64)

    district_name = input("\n  District name (e.g. 'Springfield USD'): ").strip()
    if not district_name:
        print("No district name provided. Exiting.")
        return

    grade_options = {"1": "K-5", "2": "6-8", "3": "9-12"}
    print("\n  Grade band:")
    print("    [1] K-5   (550-650 kcal target)")
    print("    [2] 6-8   (600-700 kcal target)")
    print("    [3] 9-12  (750-850 kcal target)")
    gb_choice  = input("  Choice [1/2/3, default 2]: ").strip()
    grade_band = grade_options.get(gb_choice, "6-8")

    print(f"\n  Enter menu items one at a time.")
    print(f"  For each meal, enter the main dish name.")
    print(f"  Then enter any sides/drinks (comma-separated, or press Enter to skip).")
    print(f"  Press Enter with no meal name when done.\n")

    menu_items = []
    while True:
        meal = input(f"  Meal {len(menu_items)+1} name (or Enter to finish): ").strip()
        if not meal:
            break
        sides_raw = input(f"  Sides for '{meal}' (comma-separated, or Enter to skip): ").strip()
        sides = [s.strip() for s in sides_raw.split(",") if s.strip()] \
                if sides_raw else []
        menu_items.append({"meal": meal, "sides": sides})

    if not menu_items:
        print("No menu items entered. Exiting.")
        return

    print(f"\n  Ready to analyze {len(menu_items)} meals for {district_name}.")
    confirm = input("  Proceed? (y/n): ").strip().lower()
    if confirm != "y":
        return

    analyze_district(
        district_name = district_name,
        menu_items    = menu_items,
        grade_band    = grade_band,
    )


# -----------------------------------------------------------------------------
# Quick test with known data
# -----------------------------------------------------------------------------

SAMPLE_MENU = [
    {"meal": "Soft Shell Taco",   "sides": ["corn and bean salsa"]},
    {"meal": "Cheese Pizza",      "sides": ["apple", "milk, 1%"]},
    {"meal": "Chicken Nuggets",   "sides": ["mashed potatoes", "milk, 1%"]},
    {"meal": "Hamburger",         "sides": ["french fries", "milk, 1%"]},
    {"meal": "Mac and Cheese",    "sides": ["green beans", "milk, 1%"]},
]


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--sample":
        # Run with sample menu for testing
        analyze_district(
            district_name = "Sample District USD",
            menu_items    = SAMPLE_MENU,
            grade_band    = "6-8",
        )
    else:
        _run_interactive()
