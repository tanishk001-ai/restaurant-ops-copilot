"""
All static constants for the synthetic restaurant data.
Every slug used in BILL_OF_MATERIALS must appear in RAW_MATERIAL_CATALOG.
"""

from datetime import date

# ─── Restaurant ────────────────────────────────────────────────────────────────

RESTAURANT = {
    "name": "Spice Junction",
    "locality": "Indiranagar",
    "cuisine": "North Indian",
}

# ─── Menu items (25 dishes) ────────────────────────────────────────────────────

MENU_ITEMS = [
    # Main course — veg
    {"slug": "paneer_butter_masala", "name": "Paneer Butter Masala",      "price": 320, "category": "main_course"},
    {"slug": "dal_makhani",          "name": "Dal Makhani",               "price": 280, "category": "main_course"},
    {"slug": "palak_paneer",         "name": "Palak Paneer",              "price": 300, "category": "main_course"},
    {"slug": "chole_bhature",        "name": "Chole Bhature",             "price": 220, "category": "main_course"},
    {"slug": "kadai_paneer",         "name": "Kadai Paneer",              "price": 320, "category": "main_course"},
    {"slug": "malai_kofta",          "name": "Malai Kofta",               "price": 340, "category": "main_course"},
    {"slug": "pav_bhaji",            "name": "Pav Bhaji",                 "price": 180, "category": "main_course"},
    # Main course — non-veg
    {"slug": "butter_chicken",       "name": "Butter Chicken",            "price": 360, "category": "main_course"},
    {"slug": "mutton_rogan_josh",    "name": "Mutton Rogan Josh",         "price": 480, "category": "main_course"},
    # Biryani
    {"slug": "chicken_biryani",      "name": "Chicken Biryani",           "price": 380, "category": "biryani"},
    {"slug": "veg_biryani",          "name": "Veg Biryani",               "price": 300, "category": "biryani"},
    # Breads
    {"slug": "naan",                 "name": "Butter Naan",               "price":  60, "category": "bread"},
    {"slug": "tandoori_roti",        "name": "Tandoori Roti",             "price":  40, "category": "bread"},
    {"slug": "aloo_paratha",         "name": "Aloo Paratha (2 pcs)",      "price": 160, "category": "bread"},
    # Starters
    {"slug": "samosa",               "name": "Samosa (2 pcs)",            "price":  80, "category": "starter"},
    {"slug": "onion_bhaji",          "name": "Onion Bhaji",               "price": 120, "category": "starter"},
    {"slug": "chicken_tikka",        "name": "Chicken Tikka",             "price": 380, "category": "starter"},
    {"slug": "seekh_kebab",          "name": "Seekh Kebab",               "price": 360, "category": "starter"},
    # Sides
    {"slug": "raita",                "name": "Raita",                     "price":  80, "category": "side"},
    # Desserts
    {"slug": "gulab_jamun",          "name": "Gulab Jamun (2 pcs)",       "price": 100, "category": "dessert"},
    {"slug": "kheer",                "name": "Kheer",                     "price": 120, "category": "dessert"},
    # Beverages
    {"slug": "lassi",                "name": "Sweet Lassi",               "price": 100, "category": "beverage"},
    {"slug": "mango_lassi",          "name": "Mango Lassi",               "price": 140, "category": "beverage"},
    {"slug": "masala_chai",          "name": "Masala Chai",               "price":  60, "category": "beverage"},
    {"slug": "cold_drink",           "name": "Cold Drink",                "price":  80, "category": "beverage"},
]

# ─── Base daily demand (avg units sold on a typical weekday) ───────────────────

BASE_DEMAND: dict[str, float] = {
    "paneer_butter_masala": 28,
    "dal_makhani":          22,
    "palak_paneer":         16,
    "chole_bhature":        20,
    "kadai_paneer":         19,
    "malai_kofta":          13,
    "pav_bhaji":            24,
    "butter_chicken":       25,
    "mutton_rogan_josh":     8,
    "chicken_biryani":      32,
    "veg_biryani":          18,
    "naan":                 85,
    "tandoori_roti":        65,
    "aloo_paratha":         24,
    "samosa":               36,
    "onion_bhaji":          22,
    "chicken_tikka":        16,
    "seekh_kebab":          12,
    "raita":                42,
    "gulab_jamun":          28,
    "kheer":                14,
    "lassi":                26,
    "mango_lassi":          20,
    "masala_chai":          52,
    "cold_drink":           44,
}

# ─── Bill of materials for 10 key dishes ──────────────────────────────────────
# qty_per_unit: amount of raw material consumed per ONE serving

BILL_OF_MATERIALS: dict[str, list[dict]] = {
    "paneer_butter_masala": [
        {"raw_material": "paneer",              "qty_per_unit": 200,  "unit": "g"},
        {"raw_material": "butter",              "qty_per_unit":  30,  "unit": "g"},
        {"raw_material": "cream",               "qty_per_unit":  50,  "unit": "ml"},
        {"raw_material": "tomato",              "qty_per_unit": 150,  "unit": "g"},
        {"raw_material": "onion",               "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "ginger_garlic_paste", "qty_per_unit":  20,  "unit": "g"},
        {"raw_material": "spice_mix",           "qty_per_unit":  15,  "unit": "g"},
    ],
    "dal_makhani": [
        {"raw_material": "black_lentils",       "qty_per_unit": 150,  "unit": "g"},
        {"raw_material": "butter",              "qty_per_unit":  30,  "unit": "g"},
        {"raw_material": "cream",               "qty_per_unit":  50,  "unit": "ml"},
        {"raw_material": "tomato",              "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "onion",               "qty_per_unit":  75,  "unit": "g"},
        {"raw_material": "ginger_garlic_paste", "qty_per_unit":  15,  "unit": "g"},
        {"raw_material": "spice_mix",           "qty_per_unit":  10,  "unit": "g"},
    ],
    "butter_chicken": [
        {"raw_material": "chicken",             "qty_per_unit": 250,  "unit": "g"},
        {"raw_material": "butter",              "qty_per_unit":  30,  "unit": "g"},
        {"raw_material": "cream",               "qty_per_unit":  50,  "unit": "ml"},
        {"raw_material": "tomato",              "qty_per_unit": 150,  "unit": "g"},
        {"raw_material": "onion",               "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "ginger_garlic_paste", "qty_per_unit":  20,  "unit": "g"},
        {"raw_material": "spice_mix",           "qty_per_unit":  15,  "unit": "g"},
    ],
    "chicken_biryani": [
        {"raw_material": "chicken",             "qty_per_unit": 200,  "unit": "g"},
        {"raw_material": "basmati_rice",        "qty_per_unit": 150,  "unit": "g"},
        {"raw_material": "onion",               "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "yogurt",              "qty_per_unit":  75,  "unit": "g"},
        {"raw_material": "ginger_garlic_paste", "qty_per_unit":  20,  "unit": "g"},
        {"raw_material": "biryani_spice_mix",   "qty_per_unit":  20,  "unit": "g"},
        {"raw_material": "saffron",             "qty_per_unit":   0.5,"unit": "g"},
    ],
    "veg_biryani": [
        {"raw_material": "basmati_rice",        "qty_per_unit": 150,  "unit": "g"},
        {"raw_material": "mixed_vegetables",    "qty_per_unit": 200,  "unit": "g"},
        {"raw_material": "onion",               "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "yogurt",              "qty_per_unit":  75,  "unit": "g"},
        {"raw_material": "ginger_garlic_paste", "qty_per_unit":  15,  "unit": "g"},
        {"raw_material": "biryani_spice_mix",   "qty_per_unit":  20,  "unit": "g"},
        {"raw_material": "saffron",             "qty_per_unit":   0.5,"unit": "g"},
    ],
    "palak_paneer": [
        {"raw_material": "paneer",              "qty_per_unit": 150,  "unit": "g"},
        {"raw_material": "spinach",             "qty_per_unit": 200,  "unit": "g"},
        {"raw_material": "cream",               "qty_per_unit":  30,  "unit": "ml"},
        {"raw_material": "onion",               "qty_per_unit":  75,  "unit": "g"},
        {"raw_material": "ginger_garlic_paste", "qty_per_unit":  15,  "unit": "g"},
        {"raw_material": "spice_mix",           "qty_per_unit":  10,  "unit": "g"},
    ],
    "chole_bhature": [
        {"raw_material": "chickpeas",           "qty_per_unit": 150,  "unit": "g"},
        {"raw_material": "wheat_flour",         "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "onion",               "qty_per_unit":  75,  "unit": "g"},
        {"raw_material": "tomato",              "qty_per_unit":  75,  "unit": "g"},
        {"raw_material": "ginger_garlic_paste", "qty_per_unit":  15,  "unit": "g"},
        {"raw_material": "spice_mix",           "qty_per_unit":  12,  "unit": "g"},
        {"raw_material": "cooking_oil",         "qty_per_unit":  30,  "unit": "ml"},
    ],
    "naan": [
        {"raw_material": "wheat_flour",         "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "yogurt",              "qty_per_unit":  30,  "unit": "g"},
        {"raw_material": "butter",              "qty_per_unit":  15,  "unit": "g"},
        {"raw_material": "yeast",               "qty_per_unit":   3,  "unit": "g"},
    ],
    "malai_kofta": [
        {"raw_material": "paneer",              "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "potato",              "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "cream",               "qty_per_unit":  50,  "unit": "ml"},
        {"raw_material": "tomato",              "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "onion",               "qty_per_unit":  75,  "unit": "g"},
        {"raw_material": "butter",              "qty_per_unit":  20,  "unit": "g"},
        {"raw_material": "spice_mix",           "qty_per_unit":  12,  "unit": "g"},
    ],
    "kadai_paneer": [
        {"raw_material": "paneer",              "qty_per_unit": 200,  "unit": "g"},
        {"raw_material": "capsicum",            "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "onion",               "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "tomato",              "qty_per_unit": 100,  "unit": "g"},
        {"raw_material": "butter",              "qty_per_unit":  20,  "unit": "g"},
        {"raw_material": "spice_mix",           "qty_per_unit":  15,  "unit": "g"},
    ],
}

# ─── Raw material → Instamart product catalog (21 materials) ──────────────────

RAW_MATERIAL_CATALOG: dict[str, dict] = {
    "paneer": {
        "instamart_product_id": "IM_001",
        "product_name": "Fresho Fresh Paneer Block",
        "pack_size": 200, "unit": "g",   "price": 75,  "category": "dairy",
    },
    "butter": {
        "instamart_product_id": "IM_002",
        "product_name": "Amul Butter",
        "pack_size": 500, "unit": "g",   "price": 285, "category": "dairy",
    },
    "cream": {
        "instamart_product_id": "IM_003",
        "product_name": "Amul Fresh Cream",
        "pack_size": 200, "unit": "ml",  "price": 48,  "category": "dairy",
    },
    "tomato": {
        "instamart_product_id": "IM_004",
        "product_name": "Fresh Tomatoes",
        "pack_size": 1000,"unit": "g",   "price": 42,  "category": "vegetables",
    },
    "onion": {
        "instamart_product_id": "IM_005",
        "product_name": "Fresh Onions",
        "pack_size": 1000,"unit": "g",   "price": 38,  "category": "vegetables",
    },
    "ginger_garlic_paste": {
        "instamart_product_id": "IM_006",
        "product_name": "Ginger Garlic Paste",
        "pack_size": 200, "unit": "g",   "price": 48,  "category": "condiments",
    },
    "spice_mix": {
        "instamart_product_id": "IM_007",
        "product_name": "MDH Kitchen King Masala",
        "pack_size": 100, "unit": "g",   "price": 60,  "category": "spices",
    },
    "chicken": {
        "instamart_product_id": "IM_008",
        "product_name": "Fresho Fresh Chicken Curry Cut",
        "pack_size": 500, "unit": "g",   "price": 195, "category": "meat",
    },
    "basmati_rice": {
        "instamart_product_id": "IM_009",
        "product_name": "Daawat Rozana Basmati Rice",
        "pack_size": 1000,"unit": "g",   "price": 98,  "category": "grains",
    },
    "yogurt": {
        "instamart_product_id": "IM_010",
        "product_name": "Mother Dairy Curd",
        "pack_size": 400, "unit": "g",   "price": 42,  "category": "dairy",
    },
    "saffron": {
        "instamart_product_id": "IM_011",
        "product_name": "Saffron Strands (Kesar)",
        "pack_size": 1,   "unit": "g",   "price": 130, "category": "spices",
    },
    "black_lentils": {
        "instamart_product_id": "IM_012",
        "product_name": "Whole Black Urad Dal",
        "pack_size": 500, "unit": "g",   "price": 88,  "category": "pulses",
    },
    "spinach": {
        "instamart_product_id": "IM_013",
        "product_name": "Fresh Spinach (Palak)",
        "pack_size": 500, "unit": "g",   "price": 32,  "category": "vegetables",
    },
    "chickpeas": {
        "instamart_product_id": "IM_014",
        "product_name": "White Chickpeas (Kabuli Chana)",
        "pack_size": 500, "unit": "g",   "price": 68,  "category": "pulses",
    },
    "wheat_flour": {
        "instamart_product_id": "IM_015",
        "product_name": "Aashirvaad Whole Wheat Atta",
        "pack_size": 5000,"unit": "g",   "price": 295, "category": "grains",
    },
    "cooking_oil": {
        "instamart_product_id": "IM_016",
        "product_name": "Fortune Sunflower Oil",
        "pack_size": 1000,"unit": "ml",  "price": 138, "category": "oils",
    },
    "potato": {
        "instamart_product_id": "IM_017",
        "product_name": "Fresh Potatoes",
        "pack_size": 1000,"unit": "g",   "price": 38,  "category": "vegetables",
    },
    "capsicum": {
        "instamart_product_id": "IM_018",
        "product_name": "Fresh Capsicum (Green Bell Pepper)",
        "pack_size": 500, "unit": "g",   "price": 42,  "category": "vegetables",
    },
    "biryani_spice_mix": {
        "instamart_product_id": "IM_019",
        "product_name": "MDH Biryani Masala",
        "pack_size": 50,  "unit": "g",   "price": 38,  "category": "spices",
    },
    "yeast": {
        "instamart_product_id": "IM_020",
        "product_name": "Instant Dry Yeast",
        "pack_size": 100, "unit": "g",   "price": 58,  "category": "baking",
    },
    "mixed_vegetables": {
        "instamart_product_id": "IM_021",
        "product_name": "Fresh Mixed Vegetables",
        "pack_size": 500, "unit": "g",   "price": 48,  "category": "vegetables",
    },
}

# ─── Demand seasonality ────────────────────────────────────────────────────────

# weekday() → 0=Mon … 6=Sun
WEEKLY_MULTIPLIERS: dict[int, float] = {
    0: 0.75,   # Monday    — slowest
    1: 0.80,   # Tuesday
    2: 0.85,   # Wednesday
    3: 0.90,   # Thursday
    4: 1.20,   # Friday
    5: 1.45,   # Saturday  — peak
    6: 1.35,   # Sunday
}

# Indian festivals + demand spikes for the synthetic data window
FESTIVAL_DATES: dict[date, tuple[str, float]] = {
    # 2025
    date(2025,  1,  1): ("New Year",             1.65),
    date(2025,  1, 26): ("Republic Day",          1.30),
    date(2025,  2, 14): ("Valentines Day",        1.35),
    date(2025,  3, 14): ("Holi",                  1.55),
    date(2025,  3, 15): ("Holi Day 2",            1.35),
    date(2025,  3, 30): ("Ugadi",                 1.30),
    date(2025,  3, 31): ("Eid ul-Fitr",           1.50),
    date(2025,  4,  6): ("Ram Navami",            1.20),
    date(2025,  4, 14): ("Baisakhi",              1.25),
    date(2025,  8, 15): ("Independence Day",      1.40),
    date(2025,  8, 27): ("Ganesh Chaturthi",      1.35),
    date(2025, 10,  2): ("Gandhi Jayanti",        1.25),
    date(2025, 10, 20): ("Dussehra",              1.45),
    date(2025, 10, 29): ("Diwali Eve",            1.60),
    date(2025, 10, 30): ("Diwali",                1.90),
    date(2025, 10, 31): ("Diwali Day 2",          1.70),
    date(2025, 11,  5): ("Chhath Puja",           1.20),
    date(2025, 12, 25): ("Christmas",             1.40),
    date(2025, 12, 31): ("New Year Eve",          1.75),
    # 2026
    date(2026,  1,  1): ("New Year 2026",         1.65),
    date(2026,  1, 26): ("Republic Day 2026",     1.30),
    date(2026,  2, 14): ("Valentines Day 2026",   1.35),
    date(2026,  3,  2): ("Holi 2026",             1.55),
    date(2026,  3, 20): ("Eid ul-Fitr 2026",      1.50),
    date(2026,  5, 13): ("Eid ul-Adha 2026",      1.40),
}

# Monthly growth rate (compound): ~18% annual → 1.4% per month
MONTHLY_GROWTH_RATE = 0.014
