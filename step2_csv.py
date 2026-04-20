# -*- coding: utf-8 -*-
"""
step2_csv.py
Convert a recipe dict (from step1) into a clean ingredients CSV file
that step3 will read and send to the USDA API.
"""

import csv

CSV_COLUMNS = ["ingredient_name", "grams"]


# Realistic gram weight bounds per ingredient type.
# Anything outside these ranges for a single school serving is suspicious.
GRAM_BOUNDS = {
    "default":   (1,   250),   # catch-all
    "oil":       (2,    20),   # cooking oils — more than 20g in one meal is a lot
    "butter":    (2,    20),
    "cheese":    (5,    60),   # a whole slice is ~20g; 60g is a lot of cheese
    "sour cream":(5,    40),
    "cream":     (5,    40),
    "sauce":     (10,  120),
    "tortilla":  (20,   80),
    "bread":     (20,  100),
    "meat":      (30,  180),   # proteins
    "beef":      (30,  180),
    "chicken":   (30,  180),
    "tofu":      (30,  180),
    "tempeh":    (30,  180),
    "seitan":    (30,  180),
    "lettuce":   (5,    60),
    "tomato":    (10,   80),
}


def _gram_check(name: str, grams: float) -> str | None:
    """Return a warning string if grams look implausible, else None."""
    name_lower = name.lower()
    lo, hi = GRAM_BOUNDS["default"]
    for keyword, bounds in GRAM_BOUNDS.items():
        if keyword != "default" and keyword in name_lower:
            lo, hi = bounds
            break
    if grams < lo:
        return f"  ⚠️  GRAM FLAG: '{name}' = {grams}g seems too low (min ~{lo}g) — check recipe"
    if grams > hi:
        return f"  ⚠️  GRAM FLAG: '{name}' = {grams}g seems too high (max ~{hi}g) — check recipe"
    return None


def recipe_to_csv(recipe: dict, output_path: str = None) -> str:
    """
    Write recipe ingredients to a 2-column CSV (ingredient_name, grams).
    step3_usda.py reads this and does the USDA search + detail lookup.

    Parameters
    ----------
    recipe      : dict returned by step1_recipe.get_recipe()
    output_path : optional explicit path; defaults to '<meal_name>_ingredients.csv'

    Returns
    -------
    str : path of the written CSV file
    """
    if output_path is None:
        safe_name = recipe["meal_name"].lower().replace(" ", "_").replace("/", "-")
        output_path = f"{safe_name}_ingredients.csv"

    warnings = []
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for ing in recipe["ingredients"]:
            writer.writerow({
                "ingredient_name": ing["name"],
                "grams":           ing["grams"],
            })
            warn = _gram_check(ing["name"], ing["grams"])
            if warn:
                warnings.append(warn)

    print(f"[step2] Saved {len(recipe['ingredients'])} ingredients → {output_path}")
    if warnings:
        print("[step2] Gram weight warnings — review before continuing:")
        for w in warnings:
            print(w)
    return output_path


def load_csv(csv_path: str) -> list[dict]:
    """Read an ingredients CSV back into a list of dicts."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


if __name__ == "__main__":
    sample = {
        "meal_name": "Soft Shell Taco",
        "serving_size_g": 250,
        "ingredients": [
            {"name": "flour tortilla", "grams": 65},
            {"name": "ground beef",    "grams": 85},
            {"name": "cheddar cheese", "grams": 20},
        ],
    }
    path = recipe_to_csv(sample)
    print("Rows:", load_csv(path))
