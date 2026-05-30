from __future__ import annotations


def compute_roe(net_income: float | None, total_equity: float | None) -> float | None:
    if net_income is None or total_equity in (None, 0):
        return None
    return float(net_income / total_equity)


def compute_debt_ratio(total_liabilities: float | None, total_equity: float | None) -> float | None:
    if total_liabilities is None or total_equity in (None, 0):
        return None
    return float(total_liabilities / total_equity)


def compute_per(price: float | None, eps: float | None) -> float | None:
    if price is None or price <= 0 or eps is None or eps <= 0:
        return None
    return float(price / eps)


def compute_pbr(price: float | None, bps: float | None) -> float | None:
    if price is None or price <= 0 or bps is None or bps <= 0:
        return None
    return float(price / bps)


def normalize_positive_ratio(value: float | None) -> float | None:
    if value is None or value <= 0:
        return None
    return float(value)
