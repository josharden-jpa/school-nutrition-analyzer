# -*- coding: utf-8 -*-
"""
warmup_cache.py
Pre-populates fped_learned.json by running hundreds of common school lunch
meals through Claude recipe estimation + FPED food group mapping.

This is a self-play warmup -- the meals don't need to be scored or analyzed
for nutrition. The goal is just to hit as many novel ingredients as possible
so the learned cache covers them before any real district analysis runs.

After running this once, the cache will cover ~90% of ingredients that
real districts serve, meaning district_menu.py runs faster, makes fewer
API calls, and produces more accurate food group classifications.

Estimated runtime: 20-40 minutes (depends on API speed)
Estimated cost:    $1-3 in Claude API calls (Haiku is cheap)

Usage
-----
    python warmup_cache.py              # run all tiers
    python warmup_cache.py --tier 1     # just proteins (fastest)
    python warmup_cache.py --resume     # skip meals already in cache
    python warmup_cache.py --dry-run    # show meal list without running
"""

import os
import sys
import json
import time
import config

config.load_keys()

import step1_recipe
import step2_csv
import step3b_fped


# -----------------------------------------------------------------------------
# MEAL LIST
# Organized by tier -- run Tier 1 first for maximum coverage per minute.
# Add meals here as you discover gaps in real district runs.
# -----------------------------------------------------------------------------

TIER_1_PROTEINS = [
    # Chicken
    "chicken nuggets", "grilled chicken sandwich", "chicken patty sandwich",
    "chicken strips", "chicken tenders", "baked chicken", "chicken parmesan",
    "chicken and rice", "chicken pot pie", "buffalo chicken wrap",
    "chicken quesadilla", "chicken burrito", "chicken fajitas",
    "chicken noodle soup", "chicken alfredo",

    # Beef
    "cheeseburger", "hamburger", "beef tacos", "beef burrito",
    "beef enchiladas", "sloppy joes", "beef stew", "meatball sub",
    "spaghetti with meat sauce", "lasagna", "beef and rice",
    "salisbury steak", "meatloaf",

    # Turkey
    "turkey sandwich", "turkey burger", "turkey meatballs",
    "turkey tacos", "roast turkey",

    # Fish
    "fish sticks", "fish sandwich", "fish fillet", "baked fish",
    "tuna salad sandwich", "tuna casserole",

    # Pork
    "hot dog", "corn dog", "pepperoni pizza", "sausage pizza",
    "breakfast sausage", "pulled pork sandwich",

    # Plant-based / meatless
    "cheese pizza", "mac and cheese", "grilled cheese sandwich",
    "bean and cheese burrito", "veggie burger", "black bean tacos",
]

TIER_2_SIDES = [
    # Starches
    "french fries", "mashed potatoes", "baked potato", "tater tots",
    "potato wedges", "corn on the cob", "buttered noodles",
    "dinner roll", "garlic bread", "breadstick",

    # Vegetables
    "steamed broccoli", "green beans", "peas", "carrots",
    "corn", "mixed vegetables", "caesar salad", "garden salad",
    "coleslaw", "cucumber slices", "celery sticks",

    # Fruits
    "apple slices", "orange wedges", "banana", "grapes",
    "peach cup", "pear", "fruit cup", "applesauce",
    "strawberries", "watermelon",

    # Dairy / drinks
    "milk", "chocolate milk", "string cheese", "yogurt cup",
    "cottage cheese",

    # Condiments / extras
    "ketchup", "ranch dressing", "honey mustard", "bbq sauce",
    "salsa", "guacamole",
]

TIER_3_FULL_DISHES = [
    # American classics
    "pepperoni pizza", "cheese pizza", "supreme pizza",
    "macaroni and cheese", "chili", "beef chili with beans",
    "chicken soup", "tomato soup with grilled cheese",
    "quesadilla", "nachos with cheese",

    # International
    "beef enchiladas", "chicken enchiladas", "bean burrito",
    "soft shell taco", "hard shell taco", "chicken fajita bowl",
    "fried rice with chicken", "chicken teriyaki with rice",
    "pasta with marinara sauce", "baked ziti",
    "turkey and vegetable stir fry",

    # Breakfast for lunch
    "french toast sticks", "pancakes", "scrambled eggs",
    "breakfast burrito",

    # Sandwiches
    "pbj sandwich", "turkey and cheese sandwich",
    "ham and cheese sandwich", "sub sandwich",

    # Other
    "beef stew with biscuits", "chicken and dumplings",
    "stuffed peppers", "shepherd's pie",
]

ALL_MEALS = TIER_1_PROTEINS + TIER_2_SIDES + TIER_3_FULL_DISHES


# -----------------------------------------------------------------------------
# Warmup runner
# -----------------------------------------------------------------------------

def run_warmup(
    meals:      list[str],
    fped_path:  str  = "FPED_1718.xlsx",
    resume:     bool = True,
    dry_run:    bool = False,
) -> dict:
    """
    Run all meals through Claude recipe estimation + FPED mapping.

    Parameters
    ----------
    meals     : list of meal/dish names to process
    fped_path : path to FPED_1718.xlsx
    resume    : if True, skip meals whose ingredients are already in cache
    dry_run   : if True, just print the meal list without running anything

    Returns
    -------
    {
      "processed":  int,
      "skipped":    int,
      "failed":     int,
      "new_cache_entries": int,
      "failed_meals": list[str],
    }
    """
    if dry_run:
        print(f"\n[warmup] DRY RUN -- would process {len(meals)} meals:")
        for i, meal in enumerate(meals, 1):
            print(f"  {i:3}. {meal}")
        return {}

    print(f"\n{'='*64}")
    print(f"  Cafeteria Critic -- Cache Warmup")
    print(f"  Meals to process: {len(meals)}")
    print(f"  Resume mode: {'ON' if resume else 'OFF'}")
    print(f"{'='*64}\n")

    # Load FPED
    if not os.path.exists(fped_path):
        print(f"[warmup] FPED file not found at '{fped_path}'. Exiting.")
        print("Download from: https://www.ars.usda.gov/ARSUserFiles/80400530/apps/FPED_1718.xls")
        return {}

    fped = step3b_fped.FPEDLookup(fped_path)
    cache_size_before = len(fped._learned)

    processed  = 0
    skipped    = 0
    failed     = 0
    failed_meals = []

    for i, meal_name in enumerate(meals, 1):
        print(f"\n{'─'*56}")
        print(f"  [{i}/{len(meals)}] {meal_name}")
        print(f"{'─'*56}")

        try:
            # Step 1: Claude estimates recipe
            recipe = step1_recipe.get_recipe(meal_name, "")
            ingredients = recipe.get("ingredients", [])

            if not ingredients:
                print(f"  [warmup] No ingredients returned -- skipping")
                failed += 1
                failed_meals.append(meal_name)
                continue

            # Check if resume mode should skip this meal
            if resume:
                ingredient_keys = [
                    ing["name"].lower().strip()
                    for ing in ingredients
                ]
                already_known = all(
                    k in step3b_fped.DIRECT_FPED_MAP or
                    k in step3b_fped.FPED_SKIP or
                    k in step3b_fped.FPED_NO_MATCH or
                    k in fped._learned
                    for k in ingredient_keys
                )
                if already_known:
                    print(f"  [warmup] All ingredients already in cache -- skipping")
                    skipped += 1
                    continue

            # Step 3b: FPED lookup (this is where cache gets populated)
            # We use step2 just to get the ingredient list format right
            csv_path = step2_csv.recipe_to_csv(recipe)
            with open(csv_path, newline="", encoding="utf-8") as f:
                import csv
                rows = list(csv.DictReader(f))

            ing_list = [
                {"name": r["ingredient_name"], "grams": float(r["grams"])}
                for r in rows
            ]

            # Run FPED lookup -- this populates the cache for novel ingredients
            fped.lookup_meal(ing_list)

            processed += 1

            # Save cache after every meal so progress isn't lost on interruption
            fped.save()

            print(f"  [warmup] Cache now has {len(fped._learned)} entries "
                  f"(+{len(fped._learned) - cache_size_before} new)")

            # Polite pause between meals
            time.sleep(2.0)

        except KeyboardInterrupt:
            print(f"\n[warmup] Interrupted by user after {processed} meals.")
            break
        except Exception as e:
            print(f"  [warmup] Failed: {e}")
            failed += 1
            failed_meals.append(meal_name)
            time.sleep(1.0)
            continue

    # Final save
    fped.save()
    new_entries = len(fped._learned) - cache_size_before

    print(f"\n{'='*64}")
    print(f"  Warmup complete!")
    print(f"  Processed : {processed}")
    print(f"  Skipped   : {skipped} (already in cache)")
    print(f"  Failed    : {failed}")
    print(f"  New cache entries: {new_entries}")
    print(f"  Total cache size : {len(fped._learned)} ingredients")
    print(f"{'='*64}\n")

    if failed_meals:
        print(f"Failed meals (consider re-running):")
        for m in failed_meals:
            print(f"  - {m}")

    fped.cache_stats()

    return {
        "processed":         processed,
        "skipped":           skipped,
        "failed":            failed,
        "new_cache_entries": new_entries,
        "failed_meals":      failed_meals,
    }


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    tier    = None
    resume  = True
    dry_run = False

    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        elif arg == "--no-resume":
            resume = False
        elif arg == "--tier":
            idx = sys.argv.index("--tier")
            if idx + 1 < len(sys.argv):
                tier = sys.argv[idx + 1]
        elif arg in ("1", "2", "3"):
            tier = arg

    if tier == "1":
        meals = TIER_1_PROTEINS
        label = "Tier 1 (proteins)"
    elif tier == "2":
        meals = TIER_2_SIDES
        label = "Tier 2 (sides)"
    elif tier == "3":
        meals = TIER_3_FULL_DISHES
        label = "Tier 3 (full dishes)"
    else:
        meals = ALL_MEALS
        label = "All tiers"

    print(f"\n[warmup] Running: {label} ({len(meals)} meals)")
    if not dry_run:
        confirm = input("  This will make Claude + USDA API calls. Proceed? (y/n): ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            sys.exit(0)

    run_warmup(meals=meals, resume=resume, dry_run=dry_run)
