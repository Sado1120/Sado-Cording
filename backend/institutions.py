"""Institutional alert helpers for credible market guidance."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple


@dataclass(frozen=True)
class InstitutionalProfile:
    """Represents a credible institution and alert heuristics."""

    name: str
    aliases: Sequence[str]
    weight: float
    caution_keywords: Sequence[str]
    reassurance_keywords: Sequence[str]


# Profiles draw on well-known policy institutions that routinely publish
# market-relevant assessments. The keywords focus on volatility and liquidity
# language they commonly use in public releases (e.g. BIS Quarterly Review,
# IMF Global Financial Stability Report, 한국은행 금융안정보고서).
INSTITUTION_PROFILES: Tuple[InstitutionalProfile, ...] = (
    InstitutionalProfile(
        name="한국은행 금융안정보고서",
        aliases=("한국은행", "Bank of Korea", "BOK"),
        weight=0.28,
        caution_keywords=("위험", "경고", "불안", "변동성", "긴축"),
        reassurance_keywords=("안정", "완화", "개선", "완화"),
    ),
    InstitutionalProfile(
        name="BIS Quarterly Review",
        aliases=("BIS", "Bank for International Settlements"),
        weight=0.26,
        caution_keywords=("leverag", "stress", "sell-off", "liquidity", "shock"),
        reassurance_keywords=("resilien", "improv", "stabil"),
    ),
    InstitutionalProfile(
        name="IMF Global Financial Stability Report",
        aliases=("IMF", "International Monetary Fund"),
        weight=0.22,
        caution_keywords=("risk", "volatility", "tightening", "outflow", "shock"),
        reassurance_keywords=("缓解", "stabil", "recovery"),
    ),
    InstitutionalProfile(
        name="FSB Market Monitoring",
        aliases=("금융안정위원회", "Financial Stability Board", "FSB"),
        weight=0.14,
        caution_keywords=("vulnerab", "cyber", "stress", "disruption"),
        reassurance_keywords=("coordination", "mitigat"),
    ),
    InstitutionalProfile(
        name="SEC Market Risk Alert",
        aliases=("미국 증권거래위원회", "SEC", "Securities and Exchange Commission"),
        weight=0.1,
        caution_keywords=("enforcement", "fraud", "halt", "suspension"),
        reassurance_keywords=("approval", "greenlight", "reopen"),
    ),
)


def _normalise_text(value: str | None) -> str:
    if not value:
        return ""
    return value.lower()


def evaluate_institutional_alerts(
    news_feed: Iterable[dict],
) -> Tuple[float, List[str], List[str]]:
    """Return (alert_level, alerts, sources) derived from credible briefings.

    Alert level is scaled 0-100. Alerts contains Korean summaries referencing
    the institutions that triggered the signal. Sources captures the canonical
    institution names for downstream display.
    """

    total_score = 0.0
    alerts: List[str] = []
    matched_sources: List[str] = []

    for item in news_feed:
        title = _normalise_text(item.get("title"))
        summary = _normalise_text(item.get("summary"))
        source = _normalise_text(item.get("source"))
        combined = " ".join(value for value in (title, summary, source) if value)
        if not combined:
            continue

        for profile in INSTITUTION_PROFILES:
            if not any(alias.lower() in combined for alias in profile.aliases):
                continue

            matched_sources.append(profile.name)
            severity = profile.weight * 100.0

            caution_hit = any(keyword in combined for keyword in profile.caution_keywords)
            reassurance_hit = any(keyword in combined for keyword in profile.reassurance_keywords)

            if caution_hit:
                severity *= 1.35
                alert_label = f"{profile.name}: 변동성 경보 - 최신 발표에서 위험을 경고했습니다."
                alerts.append(alert_label)
            elif reassurance_hit:
                severity *= 0.4
                alert_label = f"{profile.name}: 완화 신호 - 위험 평가가 완화되고 있습니다."
                alerts.append(alert_label)
            else:
                alert_label = f"{profile.name}: 동향 점검 필요"
                alerts.append(alert_label)

            total_score += severity

    if not alerts:
        return 0.0, [], []

    capped_score = min(100.0, total_score)
    # Deduplicate alerts while preserving order
    deduped_alerts: List[str] = []
    seen = set()
    for alert in alerts:
        if alert not in seen:
            deduped_alerts.append(alert)
            seen.add(alert)

    deduped_sources: List[str] = []
    for source_name in matched_sources:
        if source_name not in deduped_sources:
            deduped_sources.append(source_name)

    return capped_score, deduped_alerts, deduped_sources


__all__ = ["evaluate_institutional_alerts", "INSTITUTION_PROFILES"]
