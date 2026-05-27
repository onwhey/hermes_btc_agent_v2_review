"""Support/resistance zone strategy for stage-23C.

This file belongs to `app/strategy/strategies`. It builds a key price map from
snapshot-restored 4h/1d Kline windows and returns support/resistance evidence.
It is called by `app/strategy/runner.py::StrategyRunner.run_strategies` through
the strategy registry.
It does not query databases, request Binance, read or write Redis, send Hermes,
call DeepSeek or any large language model, read account or position state,
generate final advice, build trade setups, modify Kline tables, or perform
trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Mapping

from app.strategy.base import BaseStrategy
from app.strategy.common.result_contract import (
    StrategyCommonResult,
    StrategyEvidenceItem,
    StrategyResult,
    StrategyRole,
)
from app.strategy.types import StrategyEvaluationInput, StrategySignalStatus

ROLE_FLIP_STATUSES = frozenset({"none", "resistance_to_support", "support_to_resistance", "unconfirmed"})
ZONE_QUALITIES = frozenset({"clear", "weak", "wide", "narrow", "noisy", "outlier", "insufficient_data", "unknown"})


@dataclass(frozen=True)
class PricePoint:
    price: Decimal
    point_type: str
    source_level_type: str
    timeframe: str
    index: int
    total_count: int
    timeframe_weight: Decimal
    reaction_strength: Decimal


@dataclass(frozen=True)
class LevelZone:
    cluster_id: str
    zone_low: Decimal
    zone_high: Decimal
    zone_mid: Decimal
    level_type: str
    strength_score: Decimal
    confidence_score: Decimal
    current_relevance_score: Decimal
    touch_count: int
    reaction_strength: Decimal
    timeframe_weight: Decimal
    recency_score: Decimal
    cluster_density: Decimal
    distance_from_current_price_pct: Decimal
    zone_width_pct: Decimal
    zone_quality: str
    role_flip_status: str
    source_points: tuple[PricePoint, ...]


class SupportResistanceStrategy(BaseStrategy):
    """Identify support/resistance zones as a price map, not a trade setup."""

    strategy_name = "support_resistance_strategy"
    strategy_version = "23C-1"
    strategy_role = StrategyRole.SUPPORT_RESISTANCE.value

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        active_config = dict(config or {})
        self.strategy_version = str(active_config.get("strategy_version", self.strategy_version))
        self.strategy_role = str(active_config.get("strategy_role", self.strategy_role))
        self.provides = tuple(
            str(item)
            for item in active_config.get(
                "provides",
                (
                    "key_levels",
                    "support_zones",
                    "resistance_zones",
                    "range_boundaries",
                    "invalidation_reference_zones",
                    "target_observation_zones",
                    "role_flip_candidates",
                ),
            )
        )
        lookback_bars = _mapping(active_config.get("lookback_bars"))
        minimum_required = _mapping(active_config.get("minimum_required_bars"))
        thresholds = _mapping(active_config.get("thresholds"))
        output_limits = _mapping(active_config.get("output_limits"))
        self.base_lookback_bars = int(lookback_bars.get("base", 180))
        self.higher_lookback_bars = int(lookback_bars.get("higher", 365))
        self.minimum_required_base_bars = int(minimum_required.get("base", 80))
        self.minimum_required_higher_bars = int(minimum_required.get("higher", 120))
        self.swing_left_bars = int(thresholds.get("swing_left_bars", 2))
        self.swing_right_bars = int(thresholds.get("swing_right_bars", 2))
        self.min_swing_move_pct = Decimal(str(thresholds.get("min_swing_move_pct", "0.004")))
        self.cluster_width_pct = Decimal(str(thresholds.get("cluster_width_pct", "0.006")))
        self.max_zone_width_pct = Decimal(str(thresholds.get("max_zone_width_pct", "0.025")))
        self.nearest_distance_pct = Decimal(str(thresholds.get("nearest_distance_pct", "0.08")))
        self.major_level_min_strength = Decimal(str(thresholds.get("major_level_min_strength", "0.60")))
        self.outlier_reaction_min_pct = Decimal(str(thresholds.get("outlier_reaction_min_pct", "0.01")))
        self.output_limits = {
            "nearest_support": int(output_limits.get("nearest_support", 3)),
            "nearest_resistance": int(output_limits.get("nearest_resistance", 3)),
            "major_support": int(output_limits.get("major_support", 5)),
            "major_resistance": int(output_limits.get("major_resistance", 5)),
            "historical_reference": int(output_limits.get("historical_reference", 5)),
            "role_flip_candidate": int(output_limits.get("role_flip_candidate", 5)),
        }

    def evaluate(self, input_data: StrategyEvaluationInput) -> StrategyResult:
        """Return support/resistance zones without confirming triggers."""

        base_rows = tuple(input_data.base_klines)
        higher_rows = tuple(input_data.higher_klines)
        if len(base_rows) < self.minimum_required_base_bars or len(higher_rows) < self.minimum_required_higher_bars:
            return self._insufficient_data_result(input_data, len(base_rows), len(higher_rows))

        latest_close = _decimal_attr(base_rows[-1], "close_price")
        points = tuple(
            _collect_price_points(
                rows=base_rows[-self.base_lookback_bars :],
                timeframe=input_data.base_interval_value,
                timeframe_weight=Decimal("1.00"),
                swing_left_bars=self.swing_left_bars,
                swing_right_bars=self.swing_right_bars,
                min_swing_move_pct=self.min_swing_move_pct,
            )
            + _collect_price_points(
                rows=higher_rows[-self.higher_lookback_bars :],
                timeframe=input_data.higher_interval_value,
                timeframe_weight=Decimal("1.30"),
                swing_left_bars=self.swing_left_bars,
                swing_right_bars=self.swing_right_bars,
                min_swing_move_pct=self.min_swing_move_pct,
            )
        )
        zones = tuple(
            _score_cluster(index, cluster, current_price=latest_close, strategy=self)
            for index, cluster in enumerate(_cluster_points(points, current_price=latest_close, width_pct=self.cluster_width_pct), start=1)
        )
        selected = _select_key_levels(zones, current_price=latest_close, strategy=self)
        key_levels = tuple(_level_to_common_payload(item, index) for index, item in enumerate(selected, start=1))
        if not key_levels:
            return self._insufficient_data_result(input_data, len(base_rows), len(higher_rows))

        confidence = max((zone.confidence_score for _, zone in selected), default=Decimal("0"))
        reason_text = "已识别支撑、压力、区间边界和历史参考区域；这些区域只是价格地图，不是交易建议。"
        common_result = StrategyCommonResult(
            market_bias="not_applicable",
            risk_level="not_applicable",
            signal_strength=_decimal_text(confidence),
            confidence_score=_decimal_text(confidence),
            reason_codes=("support_resistance_zones_identified", "key_levels_clustered"),
            reason_text=reason_text,
            key_levels=key_levels,
            evidence_items=(
                StrategyEvidenceItem(
                    evidence_type="support_resistance_price_map",
                    direction="not_applicable",
                    strength=_decimal_text(confidence),
                    description=reason_text,
                    source=self.strategy_name,
                ),
            ),
            observation_window={
                "base_interval_value": input_data.base_interval_value,
                "higher_interval_value": input_data.higher_interval_value,
                "base_start_open_time_ms": input_data.base_start_open_time_ms,
                "base_end_open_time_ms": input_data.base_end_open_time_ms,
                "higher_start_open_time_ms": input_data.higher_start_open_time_ms,
                "higher_end_open_time_ms": input_data.higher_end_open_time_ms,
            },
            not_trading_advice=True,
        )
        return StrategyResult(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_role=self.strategy_role,
            strategy_status=StrategySignalStatus.SUCCESS.value,
            common_result=common_result,
            strategy_model_material_json=_model_material(key_levels),
            strategy_payload_json=_private_payload(points, zones, selected, strategy=self),
            trace_id=input_data.trace_id,
        )

    def _insufficient_data_result(
        self,
        input_data: StrategyEvaluationInput,
        actual_base_count: int,
        actual_higher_count: int,
    ) -> StrategyResult:
        """Return a contract-valid invalid result when windows are too short."""

        reason_text = (
            "支撑压力识别所需 K线数量不足，暂不输出关键价格区域。"
            f"基础周期要求 {self.minimum_required_base_bars} 根，实际 {actual_base_count} 根；"
            f"高周期要求 {self.minimum_required_higher_bars} 根，实际 {actual_higher_count} 根。"
        )
        common_result = StrategyCommonResult(
            market_bias="not_applicable",
            risk_level="unknown",
            signal_strength="0",
            confidence_score="0",
            reason_codes=("insufficient_data",),
            reason_text=reason_text,
            observation_window={
                "base_interval_value": input_data.base_interval_value,
                "higher_interval_value": input_data.higher_interval_value,
                "actual_base_count": actual_base_count,
                "actual_higher_count": actual_higher_count,
            },
            not_trading_advice=True,
        )
        return StrategyResult(
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_role=self.strategy_role,
            strategy_status=StrategySignalStatus.INVALID.value,
            common_result=common_result,
            strategy_model_material_json={"summary": reason_text, "provides": self.provides},
            strategy_payload_json={
                "raw_swing_points": [],
                "merged_level_clusters": [],
                "cluster_scoring_details": [],
                "reaction_strength_details": [],
                "recency_score_details": [],
                "role_flip_detection_details": [],
                "zone_width_config": _zone_width_config(self),
                "calculation_params": _calculation_params(self),
                "excluded_outliers": [],
                "insufficient_data": {
                    "actual_base_count": actual_base_count,
                    "actual_higher_count": actual_higher_count,
                },
            },
            trace_id=input_data.trace_id,
        )


def _collect_price_points(
    *,
    rows: tuple[Any, ...],
    timeframe: str,
    timeframe_weight: Decimal,
    swing_left_bars: int,
    swing_right_bars: int,
    min_swing_move_pct: Decimal,
) -> list[PricePoint]:
    points: list[PricePoint] = []
    total_count = len(rows)
    if total_count <= swing_left_bars + swing_right_bars + 1:
        return points
    for index in range(swing_left_bars, total_count - swing_right_bars):
        local_rows = rows[index - swing_left_bars : index + swing_right_bars + 1]
        high = _decimal_attr(rows[index], "high_price")
        low = _decimal_attr(rows[index], "low_price")
        if high > max(_decimal_attr(row, "high_price") for row in local_rows if row is not rows[index]):
            if _local_move_pct(local_rows, high) >= min_swing_move_pct:
                points.append(_point(rows, index, high, "swing_high", "resistance", timeframe, timeframe_weight))
        if low < min(_decimal_attr(row, "low_price") for row in local_rows if row is not rows[index]):
            if _local_move_pct(local_rows, low) >= min_swing_move_pct:
                points.append(_point(rows, index, low, "swing_low", "support", timeframe, timeframe_weight))
    points.extend(_range_reference_points(rows, timeframe, timeframe_weight))
    return points


def _range_reference_points(rows: tuple[Any, ...], timeframe: str, timeframe_weight: Decimal) -> list[PricePoint]:
    recent_count = min(24, len(rows))
    previous_count = min(24, max(0, len(rows) - recent_count))
    recent_rows = rows[-recent_count:]
    previous_rows = rows[-recent_count - previous_count : -recent_count] if previous_count else ()
    references: list[tuple[Decimal, str, str, int]] = [
        (_max_high(recent_rows), "recent_high", "resistance", _index_of_high(rows, recent_rows)),
        (_min_low(recent_rows), "recent_low", "support", _index_of_low(rows, recent_rows)),
        (_max_high(rows), "range_high", "resistance", _index_of_high(rows, rows)),
        (_min_low(rows), "range_low", "support", _index_of_low(rows, rows)),
    ]
    if previous_rows:
        references.extend(
            [
                (_max_high(previous_rows), "previous_high", "resistance", _index_of_high(rows, previous_rows)),
                (_min_low(previous_rows), "previous_low", "support", _index_of_low(rows, previous_rows)),
            ]
        )
    return [_point(rows, index, price, point_type, source_type, timeframe, timeframe_weight) for price, point_type, source_type, index in references]


def _cluster_points(points: Iterable[PricePoint], *, current_price: Decimal, width_pct: Decimal) -> tuple[tuple[PricePoint, ...], ...]:
    sorted_points = sorted(points, key=lambda item: item.price)
    if not sorted_points:
        return ()
    max_gap = current_price * width_pct
    clusters: list[list[PricePoint]] = [[sorted_points[0]]]
    for point in sorted_points[1:]:
        active = clusters[-1]
        active_mid = sum((item.price for item in active), Decimal("0")) / Decimal(len(active))
        if abs(point.price - active_mid) <= max_gap:
            active.append(point)
        else:
            clusters.append([point])
    return tuple(tuple(cluster) for cluster in clusters)


def _score_cluster(index: int, cluster: tuple[PricePoint, ...], *, current_price: Decimal, strategy: SupportResistanceStrategy) -> LevelZone:
    zone_low = min(point.price for point in cluster)
    zone_high = max(point.price for point in cluster)
    zone_mid = (zone_low + zone_high) / Decimal("2")
    touch_count = len({(point.timeframe, point.index) for point in cluster})
    reaction_strength = max((point.reaction_strength for point in cluster), default=Decimal("0"))
    timeframe_weight = max(point.timeframe_weight for point in cluster)
    recency_score = max(_recency_score(point) for point in cluster)
    cluster_density = _clamp_unit(Decimal(touch_count) / Decimal("4"))
    distance_pct = abs(zone_mid - current_price) / current_price if current_price > 0 else Decimal("1")
    width_pct = (zone_high - zone_low) / current_price if current_price > 0 else Decimal("0")
    distance_score = _clamp_unit(Decimal("1") - (distance_pct / strategy.nearest_distance_pct))
    reaction_score = _clamp_unit(reaction_strength / strategy.outlier_reaction_min_pct)
    timeframe_score = Decimal("1") if timeframe_weight > Decimal("1.0") else Decimal("0.75")
    strength = (
        cluster_density * Decimal("0.30")
        + reaction_score * Decimal("0.25")
        + recency_score * Decimal("0.20")
        + timeframe_score * Decimal("0.15")
        + distance_score * Decimal("0.10")
    )
    quality = _zone_quality(
        touch_count=touch_count,
        reaction_strength=reaction_strength,
        width_pct=width_pct,
        strategy=strategy,
    )
    if quality == "wide":
        strength *= Decimal("0.65")
    elif quality == "outlier":
        strength *= Decimal("0.45")
    confidence = _clamp_unit(strength * (Decimal("0.70") + distance_score * Decimal("0.30")))
    relevance = _clamp_unit(distance_score * Decimal("0.70") + recency_score * Decimal("0.30"))
    return LevelZone(
        cluster_id=f"SR-CLUSTER-{index:03d}",
        zone_low=zone_low,
        zone_high=zone_high,
        zone_mid=zone_mid,
        level_type=_base_level_type(zone_mid, current_price),
        strength_score=_clamp_unit(strength),
        confidence_score=confidence,
        current_relevance_score=relevance,
        touch_count=touch_count,
        reaction_strength=reaction_strength,
        timeframe_weight=timeframe_weight,
        recency_score=recency_score,
        cluster_density=cluster_density,
        distance_from_current_price_pct=distance_pct * Decimal("100"),
        zone_width_pct=width_pct * Decimal("100"),
        zone_quality=quality,
        role_flip_status=_role_flip_status(cluster, zone_mid, current_price),
        source_points=cluster,
    )


def _select_key_levels(
    zones: tuple[LevelZone, ...],
    *,
    current_price: Decimal,
    strategy: SupportResistanceStrategy,
) -> tuple[tuple[str, LevelZone], ...]:
    non_outlier = tuple(zone for zone in zones if zone.zone_quality != "outlier")
    supports = sorted((zone for zone in non_outlier if zone.zone_mid < current_price), key=lambda item: abs(item.zone_mid - current_price))
    resistances = sorted((zone for zone in non_outlier if zone.zone_mid > current_price), key=lambda item: abs(item.zone_mid - current_price))
    selected: list[tuple[str, LevelZone]] = []
    selected.extend(("nearest_support", zone) for zone in supports[: strategy.output_limits["nearest_support"]])
    selected.extend(("nearest_resistance", zone) for zone in resistances[: strategy.output_limits["nearest_resistance"]])
    major_support = sorted(
        (zone for zone in supports if zone.strength_score >= strategy.major_level_min_strength),
        key=lambda item: item.strength_score,
        reverse=True,
    )[: strategy.output_limits["major_support"]]
    major_resistance = sorted(
        (zone for zone in resistances if zone.strength_score >= strategy.major_level_min_strength),
        key=lambda item: item.strength_score,
        reverse=True,
    )[: strategy.output_limits["major_resistance"]]
    selected.extend(("major_support", zone) for zone in major_support)
    selected.extend(("major_resistance", zone) for zone in major_resistance)
    selected.extend(_range_boundary_groups(non_outlier))
    historical = sorted(
        (zone for zone in zones if zone.current_relevance_score <= Decimal("0.45")),
        key=lambda item: item.strength_score,
        reverse=True,
    )[: strategy.output_limits["historical_reference"]]
    selected.extend(("historical_reference", zone) for zone in historical)
    role_flips = [zone for zone in non_outlier if zone.role_flip_status != "none"]
    selected.extend(("role_flip_candidate", zone) for zone in role_flips[: strategy.output_limits["role_flip_candidate"]])
    return _deduplicate_grouped_zones(tuple(selected))


def _range_boundary_groups(zones: tuple[LevelZone, ...]) -> tuple[tuple[str, LevelZone], ...]:
    upper = [zone for zone in zones if any(point.point_type == "range_high" for point in zone.source_points)]
    lower = [zone for zone in zones if any(point.point_type == "range_low" for point in zone.source_points)]
    result: list[tuple[str, LevelZone]] = []
    if upper:
        result.append(("range_upper_boundary", max(upper, key=lambda item: item.zone_mid)))
    if lower:
        result.append(("range_lower_boundary", min(lower, key=lambda item: item.zone_mid)))
    return tuple(result)


def _deduplicate_grouped_zones(items: tuple[tuple[str, LevelZone], ...]) -> tuple[tuple[str, LevelZone], ...]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, LevelZone]] = []
    for group, zone in items:
        key = (group, zone.cluster_id)
        if key in seen:
            continue
        seen.add(key)
        result.append((group, zone))
    return tuple(result)


def _level_to_common_payload(item: tuple[str, LevelZone], index: int) -> Mapping[str, Any]:
    group, zone = item
    level_type = _level_type_for_group(group, zone)
    return {
        "level_id": f"SR-{index:03d}",
        "level_type": level_type,
        "level_group": group,
        "zone_low": _price_text(zone.zone_low),
        "zone_high": _price_text(zone.zone_high),
        "zone_mid": _price_text(zone.zone_mid),
        "timeframe": _dominant_timeframe(zone),
        "strength_score": _decimal_text(zone.strength_score),
        "confidence_score": _decimal_text(zone.confidence_score),
        "current_relevance_score": _decimal_text(zone.current_relevance_score),
        "touch_count": zone.touch_count,
        "distance_from_current_price_pct": _decimal_text(zone.distance_from_current_price_pct),
        "role_flip_status": zone.role_flip_status,
        "zone_quality": zone.zone_quality,
        "reason": _level_reason(group, zone),
    }


def _private_payload(
    points: tuple[PricePoint, ...],
    zones: tuple[LevelZone, ...],
    selected: tuple[tuple[str, LevelZone], ...],
    *,
    strategy: SupportResistanceStrategy,
) -> Mapping[str, Any]:
    return {
        "raw_swing_points": [_point_json(point) for point in sorted(points, key=lambda item: item.index, reverse=True)[:80]],
        "merged_level_clusters": [_zone_json(zone) for zone in sorted(zones, key=lambda item: item.strength_score, reverse=True)[:40]],
        "cluster_scoring_details": [_scoring_json(zone) for zone in sorted(zones, key=lambda item: item.strength_score, reverse=True)[:40]],
        "reaction_strength_details": [
            {"cluster_id": zone.cluster_id, "reaction_strength": _decimal_text(zone.reaction_strength)}
            for zone in zones[:40]
        ],
        "recency_score_details": [
            {"cluster_id": zone.cluster_id, "recency_score": _decimal_text(zone.recency_score)}
            for zone in zones[:40]
        ],
        "role_flip_detection_details": [
            {
                "cluster_id": zone.cluster_id,
                "role_flip_status": zone.role_flip_status,
                "source_point_types": sorted({point.point_type for point in zone.source_points}),
            }
            for zone in zones
            if zone.role_flip_status != "none"
        ][:20],
        "zone_width_config": _zone_width_config(strategy),
        "calculation_params": _calculation_params(strategy),
        "excluded_outliers": [_zone_json(zone) for zone in zones if zone.zone_quality == "outlier"][:20],
        "selected_level_groups": [
            {"level_group": group, "cluster_id": zone.cluster_id}
            for group, zone in selected
        ],
    }


def _model_material(key_levels: tuple[Mapping[str, Any], ...]) -> Mapping[str, Any]:
    nearest_support = [item for item in key_levels if item["level_group"] == "nearest_support"]
    nearest_resistance = [item for item in key_levels if item["level_group"] == "nearest_resistance"]
    return {
        "summary": "支撑压力策略输出关键价格区域摘要；这不是交易建议。",
        "nearest_support": nearest_support[:3],
        "nearest_resistance": nearest_resistance[:3],
        "main_evidence": [item["reason"] for item in key_levels[:8]],
        "uncertainty": "需要后续 23D/23F 判断突破、跌破、回踩和角色证据融合。",
        "review_questions": (
            "最近支撑压力是否仍有当前相关性？",
            "区间是否过宽导致参考价值下降？",
            "潜在角色转换是否需要后续确认？",
        ),
    }


def _point(rows: tuple[Any, ...], index: int, price: Decimal, point_type: str, source_level_type: str, timeframe: str, timeframe_weight: Decimal) -> PricePoint:
    return PricePoint(
        price=price,
        point_type=point_type,
        source_level_type=source_level_type,
        timeframe=timeframe,
        index=index,
        total_count=len(rows),
        timeframe_weight=timeframe_weight,
        reaction_strength=_reaction_strength(rows, index, price, source_level_type),
    )


def _reaction_strength(rows: tuple[Any, ...], index: int, price: Decimal, source_level_type: str) -> Decimal:
    right_rows = rows[index + 1 : index + 5]
    if not right_rows or price <= 0:
        return Decimal("0")
    if source_level_type == "resistance":
        reaction = price - min(_decimal_attr(row, "low_price") for row in right_rows)
    else:
        reaction = max(_decimal_attr(row, "high_price") for row in right_rows) - price
    return max(Decimal("0"), reaction / price)


def _local_move_pct(rows: tuple[Any, ...], price: Decimal) -> Decimal:
    high = _max_high(rows)
    low = _min_low(rows)
    close = _decimal_attr(rows[len(rows) // 2], "close_price")
    if close <= 0:
        return Decimal("0")
    return max(abs(price - high), abs(price - low), high - low) / close


def _zone_quality(
    *,
    touch_count: int,
    reaction_strength: Decimal,
    width_pct: Decimal,
    strategy: SupportResistanceStrategy,
) -> str:
    if width_pct > strategy.max_zone_width_pct:
        return "wide"
    if touch_count <= 1:
        return "outlier"
    if touch_count == 2 and reaction_strength < strategy.outlier_reaction_min_pct:
        return "weak"
    if width_pct < strategy.cluster_width_pct / Decimal("5"):
        return "narrow"
    return "clear"


def _role_flip_status(cluster: tuple[PricePoint, ...], zone_mid: Decimal, current_price: Decimal) -> str:
    has_resistance_source = any(point.source_level_type == "resistance" for point in cluster)
    has_support_source = any(point.source_level_type == "support" for point in cluster)
    if zone_mid < current_price and has_resistance_source:
        return "resistance_to_support"
    if zone_mid > current_price and has_support_source:
        return "support_to_resistance"
    if has_resistance_source and has_support_source:
        return "unconfirmed"
    return "none"


def _level_type_for_group(group: str, zone: LevelZone) -> str:
    if group.startswith("range_"):
        return "range_boundary"
    if group == "historical_reference":
        return "historical_reference"
    if group == "role_flip_candidate":
        return zone.level_type
    if group == "nearest_support":
        return "invalidation_reference"
    if group == "nearest_resistance":
        return "target_observation"
    return zone.level_type


def _level_reason(group: str, zone: LevelZone) -> str:
    if group == "nearest_support":
        return "当前价格下方最近的支撑参考区域；该区域不是最终失效价。"
    if group == "nearest_resistance":
        return "当前价格上方最近的压力观察区域；该区域不是最终目标价。"
    if group.startswith("major_"):
        return "多次触碰、反应或多周期共振形成的主要价格区域。"
    if group.startswith("range_"):
        return "近期运行区间边界，仅作为区间地图。"
    if group == "historical_reference":
        return "历史价格区域仍保留参考，但当前相关性已降低。"
    if group == "role_flip_candidate":
        return "可能存在支撑压力角色转换，是否确认留给后续策略判断。"
    return "支撑压力价格区域。"


def _base_level_type(zone_mid: Decimal, current_price: Decimal) -> str:
    return "support" if zone_mid <= current_price else "resistance"


def _recency_score(point: PricePoint) -> Decimal:
    if point.total_count <= 1:
        return Decimal("1")
    age = Decimal(point.total_count - 1 - point.index)
    return _clamp_unit(Decimal("1") - age / Decimal(point.total_count))


def _dominant_timeframe(zone: LevelZone) -> str:
    return max(zone.source_points, key=lambda point: point.timeframe_weight).timeframe


def _max_high(rows: tuple[Any, ...]) -> Decimal:
    return max(_decimal_attr(row, "high_price") for row in rows)


def _min_low(rows: tuple[Any, ...]) -> Decimal:
    return min(_decimal_attr(row, "low_price") for row in rows)


def _index_of_high(all_rows: tuple[Any, ...], subset: tuple[Any, ...]) -> int:
    high = _max_high(subset)
    target = next(row for row in subset if _decimal_attr(row, "high_price") == high)
    return all_rows.index(target)


def _index_of_low(all_rows: tuple[Any, ...], subset: tuple[Any, ...]) -> int:
    low = _min_low(subset)
    target = next(row for row in subset if _decimal_attr(row, "low_price") == low)
    return all_rows.index(target)


def _decimal_attr(row: Any, field_name: str) -> Decimal:
    return Decimal(str(getattr(row, field_name)))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _clamp_unit(value: Decimal) -> Decimal:
    if value < Decimal("0"):
        return Decimal("0")
    if value > Decimal("1"):
        return Decimal("1")
    return value


def _decimal_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _price_text(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _point_json(point: PricePoint) -> Mapping[str, Any]:
    return {
        "price": _price_text(point.price),
        "point_type": point.point_type,
        "source_level_type": point.source_level_type,
        "timeframe": point.timeframe,
        "index": point.index,
        "reaction_strength": _decimal_text(point.reaction_strength),
    }


def _zone_json(zone: LevelZone) -> Mapping[str, Any]:
    return {
        "cluster_id": zone.cluster_id,
        "zone_low": _price_text(zone.zone_low),
        "zone_high": _price_text(zone.zone_high),
        "zone_mid": _price_text(zone.zone_mid),
        "level_type": zone.level_type,
        "strength_score": _decimal_text(zone.strength_score),
        "current_relevance_score": _decimal_text(zone.current_relevance_score),
        "touch_count": zone.touch_count,
        "zone_quality": zone.zone_quality,
        "role_flip_status": zone.role_flip_status,
    }


def _scoring_json(zone: LevelZone) -> Mapping[str, Any]:
    return {
        "cluster_id": zone.cluster_id,
        "strength_score": _decimal_text(zone.strength_score),
        "confidence_score": _decimal_text(zone.confidence_score),
        "current_relevance_score": _decimal_text(zone.current_relevance_score),
        "timeframe_weight": _decimal_text(zone.timeframe_weight),
        "recency_score": _decimal_text(zone.recency_score),
        "cluster_density": _decimal_text(zone.cluster_density),
        "zone_width_pct": _decimal_text(zone.zone_width_pct),
        "distance_from_current_price_pct": _decimal_text(zone.distance_from_current_price_pct),
    }


def _zone_width_config(strategy: SupportResistanceStrategy) -> Mapping[str, str]:
    return {
        "cluster_width_pct": str(strategy.cluster_width_pct),
        "max_zone_width_pct": str(strategy.max_zone_width_pct),
        "nearest_distance_pct": str(strategy.nearest_distance_pct),
    }


def _calculation_params(strategy: SupportResistanceStrategy) -> Mapping[str, Any]:
    return {
        "swing_left_bars": strategy.swing_left_bars,
        "swing_right_bars": strategy.swing_right_bars,
        "min_swing_move_pct": str(strategy.min_swing_move_pct),
        "major_level_min_strength": str(strategy.major_level_min_strength),
        "outlier_reaction_min_pct": str(strategy.outlier_reaction_min_pct),
        "provides": strategy.provides,
    }


__all__ = ["SupportResistanceStrategy", "ROLE_FLIP_STATUSES", "ZONE_QUALITIES"]
