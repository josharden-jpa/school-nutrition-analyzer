# -*- coding: utf-8 -*-
"""
main.py
Orchestrates the full School Nutrition Analysis pipeline:

  1. Get meal name + context from user
  2. Claude estimates the recipe          (step1_recipe)
  3. Recipe → ingredients CSV             (step2_csv)
  4. CSV → USDA nutrient totals           (step3_usda)
  5. Nutrient totals → DV% charts        (step4_charts)
  6. (Optional) Plant-based substitute   (step5_substitute)
  7. Assemble PDF report                  (step6_report)

Usage:
    python main.py
"""

import os
import re
import sys
import config

# Load API key before importing any step modules
config.load_keys()

import step1_recipe
import step2_csv
import step3_usda
import step4_charts
import step5_substitute
import step6_report


def banner(text: str) -> None:
    width = 64
    print(f"\n{'─'*width}")
    print(f"  {text}")
    print(f"{'─'*width}")


def main():
    print("\n" + "="*64)
    print("  🍎  School Nutrition Analyzer")
    print("="*64)

    # ── Step 1: Get meal info from user ───────────────────────────────────────
    banner("STEP 1 — Meal Information")
    meal_name  = input("  Enter the meal name (e.g. 'Cheese Pizza'): ").strip()
    if not meal_name:
        print("No meal name provided. Exiting.")
        sys.exit(1)
    extra_info = input("  Any extra info from the district website? (press Enter to skip): ").strip()

    # ── Step 1: Claude estimates recipe ──────────────────────────────────────
    banner("STEP 1b — Claude Estimates Recipe")
    print("  Asking Claude for a recipe estimate …")
    recipe = step1_recipe.get_recipe(meal_name, extra_info)
    step1_recipe.print_recipe(recipe)

    confirm = input("  Does this recipe look reasonable? (y to continue, n to exit): ").strip().lower()
    if confirm != "y":
        print("  Exiting — please re-run and adjust the meal name or extra info.")
        sys.exit(0)

    # ── Step 2: Recipe → CSV ──────────────────────────────────────────────────
    banner("STEP 2 — Saving Ingredients CSV")
    csv_path = step2_csv.recipe_to_csv(recipe)

    # ── Step 3: USDA nutrient lookup ──────────────────────────────────────────
    banner("STEP 3 — USDA Nutrient Lookup")
    usda_result_orig = step3_usda.nutrients_from_csv(csv_path)
    totals_orig      = usda_result_orig["totals"]

    # If the user provided district calories, run a consistency check
    calorie_match = re.search(r'(\d{3,4})\s*(?:cal|kcal|calories)', extra_info, re.IGNORECASE)
    if calorie_match:
        expected_kcal = float(calorie_match.group(1))
        step3_usda.check_calorie_consistency(totals_orig.get("Calories (kcal)", 0), expected_kcal)

    # ── Step 4: Charts for original meal ─────────────────────────────────────
    banner("STEP 4 — Generating Charts (Original Meal)")
    safe_name      = meal_name.lower().replace(" ", "_").replace("/", "-")
    chart_dv_path  = step4_charts.make_dv_chart(meal_name, totals_orig)
    chart_pie_path = step4_charts.make_macro_pie(meal_name, totals_orig)

    # ── Step 5: Plant-based substitute (optional) ─────────────────────────────
    banner("STEP 5 — Plant-Based Substitute (Optional)")
    do_sub = input("  Add a plant-based substitute comparison? (y/n): ").strip().lower()

    totals_sub        = None
    sub_recipe        = None
    chart_dv_compare  = None

    if do_sub == "y":
        sub_recipe = step5_substitute.get_substitute_recipe(recipe, interactive=True)

        if sub_recipe:
            sub_name     = sub_recipe["meal_name"]
            sub_csv_path = step2_csv.recipe_to_csv(sub_recipe)

            banner("STEP 5b — USDA Lookup for Substitute")
            usda_result_sub = step3_usda.nutrients_from_csv(sub_csv_path)
            totals_sub      = usda_result_sub["totals"]

            # Save a substitute-only DV chart (for the report's second page)
            banner("STEP 5c — Generating Comparison Charts")
            chart_dv_compare = step4_charts.make_dv_chart(
                meal_name,
                totals_orig,
                totals_substitute=totals_sub,
                sub_meal_name=sub_name,
                output_path=f"{safe_name}_vs_substitute_dv_chart.png",
            )
        else:
            print("  Skipping plant-based comparison.")

    # ── Step 6: Assemble PDF report ───────────────────────────────────────────
    banner("STEP 6 — Building PDF Report")
    output_pdf = step6_report.build_report(
        meal_name             = meal_name,
        totals_orig           = totals_orig,
        chart_dv_path         = chart_dv_path,
        chart_pie_path        = chart_pie_path,
        totals_sub            = totals_sub,
        sub_meal_name         = sub_recipe["meal_name"] if sub_recipe else "Plant-Based Alternative",
        chart_dv_compare_path = chart_dv_compare,
        output_path           = f"{safe_name}_nutrition_report.pdf",
    )

    print(f"\n{'='*64}")
    print(f"  ✅  Done!  Report saved: {output_pdf}")
    print(f"{'='*64}\n")


if __name__ == "__main__":
    main()
