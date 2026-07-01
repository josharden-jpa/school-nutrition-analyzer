# Cafeteria Critic
### A school lunch scoring pipeline built on real menu data, USDA food group standards, and the belief that what kids eat at school actually matters.

---

## Why I Built This

I'm Josh, a public administration student at Syracuse University's Maxwell School with a strong interest in food policy and institutional food systems. I believe that change — for better or for worse — can happen at a significant scale when it comes to the resources and consumption of food that we are serving and supplying, especially for kids in school. School-age children are growing and in need of proper nutrition, and in many cases school is a huge, if not the majority, if not the sole provider of food for children at different points in their lives.

I wanted to create a tool that could be used by parents, school boards, and school chefs alike — something that could help find gaps in nutrition and advocate for change, like the addition of plant-based foods, with the ability to easily input and quickly see effects at wide scale. My hope is to make a tool that advocates for healthy, plant-based meal options, but I believe that with the right data, logic, and programming, something useful for anybody has been created: a way to quickly analyze school lunches and score them against official benchmarks already in place.

---

## What a School Cafeteria Actually Is

A school cafeteria today is not just a lunch hall. It is a mini grocery store. Many students are presented with options, and the fight starts young — for companies and even food ideologies — to route the next generation into decisions and habits before they become adults who buy their own groceries and make their own eating choices.

The options children have are not unlimited, and there is a huge role that business and politics plays in what ends up on a child's plate. What a child gets used to eating now can have a major impact on what they choose to eat for the rest of their lives. This tool exists to give everybody — consumers, producers, and the schools that put lunches together — the best, most accessible, and most verifiable information possible about what they are offering children, including the typical plate as well as the best and worst that can be consumed on any given day.

---

## What It Does

Cafeteria Critic finds real school menu listings and converts concrete nutrient information into standardized food group servings in order to produce standardized scores — determined officially by the USDA Healthy Eating Index 2020 (HEI-2020). 

For schools that offer daily choices, the tool quickly calculates each possible lunch tray combination in order to find a range of possible scores over time. As the program grows, it maps and retains information across counties and states, enabling comparisons of scores as well as what is holding back or propping up some scores over others.

---

## The Hard Problem — and How It Was Solved

The data that schools post publicly looks like a nutrition label on the back of a product you would buy from the store. But those numbers are not how the USDA scores the health of a meal. For that, you need serving amounts of different food groups — protein, greens, dairy, whole grains, and so on. The part that had to be somewhat simulated was the conversion between micro and macro nutrients and the serving sizes of food groups.

This tool may not produce a perfect conversion rate, but it has backup mechanisms to get closer to the real posted meal. More importantly, the pipeline is built so that any error is repeated consistently across schools as their nutrient information is converted into food group values. Even if the scale is off by a little, it is off by that same amount for each measurement — so when we look at differences between schools, those differences are still real, even if the absolute values carry an honest asterisk. That consistency is what makes comparison valid.

---

## The Three Layers

**1. The data is real.**
Rather than estimating meals from scratch, the pipeline pulls nutrient data posted directly by schools through their menu vendor software. This is the information schools are already publishing — it just hasn't been connected to a scoring system until now.

**2. The conversion is the hard part.**
Schools give us the chemistry — calories, sodium, fat. HEI needs the ingredients — how many ounces of whole grain, how many cups of vegetables. Bridging that gap is the core technical work of this project, and it is where the methodology lives.

**3. The combinations are the point.**
On days where a school offers choices, the pipeline enumerates every possible tray a student could select and scores them all. This produces a distribution — a mean, a median, a standard deviation — rather than a single fixed score. That range is itself a finding. It tells you something about what the school is structurally offering, not just what one kid happened to pick.

---

## What the Findings Show

Across every district tested so far, schools are consistently missing points on whole grains. Refined grains contribute nothing to the HEI score, and they dominate most menus.

The more nuanced finding is that school lunch health outcomes are multidimensional. For schools with fixed menus, the score ceiling is lower — but so is the floor. For schools with choices, a student can do better, but they can also do worse. Whether it is the daily offerings or the individual choices within them that determines a likely score on any given day is different from school to school. The tool is designed to start separating those two levers.

---

## What the Tool Cannot Tell You (Yet)

The moderation side of HEI — sodium, saturated fat, added sugars — is anchored to published label data and is on firm ground. The adequacy side — food group servings — depends on the AI-assisted conversion and carries an error range that is named, bounded, and designed to be consistent rather than hidden. The audit layer built into this pipeline measures and documents that error explicitly, which is itself a finding about what it takes to do this kind of analysis at scale.

---

## Where This Could Go

There are two real use cases for a tool like this, and they are not the same thing.

The first is parent-facing: give me the best tray my kid can pick today. That is a consumer tool — useful, immediate, and something a parent could act on.

The second is district-facing: use this to design menus that score higher before they are ever served. That is a policy and procurement tool. It tells a food service director that their menu structurally cannot score above a certain ceiling because whole grains are not in the rotation. That is the upstream lever, and it is the one that can move millions of meals.

There is also a plant-based comparison layer built into the pipeline. Input any menu item and the tool can run a plant-based alternative through the same scoring system. The data speaks for itself.

---

## The Bottom Line

School lunch is not a joke of a meal that gets thrown around in a food fight in a movie. It is millions of meals every day, and for many children it is an incredibly vital life source — literally. What is being offered and served in school cafeterias deserves to be taken seriously, measured rigorously, and made visible to everyone who has a stake in it.

That is what this tool is for.

---

## Technical Setup

**Requirements:**
```
pip install requests pandas matplotlib reportlab openpyxl
```

**API Keys needed:**
- USDA FoodData Central (free): https://fdc.nal.usda.gov/api-guide.html — place key in `usdaapikey.txt`
- Anthropic API key — set as environment variable `ANTHROPIC_API_KEY`

**Key scripts:**

| Script | Role |
|---|---|
| `nutrislice_scraper.py` | Pulls menu and nutrient data from Nutrislice vendor |
| `nutrislice_fped_bridge.py` | Converts nutrient labels to food group servings |
| `score_district.py` | Runs HEI-2020 scoring across a district |
| `tray_score.py` | Enumerates all tray combinations and produces score distribution |
| `results_ledger.py` | Persists scored results across districts |
| `audit_reconstruction.py` | Compares reconstructed nutrients to posted labels |
| `audit_error_model.py` | Measures and decomposes error by item type |
| `lookup_fdc.py` | Verifies USDA FoodData Central ingredient IDs |
| `main.py` | Orchestrates single-meal pipeline |

**To run a district:**
```bash
python score_district.py
```

**To run the audit layer:**
```bash
python audit_reconstruction.py
python audit_error_model.py
```

---

*Built by Josh Arden — Maxwell School of Citizenship and Public Affairs, Syracuse University*

