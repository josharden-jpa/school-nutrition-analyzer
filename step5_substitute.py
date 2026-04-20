# -*- coding: utf-8 -*-
"""
step5_substitute.py
Ask Claude to suggest the best plant-based substitution for the original meal,
then let the user confirm or override before running the full pipeline again.
"""

import json
import os
import re
import requests
import config
from step1_recipe import load_anthropic_key


SYSTEM_PROMPT = """You are a school nutrition specialist focused on plant-based meal alternatives.
Given a meal name and its ingredient list, suggest ONE realistic plant-based substitute meal
that a school cafeteria could prepare. The substitute must be FULLY VEGAN — no meat, no dairy,
no eggs, and no hidden animal-derived ingredients. This is non-negotiable.

Common hidden non-vegan ingredients to watch for and replace:
- Gelatin (found in marshmallows, gummy candies, Jell-O, some yogurts) → agar-based alternatives
- Lard or animal shortening (in some tortillas, biscuits, pie crusts) → vegetable oil versions
- Worcestershire sauce (contains anchovies) → vegan Worcestershire or soy sauce
- Caesar dressing (anchovies + egg) → vegan Caesar
- Honey → maple syrup or agave
- Some breads and tortillas (may contain lard or milk) → specify vegan/dairy-free

Replace ALL animal products including cheese, sour cream, butter, and the hidden sources above.

Protein matching is the top priority — the plant-based version should come as close as
possible to the original meal's protein content. Use these preferred swaps in order:
- Chicken (grilled, baked, strips) → seitan, cooked (first choice) or tofu, extra firm, cooked
- Ground beef or ground turkey → tempeh, cooked (crumbled) as first choice, OR
  textured soy protein, cooked as second choice. NOTE: if using textured soy protein,
  be aware USDA data for it reflects a dry concentrate (~58g protein/100g) not the
  rehydrated cooked form — prefer tempeh, cooked for more accurate nutrient tracking.
- Bacon or breakfast sausage → tempeh, cooked (smoked/seasoned)
- Tuna or fish → chickpeas, canned (drained) with nori/seaweed for flavor
- Meatballs → tempeh, cooked or chickpeas, canned
- Dairy cheese → nutritional yeast (for flavor/protein) — do NOT use dairy cheese
- Sour cream or mayo → cashew cream or avocado
- Butter → vegetable oil or coconut oil
- Eggs → tofu, firm (scrambled applications) or silken tofu

AVOID lentils as the primary protein unless there is truly no better option — they are
overused and their USDA nutrient data for the cooked form is unreliable. Prefer seitan,
tempeh, textured soy protein, tofu, or chickpeas instead.

If the original has high protein (>20g), note in the rationale how close the substitute
gets and what the primary protein source is.

Apply these ingredient naming rules:
- ALWAYS specify cooking state: "tempeh, cooked" not "tempeh", "seitan, cooked" not "seitan"
- Use generic USDA-searchable names — no brand names
- Include cooking oil if anything is pan-cooked or griddled (5-10g vegetable oil)
- Do NOT list salt, pepper, or dry spices as standalone ingredients
- Do NOT list compound condiments like "taco seasoning" — omit or break into components

Return ONLY a JSON object in this exact format, no markdown, no preamble:

{
  "suggested_meal_name": "string (name of the plant-based alternative)",
  "rationale": "string (1-2 sentences explaining the swap and protein comparison)",
  "key_swaps": ["string", ...],
  "serving_size_g": number,
  "ingredients": [
    {
      "name": "string (USDA-searchable ingredient name with cooking state)",
      "grams": number,
      "notes": "string (optional)"
    }
  ]
}
"""


def suggest_substitute(original_recipe: dict) -> dict:
    """
    Ask Claude to suggest a plant-based substitute for the original recipe.

    Parameters
    ----------
    original_recipe : dict from step1_recipe.get_recipe()

    Returns
    -------
    dict with keys: suggested_meal_name, rationale, key_swaps, serving_size_g, ingredients
    """
    meal_name    = original_recipe["meal_name"]
    ingredients  = original_recipe["ingredients"]
    ing_summary  = "\n".join(
        f"  - {i['name']}: {i['grams']} g" for i in ingredients
    )
    user_content = (
        f"Original meal: {meal_name}\n\nIngredients:\n{ing_summary}\n\n"
        "Suggest the best plant-based alternative for a school cafeteria."
    )

    anthropic_key = load_anthropic_key()

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         anthropic_key,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model":      config.ANTHROPIC_MODEL,
            "max_tokens": 1000,
            "system":     SYSTEM_PROMPT,
            "messages":   [{"role": "user", "content": user_content}],
        },
    )
    response.raise_for_status()
    data = response.json()

    raw_text = "".join(
        block["text"] for block in data["content"] if block["type"] == "text"
    )
    clean = re.sub(r"```(?:json)?|```", "", raw_text).strip()
    return json.loads(clean)


def present_substitute(suggestion: dict) -> None:
    """Print the Claude-suggested substitute for review."""
    print(f"\n{'='*60}")
    print(f"  Suggested plant-based alternative:")
    print(f"  → {suggestion['suggested_meal_name']}")
    print(f"\n  Rationale: {suggestion['rationale']}")
    print(f"\n  Key swaps:")
    for swap in suggestion.get("key_swaps", []):
        print(f"    • {swap}")
    print(f"\n  Ingredients ({suggestion['serving_size_g']} g serving):")
    for ing in suggestion["ingredients"]:
        notes = ing.get("notes", "")
        print(f"    {ing['name']:<35} {ing['grams']:>5} g  {notes}")
    print(f"{'='*60}\n")


def get_substitute_recipe(original_recipe: dict, interactive: bool = True) -> dict | None:
    """
    Full flow: Claude suggests a substitute → user can accept, override, or skip.

    Parameters
    ----------
    original_recipe : dict from step1
    interactive     : if False, auto-accept Claude's suggestion (for scripted use)

    Returns
    -------
    A recipe dict (same format as step1 output) or None if user skips.
    """
    print("\n[step5] Asking Claude for a plant-based substitute …")
    suggestion = suggest_substitute(original_recipe)
    present_substitute(suggestion)

    if not interactive:
        print("[step5] Auto-accepting Claude's suggestion.")
        return _suggestion_to_recipe(suggestion)

    print("Options:")
    print("  [1] Accept Claude's suggestion")
    print("  [2] Enter a different plant-based meal name")
    print("  [3] Skip — no substitute comparison")
    choice = input("Your choice (1/2/3): ").strip()

    if choice == "1":
        return _suggestion_to_recipe(suggestion)

    elif choice == "2":
        custom_name  = input("Enter substitute meal name: ").strip()
        custom_extra = input("Any extra info (press Enter to skip): ").strip()
        from step1_recipe import get_recipe
        # Always inject plant-based context so step1 doesn't default to dairy/meat
        plant_context = (
            "This is a FULLY VEGAN plant-based substitute — no meat, no dairy, no eggs. "
            "Use plant-based proteins such as seitan, tempeh, tofu, textured soy protein, "
            "or chickpeas. Replace all dairy with nutritional yeast, cashew cream, or avocado. "
            + (custom_extra if custom_extra else "")
        ).strip()
        print(f"\n[step5] Getting plant-based recipe for '{custom_name}' from Claude …")
        return get_recipe(custom_name, plant_context)

    else:
        print("[step5] Skipping plant-based comparison.")
        return None


def _suggestion_to_recipe(suggestion: dict) -> dict:
    """Convert Claude's substitute suggestion to the standard recipe dict format."""
    return {
        "meal_name":      suggestion["suggested_meal_name"],
        "serving_size_g": suggestion["serving_size_g"],
        "ingredients":    suggestion["ingredients"],
    }


if __name__ == "__main__":
    config.load_keys()
    # Test with a dummy original recipe
    dummy_recipe = {
        "meal_name": "Cheeseburger",
        "serving_size_g": 280,
        "ingredients": [
            {"name": "ground beef patty", "grams": 100, "notes": "80/20"},
            {"name": "hamburger bun",     "grams": 60,  "notes": "enriched flour"},
            {"name": "american cheese",   "grams": 20,  "notes": ""},
            {"name": "ketchup",           "grams": 15,  "notes": ""},
            {"name": "mustard",           "grams": 5,   "notes": ""},
            {"name": "iceberg lettuce",   "grams": 10,  "notes": ""},
            {"name": "tomato",            "grams": 20,  "notes": "sliced"},
        ],
    }
    sub = get_substitute_recipe(dummy_recipe, interactive=True)
    if sub:
        print("\nSubstitute recipe:", json.dumps(sub, indent=2))
