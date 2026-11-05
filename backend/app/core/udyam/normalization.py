
from __future__ import annotations
import re
import unicodedata


_SAFE_ABBREVIATIONS = {
    r'\bVILL\b': 'VILLAGE',
    r'\bVILL\.': 'VILLAGE',
    r'\bDIST\b': 'DISTRICT',
    r'\bDIST\.': 'DISTRICT',
    r'\bTAL\b': 'TALUKA',
    r'\bTAL\.': 'TALUKA',
    r'\bTEH\b': 'TEHSIL',
    r'\bTEH\.': 'TEHSIL',
    r'\bMDL\b': 'MANDAL',
    r'\bMDL\.': 'MANDAL',
    r'\bGP\b': 'GRAM PANCHAYAT',
    r'\bG\.P\.': 'GRAM PANCHAYAT',
    r'\bPO\b': 'POST OFFICE',
    r'\bP\.O\.': 'POST OFFICE',
    r'\bPS\b': 'POLICE STATION',
    r'\bP\.S\.': 'POLICE STATION',
    r'\bBLK\b': 'BLOCK',
    r'\bBLK\.': 'BLOCK',
    r'\bRD\b': 'ROAD',
    r'\bRD\.': 'ROAD',
    r'\bST\b': 'STREET',
    r'\bST\.': 'STREET',
    r'\bNR\b': 'NEAR',
    r'\bNR\.': 'NEAR',
    r'\bOPP\b': 'OPPOSITE',
    r'\bOPP\.': 'OPPOSITE',
    r'\bINDL\b': 'INDUSTRIAL',
    r'\bINDL\.': 'INDUSTRIAL',
}


def normalize_text_basic(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.strip()
    text = text.upper()
    text = re.sub(r'\s+', ' ', text)
    return text


def normalize_address(address: str) -> str:
    if not address:
        return ""

    addr = unicodedata.normalize("NFKD", address)
    addr = addr.upper()

    addr = re.sub(r'["\'\`\|]', ' ', addr)
    addr = re.sub(r'[\[\]\(\)\{\}]', ' ', addr)

    addr = re.sub(r'[,;]', ' ', addr)
    addr = re.sub(r'(?<=[A-Z])\s*/\s*(?=[A-Z])', ' ', addr)

    for pattern, expansion in _SAFE_ABBREVIATIONS.items():
        addr = re.sub(pattern, expansion, addr)

    addr = re.sub(r'(?<![0-9])\.(?![0-9])', ' ', addr)

    addr = re.sub(r'\s+', ' ', addr).strip()

    return addr


def normalize_pincode(pincode: Any) -> str:
    if pincode is None:
        return ""
    raw = str(pincode).strip()
    if not raw:
        return ""
    if "." in raw:
        raw = raw.split(".")[0].strip()
    cleaned = re.sub(r'\D', '', raw)
    if len(cleaned) == 6:
        return cleaned
    return ""


def normalize_state(state: str) -> str:
    if not state:
        return ""
    s = normalize_text_basic(state)

    _STATE_ALIASES = {
        "ANDHRA PRADESH": "ANDHRA PRADESH",
        "AP": "ANDHRA PRADESH",
        "ARUNACHAL PRADESH": "ARUNACHAL PRADESH",
        "ASSAM": "ASSAM",
        "BIHAR": "BIHAR",
        "CHHATTISGARH": "CHHATTISGARH",
        "CHATTISGARH": "CHHATTISGARH",
        "DELHI": "DELHI",
        "NCT OF DELHI": "DELHI",
        "GOA": "GOA",
        "GUJARAT": "GUJARAT",
        "HARYANA": "HARYANA",
        "HIMACHAL PRADESH": "HIMACHAL PRADESH",
        "HP": "HIMACHAL PRADESH",
        "JAMMU AND KASHMIR": "JAMMU AND KASHMIR",
        "JAMMU & KASHMIR": "JAMMU AND KASHMIR",
        "J&K": "JAMMU AND KASHMIR",
        "JHARKHAND": "JHARKHAND",
        "KARNATAKA": "KARNATAKA",
        "KERALA": "KERALA",
        "MADHYA PRADESH": "MADHYA PRADESH",
        "MP": "MADHYA PRADESH",
        "MAHARASHTRA": "MAHARASHTRA",
        "MANIPUR": "MANIPUR",
        "MEGHALAYA": "MEGHALAYA",
        "MIZORAM": "MIZORAM",
        "NAGALAND": "NAGALAND",
        "ODISHA": "ODISHA",
        "ORISSA": "ODISHA",
        "PUNJAB": "PUNJAB",
        "RAJASTHAN": "RAJASTHAN",
        "SIKKIM": "SIKKIM",
        "TAMIL NADU": "TAMIL NADU",
        "TAMILNADU": "TAMIL NADU",
        "TN": "TAMIL NADU",
        "TELANGANA": "TELANGANA",
        "TRIPURA": "TRIPURA",
        "UTTAR PRADESH": "UTTAR PRADESH",
        "UP": "UTTAR PRADESH",
        "UTTARAKHAND": "UTTARAKHAND",
        "UTTARANCHAL": "UTTARAKHAND",
        "WEST BENGAL": "WEST BENGAL",
        "WB": "WEST BENGAL",
        "ANDAMAN AND NICOBAR ISLANDS": "ANDAMAN AND NICOBAR ISLANDS",
        "ANDAMAN AND NICOBAR": "ANDAMAN AND NICOBAR ISLANDS",
        "CHANDIGARH": "CHANDIGARH",
        "DADRA AND NAGAR HAVELI AND DAMAN AND DIU": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
        "DADRA AND NAGAR HAVELI": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
        "DAMAN AND DIU": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
        "LAKSHADWEEP": "LAKSHADWEEP",
        "PUDUCHERRY": "PUDUCHERRY",
        "PONDICHERRY": "PUDUCHERRY",
        "LADAKH": "LADAKH",
    }
    return _STATE_ALIASES.get(s, s)


def normalize_district(district: str) -> str:
    return normalize_text_basic(district)


def extract_tokens(normalized_address: str) -> list[str]:
    if not normalized_address:
        return []
    tokens = normalized_address.split()
    return [t for t in tokens if t]
