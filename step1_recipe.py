# -*- coding: utf-8 -*-
"""
step1_recipe.py
Send a meal name (+ any extra context) to Claude and get back a structured
recipe: list of ingredients with estimated gram weights per serving.
"""

import json
import os
import re
import requests
import config


SYSTEM_PROMPT = """You are a culinary nutrition expert specializing in school lunch programs.
When given a meal name and optional context, return ONLY a JSON object — no markdown, 
no explanation, no preamble — in exactly this format:

{
  "meal_name": "string (cleaned display name)",
  "serving_size_g": number (total grams for one student serving),
  "ingredients": [
    {
      "name": "string (plain ingredient name, e.g. 'chicken breast')",
      "grams": number (grams of this ingredient per serving),
      "notes": "string (optional: cooking method, e.g. 'baked, skinless')"
    }
  ]
}

Rules:
- Use realistic school-cafeteria portion sizes (grades K-12, ~ages 5-18).
- Ingredient names must be generic and searchable — use common names like "ground beef, cooked"
  not brand names or compound descriptions. Avoid adjectives USDA wouldn't use (e.g. "crispy",
  "homestyle"). When in doubt, use the simplest possible name for the food.
- ALWAYS specify cooking state in the ingredient name: use "lentils, cooked" not "lentils",
  "black beans, canned" not "black beans", "ground beef, cooked" not "ground beef",
  "tomato sauce, canned" not "tomato sauce". Raw vs cooked makes a large difference in
  nutrient density — always use the state as it would actually be eaten in the dish.
- ALWAYS include cooking oil or fat if the protein or starch is cooked in it (e.g. "vegetable
  oil" for griddled items, "butter" for sauteed items). These are calorie-dense and often
  omitted by mistake. A typical school serving uses 5-10g of oil.
- Do NOT list salt, pepper, or dry spices (cumin, chili powder, garlic powder, oregano, etc.)
  as standalone ingredients — their gram weights are too small and they cause bad USDA search
  matches. Sodium will still be captured accurately through the other ingredients' USDA entries.
- Do NOT list compound condiments like "taco seasoning" or "ranch dressing mix" — break them
  into their dominant components, or omit if negligible. Use plain equivalents where possible.
- Include ALL significant ingredients (proteins, starches, vegetables, sauces, oils, cheese, etc.).
- For ingredients that contain hidden animal products, note it in the ingredient name so
  the plant-based substitution step can identify them correctly. Examples:
  "marshmallows (contains gelatin)", "gummy candies (contains gelatin)",
  "tortillas (may contain lard)", "caesar dressing (contains anchovy, egg)"
- Estimate gram weights honestly — school food tends to be higher in fat and sodium than
  idealized recipes suggest. Do not underestimate oils, cheese, or sauces.
- Do NOT include water or non-nutritive items.
- Return ONLY the raw JSON object, nothing else.
"""


def load_anthropic_key() -> str:
    """Read Anthropic API key from file or environment variable."""
    # Try file first (put your key in anthropicapikey.txt next to the scripts)
    if os.path.exists("anthropicapikey.txt"):
        with open("anthropicapikey.txt") as f:
            return f.readline().strip()
    # Fall back to environment variable
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise FileNotFoundError(
            "Anthropic API key not found.\n"
            "Either create 'anthropicapikey.txt' with your key on the first line,\n"
            "or set the ANTHROPIC_API_KEY environment variable."
        )
    return key


def get_recipe(meal_name: str, extra_info: str = "") -> dict:
    """
    Ask Claude to estimate a recipe for the given meal.

    Parameters
    ----------
    meal_name  : e.g. "Cheese Pizza"
    extra_info : any additional context from the district website

    Returns
    -------
    dict with keys: meal_name, serving_size_g, ingredients
    """
    user_content = f"Meal: {meal_name}"
    if extra_info.strip():
        user_content += f"\nAdditional context: {extra_info.strip()}"

    anthropic_key = load_anthropic_key()

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         anthropic_key,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": config.ANTHROPIC_MODEL,
            "max_tokens": 1000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_content}],
        },
    )
    response.raise_for_status()
    data = response.json()

    raw_text = "".join(
        block["text"] for block in data["content"] if block["type"] == "text"
    )

    # Strip any accidental markdown fences
    clean = re.sub(r"```(?:json)?|```", "", raw_text).strip()

    recipe = json.loads(clean)
    return recipe


def print_recipe(recipe: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  Meal : {recipe['meal_name']}")
    print(f"  Total serving size: {recipe['serving_size_g']} g")
    print(f"{'='*60}")
    print(f"{'Ingredient':<35} {'Grams':>8}  Notes")
    print(f"{'-'*60}")
    for ing in recipe["ingredients"]:
        notes = ing.get("notes", "")
        print(f"  {ing['name']:<33} {ing['grams']:>6} g  {notes}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    config.load_keys()
    meal  = input("Enter meal name: ").strip()
    extra = input("Any extra info from district site (press Enter to skip): ").strip()
    recipe = get_recipe(meal, extra)
    print_recipe(recipe)
    print("Recipe JSON:", json.dumps(recipe, indent=2))
