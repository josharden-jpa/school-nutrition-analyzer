# -*- coding: utf-8 -*-
"""
step6_report.py
Assemble all generated charts and a nutrient summary table into a
polished PDF report using reportlab.
"""

import os
from datetime import date
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image,
    Table, TableStyle, HRFlowable, PageBreak,
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT
import config
from config import DAILY_VALUES, LOWER_IS_BETTER


# ── Color palette ─────────────────────────────────────────────────────────────
BRAND_ORANGE  = colors.HexColor("#e07b54")
BRAND_GREEN   = colors.HexColor("#6aab6e")
BRAND_BLUE    = colors.HexColor("#5b9bd5")
BRAND_RED     = colors.HexColor("#e05c5c")
LIGHT_GRAY    = colors.HexColor("#f5f5f5")
MED_GRAY      = colors.HexColor("#cccccc")
DARK_GRAY     = colors.HexColor("#444444")


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontSize=20, leading=24, textColor=DARK_GRAY,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        "SubTitle",
        parent=styles["Normal"],
        fontSize=11, leading=14, textColor=colors.HexColor("#666666"),
        alignment=TA_CENTER, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "SectionHeader",
        parent=styles["Heading2"],
        fontSize=13, textColor=DARK_GRAY,
        spaceBefore=14, spaceAfter=6,
        borderPad=(0, 0, 2, 0),
    ))
    styles.add(ParagraphStyle(
        "BodySmall",
        parent=styles["Normal"],
        fontSize=9, leading=13, textColor=DARK_GRAY,
    ))
    styles.add(ParagraphStyle(
        "Caption",
        parent=styles["Normal"],
        fontSize=8, leading=11, textColor=colors.HexColor("#888888"),
        alignment=TA_CENTER, spaceBefore=2, spaceAfter=8,
    ))
    return styles


def _nutrient_table(totals_orig: dict, totals_sub: dict = None,
                    orig_name: str = "Original", sub_name: str = "Plant-Based") -> Table:
    """Build a nutrient summary table flowable."""
    has_sub = totals_sub is not None

    # Header
    if has_sub:
        header = ["Nutrient", "Daily Value", orig_name, "% DV", sub_name, "% DV"]
    else:
        header = ["Nutrient", "Daily Value", orig_name, "% DV"]

    rows = [header]

    for label, dv in DAILY_VALUES.items():
        orig_val = totals_orig.get(label, 0) or 0
        orig_pct = round((orig_val / dv) * 100, 1) if dv else 0

        # Units display
        unit = label.split("(")[-1].rstrip(")")
        dv_str   = f"{dv} {unit}"
        orig_str = f"{orig_val:.1f} {unit}"
        pct_str  = f"{orig_pct:.0f}%"

        if has_sub:
            sub_val = totals_sub.get(label, 0) or 0
            sub_pct = round((sub_val / dv) * 100, 1) if dv else 0
            sub_str = f"{sub_val:.1f} {unit}"
            sub_pct_str = f"{sub_pct:.0f}%"
            rows.append([label, dv_str, orig_str, pct_str, sub_str, sub_pct_str])
        else:
            rows.append([label, dv_str, orig_str, pct_str])

    col_widths = [2.2*inch, 1.1*inch, 1.2*inch, 0.7*inch]
    if has_sub:
        col_widths += [1.2*inch, 0.7*inch]

    table = Table(rows, colWidths=col_widths, repeatRows=1)

    # Base style
    style = [
        ("BACKGROUND",   (0, 0), (-1, 0),  BRAND_BLUE),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0),  8),
        ("ALIGN",        (1, 0), (-1, -1), "CENTER"),
        ("ALIGN",        (0, 0), (0, -1),  "LEFT"),
        ("FONTSIZE",     (0, 1), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
        ("GRID",         (0, 0), (-1, -1), 0.25, MED_GRAY),
        ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]

    # Color-code % DV cells
    for row_i, (label, dv) in enumerate(DAILY_VALUES.items(), start=1):
        orig_val = totals_orig.get(label, 0) or 0
        orig_pct = (orig_val / dv * 100) if dv else 0
        pct_col  = 3  # column index of "% DV" for original

        if label in LOWER_IS_BETTER and orig_pct > 100:
            style.append(("BACKGROUND", (pct_col, row_i), (pct_col, row_i), colors.HexColor("#fdd5d5")))
            style.append(("TEXTCOLOR",  (pct_col, row_i), (pct_col, row_i), BRAND_RED))

        if has_sub:
            sub_val = totals_sub.get(label, 0) or 0
            sub_pct = (sub_val / dv * 100) if dv else 0
            sub_pct_col = 5
            if label in LOWER_IS_BETTER and sub_pct > 100:
                style.append(("BACKGROUND", (sub_pct_col, row_i), (sub_pct_col, row_i),
                               colors.HexColor("#fdd5d5")))
                style.append(("TEXTCOLOR",  (sub_pct_col, row_i), (sub_pct_col, row_i), BRAND_RED))

    table.setStyle(TableStyle(style))
    return table


def build_report(
    meal_name:       str,
    totals_orig:     dict,
    chart_dv_path:   str,
    chart_pie_path:  str,
    totals_sub:      dict  = None,
    sub_meal_name:   str   = "Plant-Based Alternative",
    chart_dv_compare_path: str = None,
    output_path:     str   = None,
) -> str:
    """
    Assemble the final PDF report.

    Parameters
    ----------
    meal_name             : original meal name
    totals_orig           : nutrient totals for original meal
    chart_dv_path         : path to the DV% bar chart PNG
    chart_pie_path        : path to the macro pie chart PNG
    totals_sub            : nutrient totals for plant-based sub (optional)
    sub_meal_name         : display name for the substitute
    chart_dv_compare_path : path to the comparison DV% chart (optional)
    output_path           : output PDF path

    Returns
    -------
    str : path of saved PDF
    """
    if output_path is None:
        safe = meal_name.lower().replace(" ", "_").replace("/", "-")
        output_path = f"{safe}_nutrition_report.pdf"

    doc    = SimpleDocTemplate(output_path, pagesize=letter,
                               topMargin=0.6*inch, bottomMargin=0.6*inch,
                               leftMargin=0.75*inch, rightMargin=0.75*inch)
    styles = _styles()
    story  = []

    # ── Cover header ──────────────────────────────────────────────────────────
    story.append(Paragraph("School Nutrition Analysis", styles["ReportTitle"]))
    story.append(Paragraph(meal_name, styles["SubTitle"]))
    story.append(Paragraph(
        f"Generated {date.today().strftime('%B %d, %Y')} · "
        "Data source: USDA FoodData Central · Daily Values: FDA 2020–2025",
        styles["Caption"],
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=MED_GRAY, spaceAfter=10))

    # ── DV% bar chart ─────────────────────────────────────────────────────────
    story.append(Paragraph("Nutrient Content vs. Daily Value", styles["SectionHeader"]))
    if os.path.exists(chart_dv_path):
        story.append(Image(chart_dv_path, width=6.5*inch, height=4.5*inch))
        story.append(Paragraph(
            "Bars show each nutrient as a percentage of the FDA Daily Value for a 2,000 kcal diet. "
            "Red highlights indicate nutrients that exceed recommended limits.",
            styles["Caption"],
        ))

    # ── Macro pie chart ───────────────────────────────────────────────────────
    story.append(Paragraph("Calorie Breakdown by Macronutrient", styles["SectionHeader"]))
    if chart_pie_path and os.path.exists(chart_pie_path):
        story.append(Image(chart_pie_path, width=4.0*inch, height=3.4*inch))
        story.append(Paragraph(
            "Calorie distribution across protein, fat, and carbohydrates.",
            styles["Caption"],
        ))

    # ── Nutrient summary table ────────────────────────────────────────────────
    story.append(Paragraph("Detailed Nutrient Summary", styles["SectionHeader"]))
    story.append(_nutrient_table(totals_orig, totals_sub,
                                 orig_name=meal_name, sub_name=sub_meal_name))
    story.append(Paragraph(
        "Red % DV values indicate the meal exceeds the recommended daily limit for that nutrient.",
        styles["Caption"],
    ))

    # ── Plant-based comparison section ───────────────────────────────────────
    if totals_sub is not None:
        story.append(PageBreak())
        story.append(Paragraph(
            f"Plant-Based Alternative: {sub_meal_name}", styles["SectionHeader"]))

        if chart_dv_compare_path and os.path.exists(chart_dv_compare_path):
            story.append(Image(chart_dv_compare_path, width=6.5*inch, height=4.5*inch))
            story.append(Paragraph(
                f"Orange = {meal_name}   |   Green = {sub_meal_name}",
                styles["Caption"],
            ))

        # Key differences callout
        diffs = []
        for label, dv in DAILY_VALUES.items():
            orig_val = totals_orig.get(label, 0) or 0
            sub_val  = totals_sub.get(label, 0) or 0
            if dv and abs(orig_val - sub_val) / dv >= 0.05:   # >5% DV difference
                direction = "higher" if sub_val > orig_val else "lower"
                unit = label.split("(")[-1].rstrip(")")
                diffs.append(
                    f"<b>{label}</b>: substitute has {abs(sub_val-orig_val):.1f} {unit} "
                    f"<i>({direction})</i>"
                )
        if diffs:
            story.append(Paragraph("Notable nutritional differences (>5% DV):",
                                    styles["BodySmall"]))
            for d in diffs:
                story.append(Paragraph(f"  • {d}", styles["BodySmall"]))

    # ── Footer note ───────────────────────────────────────────────────────────
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MED_GRAY))
    story.append(Paragraph(
        "Nutrient values are estimates based on USDA FoodData Central (SR Legacy / Foundation data). "
        "Actual values may vary by preparation method, brand, and portion size. "
        "Recipe estimated by Claude AI (Anthropic). This report is for informational purposes only.",
        styles["Caption"],
    ))

    doc.build(story)
    print(f"[step6] PDF report saved → {output_path}")
    return output_path


if __name__ == "__main__":
    # Smoke test — requires chart PNGs to exist
    dummy = {
        "Calories (kcal)": 650, "Protein (g)": 28, "Total Fat (g)": 22,
        "Saturated Fat (g)": 9, "Cholesterol (mg)": 65, "Carbohydrates (g)": 80,
        "Dietary Fiber (g)": 4, "Total Sugars (g)": 12, "Sodium (mg)": 980,
        "Calcium (mg)": 350, "Iron (mg)": 3.5, "Potassium (mg)": 600,
        "Vitamin C (mg)": 8, "Vitamin D (mcg)": 1.2,
    }
    build_report(
        meal_name     = "Cheese Pizza (test)",
        totals_orig   = dummy,
        chart_dv_path = "cheese_pizza__test__dv_chart.png",
        chart_pie_path= "cheese_pizza__test__macro_pie.png",
    )
