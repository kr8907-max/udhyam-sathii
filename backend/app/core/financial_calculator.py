
from __future__ import annotations
import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
def _resolve_schemes_path() -> Path:
    candidates = [
        Path(__file__).parent.parent / "data" / "government_schemes.json",
        Path(__file__).parent / "government_schemes.json",
        Path.cwd() / "backend" / "app" / "data" / "government_schemes.json",
        Path.cwd() / "government_schemes.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]

_SCHEMES_PATH = _resolve_schemes_path()

WORKING_CAPITAL_TURNOVER_RATIO = {
    "dairy": 1.0 / 12,
    "food_processing": 1.5 / 12,
    "manufacturing": 2.0 / 12,
    "fabrication": 2.5 / 12,
    "service": 1.0 / 12,
    "repair": 1.0 / 12,
    "trading": 1.5 / 12,
    "apparel": 2.0 / 12,
    "artisan": 1.5 / 12,
    "default": 1.5 / 12,
}

DSCR_BENCHMARK = 1.33
DSCR_MARGINAL_FLOOR = 1.00


@dataclass
class AmortizationResult:
    principal: float
    annual_rate_pct: float
    tenure_years: float
    moratorium_months: int
    repayment_months: int
    monthly_emi: float
    moratorium_interest: float
    total_interest_payable: float
    total_repayment: float

    def to_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass
class WorkingCapitalResult:
    sector: str
    annual_turnover: float
    turnover_ratio_months: float
    working_capital_required: float
    monthly_working_capital_outlay: float

    def to_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass
class DSCRResult:
    monthly_net_operating_income: float
    monthly_emi: float
    dscr: float
    verdict: str

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass
class SchemeRanking:
    rank: int
    scheme_id: str
    full_name: str
    eligible: bool
    ineligibility_reason: Optional[str]
    subsidy_grant_amount: float
    effective_interest_rate_pct: float
    total_interest_payable: float
    net_financial_benefit: float
    collateral_free: bool
    notes: str
    official_url: Optional[str] = None
    benefit_type: str = "CAPITAL_GRANT"
    interest_savings_amount: float = 0.0
    benefit_summary: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("subsidy_grant_amount", "effective_interest_rate_pct", "total_interest_payable", "net_financial_benefit", "interest_savings_amount"):
            d[k] = round(d[k], 2)
        return d


_CACHED_SCHEMES: Optional[list[dict]] = None


def _load_schemes() -> list[dict]:
    global _CACHED_SCHEMES
    if _CACHED_SCHEMES is None:
        with open(_SCHEMES_PATH, "r", encoding="utf-8") as f:
            _CACHED_SCHEMES = json.load(f)["schemes"]
    return _CACHED_SCHEMES


def emi_with_moratorium(
    principal: float,
    annual_rate_pct: float,
    tenure_years: float,
    moratorium_months: int = 0,
) -> AmortizationResult:
    if principal <= 0:
        raise ValueError("principal must be > 0")
    if tenure_years <= 0:
        raise ValueError("tenure_years must be > 0")
    if moratorium_months < 0:
        raise ValueError("moratorium_months cannot be negative")

    total_months = round(tenure_years * 12)
    if moratorium_months >= total_months:
        raise ValueError("moratorium_months must be less than total tenure in months")

    r_m = (annual_rate_pct / 100.0) / 12.0
    n = total_months - moratorium_months

    if r_m == 0:
        emi = principal / n
    else:
        factor = (1 + r_m) ** n
        emi = principal * r_m * factor / (factor - 1)

    moratorium_interest = principal * r_m * moratorium_months
    total_interest = moratorium_interest + (emi * n - principal)
    total_repayment = principal + total_interest

    return AmortizationResult(
        principal=principal,
        annual_rate_pct=annual_rate_pct,
        tenure_years=tenure_years,
        moratorium_months=moratorium_months,
        repayment_months=n,
        monthly_emi=emi,
        moratorium_interest=moratorium_interest,
        total_interest_payable=total_interest,
        total_repayment=total_repayment,
    )


def working_capital_estimate(annual_turnover: float, sector: str) -> WorkingCapitalResult:
    if annual_turnover < 0:
        raise ValueError("annual_turnover cannot be negative")
    ratio = WORKING_CAPITAL_TURNOVER_RATIO.get(sector.lower(), WORKING_CAPITAL_TURNOVER_RATIO["default"])
    wc_required = annual_turnover * ratio
    return WorkingCapitalResult(
        sector=sector,
        annual_turnover=annual_turnover,
        turnover_ratio_months=round(ratio * 12, 2),
        working_capital_required=wc_required,
        monthly_working_capital_outlay=wc_required,
    )


def compute_dscr(monthly_net_operating_income: float, monthly_emi: float) -> DSCRResult:
    if monthly_emi <= 0:
        raise ValueError("monthly_emi must be > 0")
    dscr = monthly_net_operating_income / monthly_emi
    if dscr >= DSCR_BENCHMARK:
        verdict = "VIABLE"
    elif dscr >= DSCR_MARGINAL_FLOOR:
        verdict = "MARGINAL"
    else:
        verdict = "AT RISK"
    return DSCRResult(
        monthly_net_operating_income=monthly_net_operating_income,
        monthly_emi=monthly_emi,
        dscr=dscr,
        verdict=verdict,
    )


def _category_is_special(promoter_category: str) -> bool:
    return promoter_category.lower() not in ("general",)


def rank_eligible_schemes(
    project_cost: float,
    business_category: str,
    sector: str,
    promoter_category: str,
    is_rural: bool,
    tenure_years: float = 5.0,
    default_bank_interest_rate_pct: float = 11.0,
    moratorium_months: int = 6,
    schemes: Optional[list[dict]] = None,
) -> list[SchemeRanking]:
    if schemes is None:
        schemes = _load_schemes()

    business_category = business_category.lower()
    sector = sector.lower()
    promoter_category = promoter_category.lower()
    results: list[SchemeRanking] = []

    for s in schemes:
        eligible = True
        reasons = []

        target_sectors = [t.lower() for t in s["target_sectors"]]
        if "all" not in target_sectors and sector not in target_sectors:
            eligible = False
            reasons.append(f"sector '{sector}' not in scheme's target sectors {target_sectors}")

        elig_categories = [c.lower() for c in s["eligible_categories"]]
        if promoter_category not in elig_categories and "general" not in elig_categories:
            eligible = False
            reasons.append(f"promoter category '{promoter_category}' not eligible (scheme restricted to {elig_categories})")
        elif promoter_category not in elig_categories and elig_categories == ["general"]:
            eligible = False
            reasons.append("scheme open to 'general' only")

        max_cost = s["max_project_cost"].get(business_category, s["max_project_cost"].get("manufacturing"))
        if project_cost > max_cost:
            eligible = False
            reasons.append(f"project cost ₹{project_cost:,.0f} exceeds scheme ceiling ₹{max_cost:,.0f}")
        min_cost = s.get("min_project_cost")
        if min_cost and project_cost < min_cost:
            eligible = False
            reasons.append(f"project cost ₹{project_cost:,.0f} below scheme minimum ₹{min_cost:,.0f}")

        slabs = s["subsidy_slabs"]
        raw_subsidy = 0.0
        pct = 0.0
        if "flat_pct" in slabs and slabs["flat_pct"] > 0:
            pct = slabs["flat_pct"]
            raw_subsidy = project_cost * pct
            cap = slabs.get("cap_amount", 0)
            if cap:
                raw_subsidy = min(raw_subsidy, cap)
        elif "general_urban_pct" in slabs:
            special = _category_is_special(promoter_category)
            if special and is_rural:
                pct = slabs["special_rural_pct"]
            elif special and not is_rural:
                pct = slabs["special_urban_pct"]
            elif not special and is_rural:
                pct = slabs["general_rural_pct"]
            else:
                pct = slabs["general_urban_pct"]
            raw_subsidy = project_cost * pct

        actual_subsidy = raw_subsidy if eligible else 0.0

        subvention = s.get("interest_subvention_pct", 0.0)
        if "fixed_interest_rate_pct" in s:
            eff_rate = s["fixed_interest_rate_pct"] * 100
        elif "effective_interest_rate_pct" in s:
            eff_rate = s["effective_interest_rate_pct"] * 100
        else:
            eff_rate = max(default_bank_interest_rate_pct - (subvention * 100), 0.0)

        if "composite_loan_coverage_pct" in s:
            loan_principal = project_cost * s["composite_loan_coverage_pct"]
        else:
            promoter_pct = s["promoter_contribution_pct"].get(
                "special" if _category_is_special(promoter_category) else "general", 0.10
            )
            loan_principal = max(project_cost - actual_subsidy - (project_cost * promoter_pct), 0.0)

        total_interest = 0.0
        base_interest = 0.0
        if loan_principal > 0:
            try:
                amort = emi_with_moratorium(loan_principal, eff_rate, tenure_years, moratorium_months)
                total_interest = amort.total_interest_payable
            except ValueError:
                total_interest = 0.0
            
            try:
                base_amort = emi_with_moratorium(loan_principal, default_bank_interest_rate_pct, tenure_years, moratorium_months)
                base_interest = base_amort.total_interest_payable
            except ValueError:
                base_interest = total_interest

        interest_savings = max(base_interest - total_interest, 0.0)

        if not eligible:
            benefit_type = "INELIGIBLE"
            benefit_summary = f"₹0 (Ineligible)"
            net_benefit = -999999.0
        elif actual_subsidy > 0:
            benefit_type = "CAPITAL_GRANT"
            benefit_summary = f"₹{actual_subsidy:,.0f} Direct Capital Grant ({pct*100:.0f}%)"
            net_benefit = actual_subsidy + interest_savings - total_interest
        elif (subvention > 0 or eff_rate < default_bank_interest_rate_pct):
            benefit_type = "INTEREST_SUBVENTION"
            subv_diff = default_bank_interest_rate_pct - eff_rate
            benefit_summary = f"{subv_diff:.1f}% p.a. Interest Subvention (₹{interest_savings:,.0f} Saved)"
            net_benefit = interest_savings - total_interest
        else:
            benefit_type = "CONCESSIONAL_CREDIT"
            benefit_summary = "100% Collateral-Free Refinanced Loan"
            net_benefit = -total_interest

        results.append(
            SchemeRanking(
                rank=0,
                scheme_id=s["scheme_id"],
                full_name=s["full_name"],
                eligible=eligible,
                ineligibility_reason="; ".join(reasons) if reasons else None,
                subsidy_grant_amount=actual_subsidy,
                effective_interest_rate_pct=eff_rate,
                total_interest_payable=total_interest,
                net_financial_benefit=net_benefit,
                collateral_free=(s.get("collateral_free_limit", 0) >= loan_principal) if loan_principal else True,
                notes=s.get("notes", ""),
                official_url=s.get("official_url"),
                benefit_type=benefit_type,
                interest_savings_amount=interest_savings,
                benefit_summary=benefit_summary,
            )
        )

    eligible_sorted = sorted([r for r in results if r.eligible], key=lambda r: r.net_financial_benefit, reverse=True)
    ineligible_sorted = sorted([r for r in results if not r.eligible], key=lambda r: r.net_financial_benefit, reverse=True)

    ordered = eligible_sorted + ineligible_sorted
    for i, r in enumerate(ordered, start=1):
        r.rank = i

    return ordered


if __name__ == "__main__":
    amort = emi_with_moratorium(500000, 11.0, 5, 6)
    print(amort.to_dict())
    wc = working_capital_estimate(1200000, "dairy")
    print(wc.to_dict())
    dscr = compute_dscr(45000, 30000)
    print(dscr.to_dict())
