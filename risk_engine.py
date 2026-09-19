"""Risk scoring for the Industrial Safety AI prototype."""
from dataclasses import dataclass

@dataclass
class RiskResult:
    score: int
    level: str
    reasons: list[str]

def severity(score: int) -> str:
    if score <= 30: return "LOW"
    if score <= 70: return "MEDIUM"
    if score <= 94: return "HIGH"
    return "CRITICAL"

def calculate_risk(*, restricted: int, aerosol: bool, fire: bool, fall: bool,
                   helmet_not_verified: int, workers: int,
                   gas: float, temperature: float, pressure: float,
                   equipment: float) -> RiskResult:
    score, reasons = 0, []
    rules = [
        (restricted > 0, 15, "Restricted-zone entry"),
        (aerosol, 18, "Aerosol/smoke-like event"),
        # Fire is intentionally weighted heavily: an active fire must dominate the risk score.
        (fire, 65, "Fire-like visual"),
        (fall, 28, "Possible fall"),
        (helmet_not_verified > 0, 10, "Cap not verified"),
        (gas >= 70, 24, "High gas reading"),
        (gas >= 35, 12, "Elevated gas reading"),
        (temperature >= 90, 15, "High temperature"),
        (temperature >= 65, 7, "Elevated temperature"),
        (pressure >= 8, 18, "High pressure"),
        (pressure >= 6.5, 9, "Elevated pressure"),
        (equipment >= 70, 10, "Equipment anomaly"),
        (workers >= 3 and (aerosol or fire or gas >= 50), 8, "Multiple workers exposed"),
    ]
    for condition, points, reason in rules:
        if condition:
            score += points; reasons.append(reason)
    score = min(100, int(score))
    return RiskResult(score, severity(score), reasons)
