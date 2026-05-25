from __future__ import annotations


def compute_roe(net_income: float | None, total_equity: float | None) -> float | None:
    if net_income is None or total_equity in (None, 0):
        return None
    return float(net_income / total_equity)


def compute_debt_ratio(total_liabilities: float | None, total_equity: float | None) -> float | None:
    if total_liabilities is None or total_equity in (None, 0):
        return None
    return float(total_liabilities / total_equity)
