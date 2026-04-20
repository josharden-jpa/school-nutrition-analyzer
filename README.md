# School Nutrition Analyzer

Analyzes school cafeteria meals using Claude AI + USDA FoodData Central,
then generates a downloadable PDF report comparing nutrient content to
FDA Daily Value recommendations — with an optional plant-based alternative layer.

---

## Purpose

School cafeteria meals vary widely in nutritional quality, and it can be
difficult for parents, administrators, or researchers to understand what a
meal actually contains without detailed nutritional data. This project
automates that analysis: given a meal name (e.g. "Soft Shell Taco"), it:

1. Uses Claude AI to estimate a realistic school-serving recipe with gram weights
2. Looks up each ingredient in the USDA FoodData Central database
3. Calculates the meal's total nutrient content and compares it to FDA Daily Values
4. Generates charts and a PDF report summarizing the findings
5. Optionally generates a fully plant-based substitute meal and compares the two side-by-side

---

## Example Output

The repository includes a complete example run for a **Soft Shell Taco**,
including a plant-based comparison (Plant-Based Soft Shell Taco):

| Chart | Description |
|-------|-------------|
| `soft_shell_taco_dv_chart.png` | Nutrient content vs. Daily Value (original meal) |
| `soft_shell_taco_macro_pie.png` | Calorie breakdown by macronutrient |
| `soft_shell_taco_vs_substitute_dv_chart.png` | Original vs. plant-based comparison |
| `soft_shell_taco_nutrition_report.pdf` | Full 3-page PDF report |

### Key findings (Soft Shell Taco)
- **415 kcal** per serving — about 21% of a 2,000 kcal daily diet
- **Protein**: 23.3 g (46% DV) — a solid protein source for a school lunch
- **Saturated Fat**: 8.9 g (44% DV) — notably high; primarily from beef and cheese
- **Sodium**: 650.5 mg (28% DV) — significant for a single meal
- **Cholesterol**: 68.8 mg (23% DV)
- Low in fiber (7% DV), Vitamin C (4% DV), and Vitamin D (0% DV)

The plant-based version reduces saturated fat by nearly half (44% → 23% DV),
eliminates cholesterol entirely, and cuts sodium almost in half (28% → 15% DV),
while keeping calories similar and providing meaningful iron (23% DV).

---

## How to Obtain the Input Data

This project does **not** require you to download a dataset manually.
All nutritional data is fetched live from the **USDA FoodData Central API**,
which is free and publicly available.

**To get a USDA API key (free):**
1. Go to https://fdc.nal.usda.gov/api-guide.html
2. Click "Get an API Key" and register
3. Save the key in a file called `usdaapikey.txt` in the project directory (one line, no quotes)

**To use Claude AI for recipe generation:**
1. Go to https://www.anthropic.com and create an account
2. Generate an API key from the Console
3. Save the key in a file called `anthropicapikey.txt` in the project directory

> ⚠️ **Never commit your API key files to GitHub.** They are listed in `.gitignore`.

---

## Setup

### Requirements

```bash
pip install requests pandas matplotlib reportlab
```

Python 3.10+ is recommended (uses `str | None` union type hints).

### API Keys

Create two plain text files in the project root:
- `usdaapikey.txt` — your USDA FoodData Central API key
- `anthropicapikey.txt` — your Anthropic API key

---

## File Structure and Script Order

Run the scripts in this order via `main.py`, or individually for testing:

| File | Purpose | Run order |
|------|---------|-----------|
| `config.py` | Central config: API keys, Daily Values, color palette | — (imported by all) |
| `main.py` | **Orchestrates the full pipeline** — start here | 1 |
| `step1_recipe.py` | Sends meal name to Claude AI → returns estimated recipe with gram weights | 2 |
| `step2_csv.py` | Converts recipe dict → ingredients CSV file | 3 |
| `step3_usda.py` | Reads CSV → queries USDA API → returns scaled nutrient totals | 4 |
| `step4_charts.py` | Generates DV% bar chart and macro pie chart (PNG) | 5 |
| `step5_substitute.py` | Asks Claude for a plant-based substitute → runs same pipeline | 6 (optional) |
| `step6_report.py` | Assembles all charts + nutrient table into a PDF report | 7 |
| `lookup_fdc.py` | Utility: search USDA for a specific ingredient to find its FDC ID | standalone |

### Running the full pipeline

```bash
python main.py
```

You will be prompted to:
1. Enter a meal name (e.g. `Soft Shell Taco`)
2. Optionally paste extra info from a school district website
3. Confirm Claude's recipe estimate looks reasonable
4. Optionally generate a plant-based substitute comparison

The PDF report is saved in the same directory.

### Running individual steps (for testing)

```bash
python step1_recipe.py                          # test Claude recipe generation
python step3_usda.py soft_shell_taco_ingredients.csv   # test USDA lookup on existing CSV
python step4_charts.py                          # smoke test with dummy data
python lookup_fdc.py "cheddar cheese"           # search USDA for a specific ingredient
```

---

## Additional Files

| File | Description |
|------|-------------|
| `soft_shell_taco_ingredients.csv` | Ingredient list + gram weights for the original taco (Claude's estimate) |
| `plantbased_soft_shell_taco_ingredients.csv` | Ingredient list for the plant-based version |
| `soft_shell_taco_nutrition_report.pdf` | Full example output PDF (3 pages) |
| `*.png` | Chart outputs embedded in the PDF report |

---

## Results Discussion

### Soft Shell Taco (original)

The soft shell taco provides a reasonable protein contribution for a school lunch
(46% DV) but is high in saturated fat (44% DV) relative to its calorie count (21% DV).
This indicates the meal is **calorie-dense in fat rather than carbohydrates** —
confirmed by the macro pie chart showing fat at 51.6% of calories.

Sodium at 28% DV from a single meal is notable: students who eat school lunch five
days per week could be getting a substantial fraction of their daily sodium limit
from this one meal alone.

Micronutrient coverage is weak: Vitamin D (0%), Vitamin C (4%), Dietary Fiber (7%),
and Potassium (8%) are all low. This is consistent with the meal being
protein-and-fat-heavy without significant vegetable content.

### Plant-Based Soft Shell Taco (comparison)

The plant-based version (tempeh replacing ground beef, nutritional yeast replacing
cheese) achieves a meaningful improvement across the "lower is better" nutrients:

- Saturated fat drops from 44% → 23% DV (nearly halved)
- Cholesterol drops from 23% → 0% DV (eliminated entirely)
- Sodium drops from 28% → 15% DV

Protein is lower (34% vs 46% DV) — a real trade-off — but still substantial for
a school lunch. Iron actually improves slightly (19% → 23% DV) due to tempeh's
iron content. Fiber also improves modestly (7% → 10% DV).

The plant-based version is a nutritionally defensible alternative for school
cafeterias seeking to reduce saturated fat and cholesterol without dramatically
changing calorie or protein delivery.

---

## Limitations

- Recipe gram weights are **estimated by Claude AI** and may not exactly match
  any specific school district's preparation.
- USDA nutrient data reflects **raw database entries** (SR Legacy / Foundation),
  which may differ from actual cooked meals depending on preparation method.
- Some nutrients (Vitamin D, Total Sugars) show 0% because USDA data for those
  specific ingredients lacks entries — not necessarily because the nutrient is absent.
- The pipeline is designed for **single-serving analysis** of a defined meal;
  it does not account for day-to-day variation in cafeteria preparation.

---

## License

MIT License — free to use, modify, and distribute with attribution.
