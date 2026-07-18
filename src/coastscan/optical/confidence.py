"""Evidence confidence kept explicitly separate from optical clarity."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceResult:
    confidence: str
    quality_flag: str
    reasons: tuple[str, ...]


def classify_confidence(
    *,
    valid_scenes: int,
    valid_years: int,
    valid_months: int,
    valid_observation_share: float,
    mean_mask_burden: float,
    minimum_scenes: int,
    minimum_months: int,
) -> ConfidenceResult:
    reasons: list[str] = []
    if valid_scenes < minimum_scenes:
        reasons.append("too_few_valid_scenes")
    if valid_months < minimum_months:
        reasons.append("too_few_valid_months")
    if valid_years < 2:
        reasons.append("single_year_only")
    if valid_observation_share < 0.25:
        reasons.append("low_valid_observation_share")
    if mean_mask_burden > 0.6:
        reasons.append("high_mask_burden")
    if valid_scenes == 0:
        return ConfidenceResult(
            "insufficient", "insufficient", tuple(reasons or ["no_valid_observations"])
        )
    if reasons:
        confidence = "low" if len(reasons) >= 2 else "medium"
        return ConfidenceResult(confidence, "limited", tuple(reasons))
    if valid_scenes >= minimum_scenes * 2 and valid_years >= 4 and valid_observation_share >= 0.6:
        return ConfidenceResult("high", "usable", ())
    return ConfidenceResult("medium", "usable", ())
