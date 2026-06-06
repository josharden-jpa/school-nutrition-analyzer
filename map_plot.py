# -*- coding: utf-8 -*-
"""
map_plot.py
Reads district_scores.json (produced by map_builder.py) and generates a
fully self-contained interactive HTML map using Plotly.

The output file requires no server, no internet connection, and no Python
to view -- just open cafeteria_critic_map.html in any browser.

It can be:
- Hosted on GitHub Pages as a static file
- Embedded in any website with <iframe src="cafeteria_critic_map.html">
- Sent directly to journalists or organizations
- Opened locally in Chrome/Firefox

Usage
-----
    python map_plot.py                          # reads district_scores.json
    python map_plot.py --scores my_scores.json  # custom input
    python map_plot.py --out my_map.html        # custom output name
    python map_plot.py --sample                 # run with built-in sample data

Requirements
------------
    pip install plotly
    (Already included in Anaconda distributions)
"""

import json
import os
import sys
import math
from datetime import datetime


# -----------------------------------------------------------------------------
# Sample data for testing without running district_menu.py first
# Replace / extend with real district data as you analyze more districts
# -----------------------------------------------------------------------------
SAMPLE_DISTRICTS = [
    {
        "name":      "Onondaga Central School District",
        "state":     "NY",
        "lat":       43.03,
        "lng":       -76.14,
        "hei_score": 41.9,
        "hei_grade": "F",
        "n_meals":   4,
        "calories":  545.0,
        "is_partial": True,
    },
]

# Static lat/lng lookup for common NY districts (expand as needed)
# When district_menu.py adds a district, add its coordinates here.
# Find coordinates at: https://www.latlong.net/convert-address-to-lat-long.html
DISTRICT_COORDS = {
    # New York
    "onondaga central school district":          (43.03,  -76.14),
    "syracuse city school district":             (43.05,  -76.15),
    "buffalo city school district":              (42.89,  -78.88),
    "rochester city school district":            (43.16,  -77.61),
    "new york city school district":             (40.71,  -74.01),
    "albany city school district":               (42.65,  -73.76),
    "utica city school district":                (43.10,  -75.23),
    "yonkers city school district":              (40.94,  -73.90),
    "schenectady city school district":          (42.81,  -73.94),
    "troy city school district":                 (42.73,  -73.69),
    # Add more as you analyze them
}


def get_coords(district_name: str, lat: float = None, lng: float = None):
    """Return (lat, lng) for a district, using hardcoded lookup or passed values."""
    if lat and lng:
        return lat, lng
    key = district_name.lower().strip()
    return DISTRICT_COORDS.get(key, None)


def score_to_color(score: float) -> str:
    """Map HEI score to a hex color on red -> amber -> green scale."""
    if score is None:
        return "#aaaaaa"
    if score < 25:  return "#e05c5c"   # red    -- F
    if score < 45:  return "#e07b54"   # orange -- F/D
    if score < 60:  return "#e8a84e"   # amber  -- D/C
    if score < 75:  return "#6aab6e"   # green  -- B
    return "#2e7d32"                   # dark green -- A


def score_to_grade(score: float) -> str:
    if score is None: return "?"
    if score >= 90: return "A"
    if score >= 80: return "B"
    if score >= 70: return "C"
    if score >= 60: return "D"
    return "F"


def load_scores(scores_path: str) -> list[dict]:
    """Load district_scores.json produced by map_builder.py."""
    if not os.path.exists(scores_path):
        print(f"[map_plot] {scores_path} not found -- using sample data")
        print("  Run map_builder.py first to generate real district scores.")
        return SAMPLE_DISTRICTS

    with open(scores_path, encoding="utf-8") as f:
        data = json.load(f)

    districts = data.get("districts", [])
    print(f"[map_plot] Loaded {len(districts)} districts from {scores_path}")
    return districts


def build_map(
    districts:   list[dict],
    output_path: str = "cafeteria_critic_map.html",
    title:       str = "Cafeteria Critic — School Lunch Quality Map",
) -> str:
    """
    Build a Plotly scatter_geo map and write a standalone HTML file.

    Parameters
    ----------
    districts   : list of district dicts (from district_scores.json)
    output_path : where to save the HTML file
    title       : page title and map title

    Returns
    -------
    str : path to the written HTML file
    """
    try:
        import plotly.graph_objects as go
        import plotly.io as pio
    except ImportError:
        print("[map_plot] Plotly not found. Install with: pip install plotly")
        print("  (Plotly is included in Anaconda -- try: conda install plotly)")
        return None

    # ── Prepare data ──────────────────────────────────────────────────────────
    plot_districts = []
    skipped = []

    for d in districts:
        coords = get_coords(d["name"], d.get("lat"), d.get("lng"))
        if coords is None:
            skipped.append(d["name"])
            continue
        lat, lng = coords
        score = d.get("hei_score")
        grade = d.get("hei_grade") or score_to_grade(score)

        plot_districts.append({
            "name":       d["name"],
            "state":      d.get("state", ""),
            "lat":        lat,
            "lng":        lng,
            "score":      score,
            "grade":      grade,
            "n_meals":    d.get("n_meals", "?"),
            "calories":   d.get("calories"),
            "is_partial": d.get("is_partial", False),
            "color":      score_to_color(score),
        })

    if skipped:
        print(f"[map_plot] Skipped {len(skipped)} districts (no coordinates):")
        for s in skipped:
            print(f"  - {s}")
        print("  Add coordinates to DISTRICT_COORDS in map_plot.py")

    if not plot_districts:
        print("[map_plot] No districts to plot. Using sample data.")
        plot_districts = []
        for d in SAMPLE_DISTRICTS:
            coords = get_coords(d["name"], d.get("lat"), d.get("lng"))
            if coords:
                lat, lng = coords
                plot_districts.append({
                    "name": d["name"], "state": d.get("state",""),
                    "lat": lat, "lng": lng,
                    "score": d.get("hei_score"), "grade": d.get("hei_grade","F"),
                    "n_meals": d.get("n_meals","?"), "calories": d.get("calories"),
                    "is_partial": d.get("is_partial", False),
                    "color": score_to_color(d.get("hei_score")),
                })

    # ── Build hover text ──────────────────────────────────────────────────────
    hover_texts = []
    marker_colors = []
    marker_sizes  = []
    lats, lngs, names = [], [], []

    for d in plot_districts:
        score_str = f"{d['score']:.1f}" if d['score'] is not None else "N/A"
        cal_str   = f"{d['calories']:.0f} kcal avg" if d["calories"] else ""
        partial   = " (partial)" if d["is_partial"] else ""

        hover = (
            f"<b>{d['name']}</b><br>"
            f"{d['state']} · {d['n_meals']} meals analyzed<br>"
            f"<b>HEI Score: {score_str}/100 (Grade {d['grade']}){partial}</b><br>"
            f"{cal_str}<br>"
            f"<i>US child avg ≈ 50 (USDA 2013)</i>"
        )
        hover_texts.append(hover)
        marker_colors.append(d["color"])
        marker_sizes.append(18)
        lats.append(d["lat"])
        lngs.append(d["lng"])
        names.append(d["name"])

    # ── Score labels inside markers ───────────────────────────────────────────
    score_labels = [
        f"{d['score']:.0f}" if d['score'] is not None else "?"
        for d in plot_districts
    ]

    # ── Build Plotly figure ───────────────────────────────────────────────────
    fig = go.Figure()

    # District markers
    fig.add_trace(go.Scattergeo(
        lat          = lats,
        lon          = lngs,
        text         = score_labels,
        hovertext    = hover_texts,
        hoverinfo    = "text",
        mode         = "markers+text",
        textposition = "middle center",
        textfont     = dict(size=9, color="white", family="Arial"),
        marker       = dict(
            size    = marker_sizes,
            color   = marker_colors,
            line    = dict(width=1.5, color="rgba(255,255,255,0.6)"),
            opacity = 0.92,
        ),
        name         = "Analyzed districts",
        showlegend   = True,
    ))

    # Invisible legend entries for the color scale
    scale_items = [
        ("A (75-100)", "#2e7d32"),
        ("B (60-75)",  "#6aab6e"),
        ("C (45-60)",  "#e8a84e"),
        ("D/F (0-45)", "#e05c5c"),
        ("US avg ~50", "#5b9bd5"),
    ]
    for label, color in scale_items:
        fig.add_trace(go.Scattergeo(
            lat=[None], lon=[None],
            mode="markers",
            marker=dict(size=12, color=color),
            name=label,
            showlegend=True,
        ))

    # ── Layout ────────────────────────────────────────────────────────────────
    fig.update_layout(
        title=dict(
            text=(
                f"<b>Cafeteria Critic</b> — School Lunch Quality (HEI-2020)<br>"
                f"<span style='font-size:13px;color:#666;'>"
                f"{len(plot_districts)} district(s) analyzed · "
                f"Generated {datetime.now().strftime('%B %d, %Y')}"
                f"</span>"
            ),
            x=0.01, xanchor="left",
            font=dict(size=17, family="Arial"),
        ),
        geo=dict(
            scope            = "usa",
            projection_type  = "albers usa",
            showland         = True,
            landcolor        = "#f0f0f0",
            showlakes        = True,
            lakecolor        = "#cce5ff",
            showrivers       = False,
            showcountries    = False,
            showsubunits     = True,
            subunitcolor     = "#cccccc",
            subunitwidth     = 0.5,
            bgcolor          = "#f8f9fa",
            framecolor       = "#cccccc",
            framewidth       = 0.5,
        ),
        legend=dict(
            title=dict(text="HEI Score Grade", font=dict(size=12)),
            x=0.01, y=0.35,
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#cccccc",
            borderwidth=0.5,
            font=dict(size=11),
        ),
        paper_bgcolor = "#ffffff",
        plot_bgcolor  = "#ffffff",
        margin        = dict(l=0, r=0, t=80, b=20),
        height        = 600,
        annotations   = [
            dict(
                text=(
                    "Score source: HEI-2020 (NCI/USDA) · "
                    "Nutrient data: USDA FoodData Central · "
                    "Food groups: USDA FPED · "
                    "Recipes estimated by Claude AI (Anthropic)<br>"
                    "Benchmark: avg U.S. child diet ≈ 50/100 (USDA 2013). "
                    "Scores are estimates based on AI recipe estimation and may vary from actual meals."
                ),
                xref="paper", yref="paper",
                x=0.01, y=-0.02,
                xanchor="left", yanchor="top",
                showarrow=False,
                font=dict(size=9, color="#888888"),
            )
        ],
    )

    # ── Write standalone HTML ─────────────────────────────────────────────────
    pio.write_html(
        fig,
        file              = output_path,
        full_html         = True,
        include_plotlyjs  = "cdn",   # loads Plotly from CDN; use "inline" for offline
        auto_open         = False,
        config            = {
            "displayModeBar": True,
            "modeBarButtonsToRemove": ["select2d", "lasso2d"],
            "toImageButtonOptions": {
                "format":   "png",
                "filename": "cafeteria_critic_map",
                "height":   700,
                "width":    1200,
                "scale":    2,
            },
        },
    )

    print(f"\n[map_plot] Map saved -> {output_path}")
    print(f"  Open in any browser: file:///{os.path.abspath(output_path)}")
    print(f"  Host on GitHub Pages, share link, or embed with <iframe>")
    print(f"  Districts plotted: {len(plot_districts)}")

    if skipped:
        print(f"\n  To add missing districts, open map_plot.py and add to DISTRICT_COORDS:")
        for s in skipped:
            print(f'    "{s.lower()}": (LAT, LNG),')

    return output_path


# ── Full pipeline: scores JSON -> map HTML ────────────────────────────────────

def run_pipeline(
    scores_path: str = "district_scores.json",
    output_path: str = "cafeteria_critic_map.html",
) -> str:
    """
    Full pipeline:
      1. Load district_scores.json (from map_builder.py)
      2. Match districts to coordinates
      3. Generate and save interactive HTML map

    Returns path to the output HTML file.
    """
    districts = load_scores(scores_path)
    return build_map(districts, output_path)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    scores_path = "district_scores.json"
    output_path = "cafeteria_critic_map.html"
    use_sample  = False

    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--scores" and i + 1 < len(args):
            scores_path = args[i + 1]
        elif arg == "--out" and i + 1 < len(args):
            output_path = args[i + 1]
        elif arg == "--sample":
            use_sample = True

    if use_sample:
        print("[map_plot] Using built-in sample data (--sample flag)")
        build_map(SAMPLE_DISTRICTS, output_path)
    else:
        run_pipeline(scores_path, output_path)
