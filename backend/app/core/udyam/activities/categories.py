
from __future__ import annotations
from typing import Optional
from ..models import ActivityRecord


BUSINESS_CATEGORIES = {
    "FOOD_RETAIL": {
        "label": "Food & Grocery Retail",
        "description": "Retail sale of food items, grocery stores, kirana shops",
    },
    "GENERAL_RETAIL": {
        "label": "General Retail Trade",
        "description": "Non-food retail, hardware, stationery, electronics",
    },
    "TAILORING": {
        "label": "Tailoring & Garments",
        "description": "Custom tailoring, garment manufacturing, alterations",
    },
    "DAIRY": {
        "label": "Dairy & Milk Products",
        "description": "Dairy farming, milk processing, chilling, ghee, paneer",
    },
    "POULTRY": {
        "label": "Poultry Farming",
        "description": "Poultry rearing, egg production, hatcheries",
    },
    "GOAT_SHEEP": {
        "label": "Goat & Sheep Rearing",
        "description": "Goat farming, sheep rearing, wool",
    },
    "TRANSPORT": {
        "label": "Transport Services",
        "description": "Goods transport, passenger transport, auto-rickshaw",
    },
    "CONSTRUCTION": {
        "label": "Construction & Building",
        "description": "Construction contracting, masonry, building materials",
    },
    "FABRICATION": {
        "label": "Metal Fabrication & Welding",
        "description": "Steel fabrication, welding, metalworks, structural steel",
    },
    "MANUFACTURING": {
        "label": "General Manufacturing",
        "description": "Manufacturing activities not covered by specific categories",
    },
    "FOOD_PROCESSING": {
        "label": "Food Processing & Agro",
        "description": "Food processing, flour milling, oil pressing, pickle making",
    },
    "SERVICES": {
        "label": "General Services",
        "description": "Service activities including repair, maintenance, consulting",
    },
    "AGRICULTURE_SUPPORT": {
        "label": "Agriculture Support Services",
        "description": "Farm equipment rental, seed supply, fertilizer, irrigation",
    },
    "BEAUTY_WELLNESS": {
        "label": "Beauty & Wellness",
        "description": "Beauty parlour, salon, spa, wellness services",
    },
    "EDUCATION_TRAINING": {
        "label": "Education & Training",
        "description": "Tuition, coaching, skill training, computer education",
    },
    "HEALTHCARE": {
        "label": "Healthcare Services",
        "description": "Medical practice, pharmacy, diagnostics, dental",
    },
    "UNKNOWN": {
        "label": "Unclassified",
        "description": "Category could not be determined with sufficient confidence",
    },
}


NIC_2DIGIT_TO_CATEGORY = {
    "01": "AGRICULTURE_SUPPORT",
    "02": "AGRICULTURE_SUPPORT",
    "03": "AGRICULTURE_SUPPORT",
    "10": "FOOD_PROCESSING",
    "11": "FOOD_PROCESSING",
    "13": "TAILORING",
    "14": "TAILORING",
    "15": "MANUFACTURING",
    "16": "MANUFACTURING",
    "17": "MANUFACTURING",
    "20": "MANUFACTURING",
    "22": "MANUFACTURING",
    "23": "CONSTRUCTION",
    "24": "FABRICATION",
    "25": "FABRICATION",
    "28": "MANUFACTURING",
    "29": "TRANSPORT",
    "31": "MANUFACTURING",
    "33": "SERVICES",
    "41": "CONSTRUCTION",
    "42": "CONSTRUCTION",
    "43": "CONSTRUCTION",
    "45": "GENERAL_RETAIL",
    "46": "GENERAL_RETAIL",
    "47": "FOOD_RETAIL",
    "49": "TRANSPORT",
    "50": "TRANSPORT",
    "56": "FOOD_PROCESSING",
    "62": "SERVICES",
    "68": "SERVICES",
    "85": "EDUCATION_TRAINING",
    "86": "HEALTHCARE",
    "95": "SERVICES",
    "96": "BEAUTY_WELLNESS",
}

NIC_SPECIFIC_TO_CATEGORY = {
    "0141": "DAIRY",
    "0143": "GOAT_SHEEP",
    "0144": "GOAT_SHEEP",
    "0145": "GOAT_SHEEP",
    "0146": "POULTRY",
    "1050": "DAIRY",
    "105": "DAIRY",
    "251": "FABRICATION",
    "106": "FOOD_PROCESSING",
    "107": "FOOD_PROCESSING",
    "141": "TAILORING",
    "4711": "FOOD_RETAIL",
    "4719": "GENERAL_RETAIL",
    "472": "FOOD_RETAIL",
    "475": "GENERAL_RETAIL",
    "476": "GENERAL_RETAIL",
    "4761": "GENERAL_RETAIL",
    "477": "GENERAL_RETAIL",
    "4771": "GENERAL_RETAIL",
    "4773": "HEALTHCARE",
    "478": "FOOD_RETAIL",
    "479": "GENERAL_RETAIL",
    "9602": "BEAUTY_WELLNESS",
}


_KEYWORD_RULES = [
    (["DAIRY", "MILK", "GHEE", "PANEER", "CURD", "CHEESE", "CREAMERY"], "DAIRY"),
    (["POULTRY", "CHICKEN", "EGG", "HATCHERY", "BROILER"], "POULTRY"),
    (["GOAT", "SHEEP", "MUTTON", "WOOL"], "GOAT_SHEEP"),
    (["TAILOR", "GARMENT", "STITCH", "BOUTIQUE", "APPAREL", "READYMADE"], "TAILORING"),
    (["FABRICAT", "WELD", "STEEL", "METAL", "IRON", "ALUMINIUM"], "FABRICATION"),
    (["FLOUR", "MILL", "ATTA", "CHAKKI", "OIL MILL", "RICE MILL", "DAL MILL"], "FOOD_PROCESSING"),
    (["GROCERY", "KIRANA", "PROVISION", "GENERAL STORE"], "FOOD_RETAIL"),
    (["HARDWARE", "ELECTRIC", "STATIONAR", "MOBILE", "COMPUTER"], "GENERAL_RETAIL"),
    (["TRANSPORT", "TRUCK", "AUTO", "RICKSHAW", "TAXI", "LOGISTICS"], "TRANSPORT"),
    (["CONSTRUCT", "BUILDING", "MASON", "BRICK", "CEMENT", "CONCRETE"], "CONSTRUCTION"),
    (["SALON", "PARLOUR", "PARLOR", "BEAUTY", "BARBER", "MEHNDI"], "BEAUTY_WELLNESS"),
    (["SCHOOL", "TUITION", "COACHING", "TRAINING", "EDUCATION"], "EDUCATION_TRAINING"),
    (["MEDICAL", "CLINIC", "HOSPITAL", "PHARMACY", "DOCTOR", "DENTAL"], "HEALTHCARE"),
    (["REPAIR", "SERVICE", "MAINTENANCE", "WORKSHOP"], "SERVICES"),
    (["FARM", "SEED", "FERTILIZ", "PESTICIDE", "TRACTOR", "IRRIGATION"], "AGRICULTURE_SUPPORT"),
]


def map_nic_to_category(
    nic_code: str,
    activity_description: str = "",
) -> tuple[str, str, str]:
    if nic_code:
        for prefix_len in [4, 3]:
            prefix = nic_code[:prefix_len]
            if prefix in NIC_SPECIFIC_TO_CATEGORY:
                return (
                    NIC_SPECIFIC_TO_CATEGORY[prefix],
                    "HIGH",
                    f"nic_specific_{prefix}",
                )

        nic_2 = nic_code[:2]
        if nic_2 in NIC_2DIGIT_TO_CATEGORY:
            return (
                NIC_2DIGIT_TO_CATEGORY[nic_2],
                "HIGH",
                f"nic_2digit_{nic_2}",
            )

    if activity_description:
        desc_upper = activity_description.upper()
        for keywords, category in _KEYWORD_RULES:
            for kw in keywords:
                if kw in desc_upper:
                    return (
                        category,
                        "MEDIUM",
                        f"keyword_match_{kw.lower()}",
                    )

    return ("UNKNOWN", "UNKNOWN", "no_match")


def enrich_activity_record(record: ActivityRecord) -> ActivityRecord:
    category, confidence, source = map_nic_to_category(
        nic_code=record.nic_code,
        activity_description=record.activity_description,
    )
    record.normalized_category = category
    record.category_confidence = confidence
    record.validation_source = source
    return record


def get_category_label(category_code: str) -> str:
    cat = BUSINESS_CATEGORIES.get(category_code)
    if cat:
        return cat["label"]
    return category_code


def is_potential_competitor(
    category_a: str,
    category_b: str,
) -> bool:
    if category_a == "UNKNOWN" or category_b == "UNKNOWN":
        return False
    return category_a == category_b


def is_potential_supply_chain(
    category_a: str,
    category_b: str,
) -> bool:
    supply_chain_pairs = {
        frozenset({"DAIRY", "FOOD_RETAIL"}),
        frozenset({"DAIRY", "FOOD_PROCESSING"}),
        frozenset({"AGRICULTURE_SUPPORT", "DAIRY"}),
        frozenset({"AGRICULTURE_SUPPORT", "FOOD_PROCESSING"}),
        frozenset({"AGRICULTURE_SUPPORT", "GOAT_SHEEP"}),
        frozenset({"AGRICULTURE_SUPPORT", "POULTRY"}),
        frozenset({"FABRICATION", "CONSTRUCTION"}),
        frozenset({"MANUFACTURING", "GENERAL_RETAIL"}),
        frozenset({"FOOD_PROCESSING", "FOOD_RETAIL"}),
        frozenset({"TAILORING", "GENERAL_RETAIL"}),
    }
    pair = frozenset({category_a, category_b})
    return pair in supply_chain_pairs
