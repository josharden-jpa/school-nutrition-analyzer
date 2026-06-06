# -*- coding: utf-8 -*-
"""
main.py
Orchestrates the full School Nutrition Analysis pipeline:

  1. Get meal name + context from user
  2. Claude estimates the recipe          (step1_recipe)
  3. Recipe -> ingredients CSV            (step2_csv)
  4. CSV -> USDA nutrient totals          (step3_usda)
  5. Nutrient totals -> DV% charts       (step4_charts)
  6. (Optional) Plant-based substitute   (step5_substitute)
  7. Assemble PDF report                  (step6_report)
  8. FPED food group lookup               (step3b_fped)
  9. HEI-2020 scoring                     (score_district)

Usage:
    python main.py
"""

import os
import re
import sys
import config

# Load API keys before importing any step modules
config.load_keys()

import step1_recipe
import step2_csv
import step3_usda
import step4_charts
import step5_substitute
import step6_report
import step3b_fped
import score_district as scorer


def banner(text: str) -> None:
    width = 64
    print(f"\n{'─'*width}")
    print(f"  {text}")
    print(f"{'─'*width}")


def main():
    print("\n" + "="*64)
    print("  🍎  Cafeteria Critic — School Nutrition Analyzer")
    print("="*64)

    # ── Step 1: Get meal info from user ───────────────────────────────────────
    banner("STEP 1 — Meal Information")
    meal_name  = input("  Enter the meal name (e.g. 'Cheese Pizza'): ").strip()
    if not meal_name:
        print("No meal name provided. Exiting.")
        sys.exit(1)
    extra_info = input("  Any extra info from the district website? (press Enter to skip): ").strip()

    # Ask upfront if there are side dishes so we can score the full tray
    print("\n  School lunches often include sides (fruit, milk, salsa, etc.).")
    sides_input = input(
        "  List any sides/drinks (comma-separated), or press Enter to skip: "
    ).strip()
    side_names = [s.strip() for s in sides_input.split(",") if s.strip()] \
                 if sides_input else []

    # ── Step 1b: Claude estimates recipe ─────────────────────────────────────
    banner("STEP 1b — Claude Estimates Recipe")
    print("  Asking Claude for a recipe estimate ...")
    recipe = step1_recipe.get_recipe(meal_name, extra_info)
    step1_recipe.print_recipe(recipe)

    confirm = input("  Does this recipe look reasonable? (y to continue, n to exit): ").strip().lower()
    if confirm != "y":
        print("  Exiting -- please re-run and adjust the meal name or extra info.")
        sys.exit(0)

    # ── Step 2: Recipe -> CSV ─────────────────────────────────────────────────
    banner("STEP 2 — Saving Ingredients CSV")
    csv_path  = step2_csv.recipe_to_csv(recipe)
    safe_name = meal_name.lower().replace(" ", "_").replace("/", "-")

    # ── Step 3: USDA nutrient lookup ──────────────────────────────────────────
    banner("STEP 3 — USDA Nutrient Lookup")
    usda_result_orig = step3_usda.nutrients_from_csv(csv_path)
    totals_orig      = usda_result_orig["totals"]

    calorie_match = re.search(r'(\d{3,4})\s*(?:cal|kcal|calories)', extra_info, re.IGNORECASE)
    if calorie_match:
        expected_kcal = float(calorie_match.group(1))
        step3_usda.check_calorie_consistency(
            totals_orig.get("Calories (kcal)", 0), expected_kcal)

    # ── Step 3b: FPED food group lookup ──────────────────────────────────────
    banner("STEP 3b — Food Group Lookup (FPED)")
    fped_result_orig = None
    fped             = None

    try:
        fped = step3b_fped.FPEDLookup("FPED_1718.xlsx")

        # Build tray: main dish + any sides the user listed
        tray_items = [
            {"meal_name": meal_name, "ingredients": usda_result_orig["ingredients"]}
        ]

        if side_names:
            print(f"\n  Getting side dish recipes: {side_names}")
            for side in side_names:
                print(f"\n  Estimating recipe for side: '{side}' ...")
                side_recipe     = step1_recipe.get_recipe(side, "")
                side_csv        = step2_csv.recipe_to_csv(side_recipe)
                side_usda       = step3_usda.nutrients_from_csv(side_csv)

                # Add side nutrients to original totals for combined score
                for k, v in side_usda["totals"].items():
                    totals_orig[k] = totals_orig.get(k, 0) + (v or 0)

                tray_items.append({
                    "meal_name":   side,
                    "ingredients": side_usda["ingredients"],
                })

        if len(tray_items) > 1:
            fped_result_orig = fped.lookup_tray(tray_items)
        else:
            fped_result_orig = fped.lookup_meal(usda_result_orig["ingredients"])

        fped.explain_food_groups(
            fped_result_orig,
            meal_name + (f" + {len(side_names)} sides" if side_names else ""))

    except FileNotFoundError:
        print("  FPED file not found -- skipping food group lookup.")
        print("  Download FPED_1718.xlsx and place it in your project directory")
        print("  for full HEI-2020 scoring.")
    except Exception as e:
        print(f"  FPED lookup failed: {e}")
        print("  Continuing without food group data.")

    # ── Step 4: Charts for original meal ─────────────────────────────────────
    banner("STEP 4 — Generating Charts (Original Meal)")
    chart_dv_path  = step4_charts.make_dv_chart(meal_name, totals_orig)
    chart_pie_path = step4_charts.make_macro_pie(meal_name, totals_orig)

    # ── Step 5: Plant-based substitute (optional) ─────────────────────────────
    banner("STEP 5 — Plant-Based Substitute (Optional)")
    do_sub = input("  Add a plant-based substitute comparison? (y/n): ").strip().lower()

    totals_sub       = None
    sub_recipe       = None
    chart_dv_compare = None
    fped_result_sub  = None

    if do_sub == "y":
        sub_recipe = step5_substitute.get_substitute_recipe(recipe, interactive=True)

        if sub_recipe:
            sub_name     = sub_recipe["meal_name"]
            sub_csv_path = step2_csv.recipe_to_csv(sub_recipe)

            banner("STEP 5b — USDA Lookup for Substitute")
            usda_result_sub = step3_usda.nutrients_from_csv(sub_csv_path)
            totals_sub      = usda_result_sub["totals"]

            banner("STEP 5c — Generating Comparison Charts")
            chart_dv_compare = step4_charts.make_dv_chart(
                meal_name,
                totals_orig,
                totals_substitute=totals_sub,
                sub_meal_name=sub_name,
                output_path=f"{safe_name}_vs_substitute_dv_chart.png",
            )

            # FPED lookup for substitute
            if fped is not None:
                try:
                    fped_result_sub = fped.lookup_meal(usda_result_sub["ingredients"])
                except Exception as e:
                    print(f"  FPED lookup for substitute failed: {e}")
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

    # ── Step 9: HEI-2020 scoring ──────────────────────────────────────────────
    banner("STEP 9 — HEI-2020 Nutrition Score")

    score_orig = scorer.score_meal(
        nutrient_totals = totals_orig,
        fped_result     = fped_result_orig,
        meal_name       = meal_name + (f" + sides" if side_names else ""),
        grade_band      = "6-8",
    )
    scorer.explain_score(score_orig)

    if totals_sub is not None:
        score_sub = scorer.score_meal(
            nutrient_totals = totals_sub,
            fped_result     = fped_result_sub,
            meal_name       = sub_recipe["meal_name"],
            grade_band      = "6-8",
        )
        scorer.explain_score(score_sub)

        print(f"\n  Score comparison:")
        print(f"    Original:    {score_orig['total_score']}/100  "
              f"({score_orig['letter_grade']})")
        print(f"    Plant-based: {score_sub['total_score']}/100  "
              f"({score_sub['letter_grade']})")
        delta = round(score_sub["total_score"] - score_orig["total_score"], 1)
        direction = "+" if delta >= 0 else ""
        print(f"    Difference:  {direction}{delta} points")

    # ── Done ──────────────────────────────────────────────────────────────────
    print(f"\n{'='*64}")
    print(f"  Done!  Report saved: {output_pdf}")
    if score_orig:
        print(f"  HEI Score: {score_orig['total_score']}/100 "
              f"({score_orig['letter_grade']})"
              + (" [partial]" if score_orig["is_partial"] else ""))
    print(f"{'='*64}\n")


if __name__ == "__main__":
    main()

