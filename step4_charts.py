# -*- coding: utf-8 -*-
"""
step4_charts.py
Generate a matplotlib figure showing each nutrient as a % of Daily Value,
with color coding: blue = fine, red = over DV (for nutrients where high is bad)
or under DV (for nutrients where high is fine — shown separately).

Can overlay an original meal vs a plant-based substitute on the same chart.
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from config import DAILY_VALUES, LOWER_IS_BETTER, COLOR_ORIGINAL, COLOR_SUBSTITUTE


def _dv_percent(totals: dict) -> dict:
    """Convert raw nutrient totals → % of Daily Value."""
    pct = {}
    for label, dv in DAILY_VALUES.items():
        val = totals.get(label, 0) or 0
        pct[label] = round((val / dv) * 100, 1) if dv else 0
    return pct


def _bar_color(label: str, pct: float, is_substitute_bar: bool = False) -> str:
    """
    Pick bar color:
    - substitute bars always use COLOR_SUBSTITUTE green
    - original bars: red if over 100% for a 'lower is better' nutrient, else COLOR_ORIGINAL
    """
    if is_substitute_bar:
        return COLOR_SUBSTITUTE
    if label in LOWER_IS_BETTER and pct > 100:
        return "#e05c5c"   # over the limit
    return COLOR_ORIGINAL


def make_dv_chart(
    meal_name: str,
    totals_original: dict,
    totals_substitute: dict = None,
    sub_meal_name: str = "Plant-Based Alternative",
    output_path: str = None,
) -> str:
    """
    Build and save a grouped horizontal bar chart of DV%.

    Parameters
    ----------
    meal_name         : display name for the original meal
    totals_original   : nutrient totals dict from step3
    totals_substitute : optional nutrient totals for plant-based version
    sub_meal_name     : display name for the substitute
    output_path       : where to save the PNG (auto-generated if None)

    Returns
    -------
    str : path of saved PNG
    """
    has_sub = totals_substitute is not None

    pct_orig = _dv_percent(totals_original)
    pct_sub  = _dv_percent(totals_substitute) if has_sub else {}

    labels  = list(DAILY_VALUES.keys())
    n       = len(labels)
    y_pos   = np.arange(n)

    # ── Layout ────────────────────────────────────────────────────────────────
    fig_height = max(8, n * 0.55 + 2)
    fig, ax    = plt.subplots(figsize=(11, fig_height))

    bar_h     = 0.35 if has_sub else 0.55
    offsets   = [-bar_h / 2, bar_h / 2] if has_sub else [0]

    # ── Draw bars ─────────────────────────────────────────────────────────────
    for i, label in enumerate(labels):
        orig_pct = pct_orig.get(label, 0)
        color    = _bar_color(label, orig_pct, is_substitute_bar=False)
        ax.barh(y_pos[i] + offsets[0], orig_pct, height=bar_h,
                color=color, alpha=0.88, edgecolor="white", linewidth=0.4)
        ax.text(orig_pct + 0.8, y_pos[i] + offsets[0],
                f"{orig_pct:.0f}%", va="center", ha="left", fontsize=7.5,
                color="#333333")

        if has_sub:
            sub_pct = pct_sub.get(label, 0)
            ax.barh(y_pos[i] + offsets[1], sub_pct, height=bar_h,
                    color=COLOR_SUBSTITUTE, alpha=0.88,
                    edgecolor="white", linewidth=0.4)
            ax.text(sub_pct + 0.8, y_pos[i] + offsets[1],
                    f"{sub_pct:.0f}%", va="center", ha="left", fontsize=7.5,
                    color="#333333")

    # ── 100% reference line ───────────────────────────────────────────────────
    ax.axvline(100, color="#555555", linewidth=1.2, linestyle="--", alpha=0.6,
               label="100% Daily Value")

    # ── Axes ──────────────────────────────────────────────────────────────────
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("% of Daily Value", fontsize=10)
    ax.set_xlim(0, max(130, max(pct_orig.values() or [0]) + 20))
    ax.set_title(
        f"Nutritional Content vs Daily Value\n{meal_name}"
        + (f"  vs  {sub_meal_name}" if has_sub else ""),
        fontsize=12, fontweight="bold", pad=12,
    )
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.invert_yaxis()

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_handles = [
        mpatches.Patch(color=COLOR_ORIGINAL,   label=meal_name),
        mpatches.Patch(color="#e05c5c",         label="Exceeds recommended limit"),
    ]
    if has_sub:
        legend_handles.insert(1,
            mpatches.Patch(color=COLOR_SUBSTITUTE, label=sub_meal_name))

    ax.legend(handles=legend_handles, loc="lower right", fontsize=8.5,
              framealpha=0.85)

    plt.tight_layout()

    if output_path is None:
        safe = meal_name.lower().replace(" ", "_").replace("/", "-")
        output_path = f"{safe}_dv_chart.png"

    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[step4] Chart saved → {output_path}")
    return output_path


def make_macro_pie(meal_name: str, totals: dict, output_path: str = None) -> str:
    """
    Simple pie chart showing calorie breakdown by macro
    (protein, fat, carbs — skips the rest).
    """
    protein_kcal = totals.get("Protein (g)", 0) * 4
    fat_kcal     = totals.get("Total Fat (g)", 0) * 9
    carb_kcal    = totals.get("Carbohydrates (g)", 0) * 4
    total_kcal   = protein_kcal + fat_kcal + carb_kcal

    if total_kcal == 0:
        print("[step4] No macronutrient data for pie chart — skipping.")
        return None

    sizes  = [protein_kcal, fat_kcal, carb_kcal]
    labels = [
        f"Protein\n{protein_kcal:.0f} kcal",
        f"Fat\n{fat_kcal:.0f} kcal",
        f"Carbohydrates\n{carb_kcal:.0f} kcal",
    ]
    colors = ["#5b9bd5", "#e07b54", "#f0c05a"]
    explode = (0.03, 0.03, 0.03)

    fig, ax = plt.subplots(figsize=(6, 5))
    ax.pie(sizes, labels=labels, colors=colors, explode=explode,
           autopct="%1.1f%%", startangle=140, textprops={"fontsize": 9})
    ax.set_title(f"Calorie Breakdown\n{meal_name}\n(Total: {total_kcal:.0f} kcal)",
                 fontsize=11, fontweight="bold")

    plt.tight_layout()

    if output_path is None:
        safe = meal_name.lower().replace(" ", "_").replace("/", "-")
        output_path = f"{safe}_macro_pie.png"

    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[step4] Pie chart saved → {output_path}")
    return output_path


if __name__ == "__main__":
    # Quick smoke test with dummy data
    dummy = {
        "Calories (kcal)": 650, "Protein (g)": 28, "Total Fat (g)": 22,
        "Saturated Fat (g)": 9, "Cholesterol (mg)": 65, "Carbohydrates (g)": 80,
        "Dietary Fiber (g)": 4, "Total Sugars (g)": 12, "Sodium (mg)": 980,
        "Calcium (mg)": 350, "Iron (mg)": 3.5, "Potassium (mg)": 600,
        "Vitamin C (mg)": 8, "Vitamin D (mcg)": 1.2,
    }
    make_dv_chart("Cheese Pizza (test)", dummy)
    make_macro_pie("Cheese Pizza (test)", dummy)
