# Cafeteria Critic

**Scores what students could actually eat — not just what's printed on the menu.**

Cafeteria Critic reads a school's *published* lunch menu — the same feed parents see in the menu apps — and scores every reimbursable meal a student could assemble that day against the USDA Healthy Eating Index (HEI-2020). For a fixed menu that's one tray a day; for a choice menu it's hundreds. Instead of a single grade, it returns a **distribution**: the worst tray a student can build, the average, and the best — and it splits the spread into *what you pick* (within-day choice) versus *what day it is* (calendar rotation).

<!-- Drop a console screenshot of the envelope summary here -->
![Envelope output](docs/envelope.png)

## The idea

A lunch menu with choice isn't one meal — it's a *space* of possible meals, and a single letter grade hides that. So the core of the tool is a combinatorial **tray model**: it enumerates every valid National School Lunch Program tray (one entrée, one fruit, one vegetable, one beverage), scores each against HEI-2020, and reports the whole distribution.

That distribution is both more honest and more useful than a grade:

- the **floor** is the worst meal the menu permits — a guarantee, or the lack of one
- the **ceiling** is its best-case potential
- the **spread** says whether quality rides on the student's choices or on the calendar

A fixed menu is simply the degenerate case: a distribution with zero within-day spread.

## What it found

Run across districts that differ on every obvious axis — affluent vs. high-poverty, two menu-software vendors (Nutrislice and MealViewer), California / New York / Texas — the same pattern holds:

- **Whole grains are absent (0 / 10) everywhere.** A high-poverty rural district and a wealthy one fail the same component for the same reason. The failure looks structural to commodity-style menus, not a wealth gap.
- **Failure is about absence, not excess.** Menus lose points for what's *missing* — whole grains, dairy, whole fruit — not for junk. The worst tray a student can assemble is usually incomplete, not unhealthy.
- **Choice changes the variance, not the floor.** A choice menu opens a wide within-day envelope; a fixed menu has none. But if neither serves a whole grain, the ceiling is capped the same way.

> **On rigor:** the *absolute* scores shift with modeling assumptions (e.g., how a daily fruit/veg bar is enumerated). The *structural* findings above don't — they survive the ingredient-matching changes, the vendor switch, and the modeling choices. The tool is built to expose structure, not to rank schools.

<!-- Drop a screenshot of a floor/ceiling tray with its named culprit item here -->
![Floor and ceiling trays](docs/floor_ceiling.png)

## How it works

```
published menu feed
  → vendor adapter (Nutrislice | MealViewer)     normalize to a common day/tray shape
  → item decomposition (Claude)                  "Chicken Teriyaki Bowl" → ingredients
  → USDA FoodData Central + FPED food groups     ingredients → HEI food groups
  → HEI-2020 component scoring
  → combinatorial tray model                     enumerate every valid tray
  → distribution + variance decomposition
```

A few pieces worth calling out:

- **Vendor-agnostic core.** Each menu vendor gets a thin adapter that reshapes its feed into one common structure; a single scoring engine serves all of them. Adding a vendor is a small shim, not a rewrite.
- **Claude-assisted matching.** Menu items are free text ("OTG Galaxy Parfait"). The pipeline decomposes each into ingredients, matches them to USDA food codes, and runs an auto-validation step that rejects bad fuzzy matches before they contaminate the food-group scores — backed by a learned cache so each item is resolved once.
- **Observability as signal.** Menus that publish per-item role tags turn out to be the ones with real choice; fixed menus publish flat, untagged lists. The *shape of the data* is itself a fingerprint of how the cafeteria is run.

## Running it

```bash
pip install -r requirements.txt
# set your USDA FoodData Central and Anthropic keys (see config.py) — never commit them
```

```python
# a choice menu's full distribution (Nutrislice)
import tray_score
from datetime import date, timedelta
weeks = [date(2026, 4, 6) + timedelta(weeks=i) for i in range(8)]
dist = tray_score.score_distribution("srvusd", 45535, 15026, weeks,
                                     grade_band="K-5", label="San Ramon Valley USD")
tray_score.show_summary(dist)

# the same engine, a different vendor (MealViewer)
import mealviewer_bridge as mv
dist = mv.score("JAMESMADISONHIGH", weeks, grade_band="9-12", label="Dallas Madison HS")
tray_score.show_summary(dist)
```

## Repo map

| area | files |
|---|---|
| scoring engine | `tray_score.py`, `score_district.py`, `tray_model.py` |
| vendor adapters & discovery | `nutrislice_*.py`, `mealviewer_bridge.py`, `mealviewer_discover.py`, `probe_district.py` |
| food matching | `step3b_fped.py`, `lookup_fdc.py`, `fped_learned.json`, `FPED_1718.xlsx` |
| original linear pipeline | `step1_*` – `step6_*.py`, `main.py` |
| outputs & maps | `*_summary.csv`, `*_daily.csv`, `map_*.py` |

## Data & credits

USDA Healthy Eating Index (HEI-2020), Food Patterns Equivalents Database (FPED), and FoodData Central are public USDA resources. Menu data comes from public Nutrislice and MealViewer feeds. Item decomposition and matching are assisted by the Anthropic API.

## Limitations

This scores menu *potential*, not consumption — what a student could assemble, not what they ate; closing that gap would take point-of-sale data. Absolute scores are sensitive to modeling choices (see the note above). The structural findings are what the tool is built to support.
