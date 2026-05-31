"""26B strategy evidence quality gate service.

调用链：

用户 CLI / scheduler 触发 25 pipeline
    ↓
app/strategy_pipeline/service.py::StrategyPipelineService._run_confirmed_pipeline
    ↓
app/strategy/evidence_quality/service.py::run_strategy_evidence_quality_gate
    ↓
app/strategy/evidence_quality/repository.py::get_strategy_evidence_aggregation
    ↓
app/strategy/evidence_quality/repository.py::list_strategy_signal_results
    ↓
app/strategy/evidence_quality/repository.py::upsert_quality_check_result
    ↓
app/strategy/evidence_quality/alerting.py::send_strategy_evidence_quality_failure_alert

本文件属于 `app/strategy/evidence_quality` 模块。
本文件负责 26B 质量闸门编排：读取配置、调用纯证据评估、落库质量结果，
并在 blocking failure 时发送固定模板 Hermes 系统告警。
本文件不负责策略算法，不负责 18/20/21 核心逻辑，不请求 Binance，不读写 Redis，
不调用 DeepSeek 或其他大模型，不读取账户或仓位，不生成订单，不自动交易。
MySQL：通过 repository 读取 SSR/SEA/strategy result，并写入 26B 质量结果。
Hermes：仅在 blocking failure 且配置允许时发送固定模板系统告警。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from app.core.config import AppSettings, get_settings
from app.core.logger import get_logger
from app.strategy.evidence_quality.alerting import send_strategy_evidence_quality_failure_alert
from app.strategy.evidence_quality.config import StrategyEvidenceQualityConfigProvider
from app.strategy.evidence_quality.evaluator import (
    alert_message_id,
    alert_status,
    build_error_message,
    chain_scope_issues,
    config_snapshot,
    evaluate_active_strategy_result,
    existing_quality_check_id,
    field_label,
    int_or_none,
    issue,
    persistence_payload_from_result,
    required_role_issues,
    role_quality_from_aggregation,
    rows_by_strategy,
    unique_non_empty,
)
from app.strategy.evidence_quality.repository import (
    StrategyEvidenceQualityRepository,
    create_default_strategy_evidence_quality_repository,
)
from app.strategy.evidence_quality.types import (
    EXIT_FAILED,
    EXIT_SUCCESS,
    STRATEGY_EVIDENCE_QUALITY_ERROR_CODE,
    NormalOperatingStrategyDefinition,
    StrategyEvidenceQualityCheckIssue,
    StrategyEvidenceQualityGateRequest,
    StrategyEvidenceQualityGateResult,
    StrategyEvidenceQualityQueryReport,
    StrategyEvidenceQualityQueryRequest,
    StrategyEvidenceQualitySeverity,
    StrategyEvidenceQualityStatus,
    build_quality_check_id,
)

ALERT_TERMINAL_STATUSES = {"submitted_to_hermes", "gateway_rejected", "submit_failed", "skipped", "skipped_by_config"}


class StrategyEvidenceQualityGateService:
    """Run the 26B quality gate before stage 18 material-pack creation.

    Parameters:
    - `settings`: shared non-sensitive settings, including 26B enable switches.
    - `repository`: MySQL repository; fake repositories can be injected in tests.
    - `config_provider`: local config provider for active strategy definitions.
    - `alert_dispatcher`: fixed-template Hermes dispatcher; injectable for tests.

    Return value: `StrategyEvidenceQualityGateResult`.
    Failure scenarios: database/config failures propagate to the 25 pipeline,
    which records a structured pipeline failure. Hermes failures are caught and
    recorded in quality/pipeline details without rolling back the quality row.
    External services: never calls models/Binance/Redis. Hermes is called only
    for blocking failures when the alert switch is enabled.
    """

    def __init__(
        self,
        *,
        settings: AppSettings | Any | None = None,
        repository: StrategyEvidenceQualityRepository | Any | None = None,
        config_provider: StrategyEvidenceQualityConfigProvider | Any | None = None,
        alert_dispatcher: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repository = repository or create_default_strategy_evidence_quality_repository()
        self._config_provider = config_provider or StrategyEvidenceQualityConfigProvider()
        self._alert_dispatcher = alert_dispatcher or send_strategy_evidence_quality_failure_alert
        self._logger = get_logger("strategy.evidence_quality.service")

    def run_strategy_evidence_quality_gate(
        self,
        db_session: Any,
        *,
        request: StrategyEvidenceQualityGateRequest,
    ) -> StrategyEvidenceQualityGateResult:
        """Run 26B and return whether the 25 pipeline may enter stage 18.

        The service reads persisted SSR/SEA/strategy result rows. It never
        reruns strategies, never invokes 18/20/21, never calls any model, and
        never sends a trading suggestion.
        """

        if not bool(getattr(self._settings, "strategy_evidence_quality_gate_enabled", True)):
            return self._persist_and_return(db_session, result=self._build_skipped_result(request))

        active_strategies = self._config_provider.list_normal_operating_strategies()
        required_roles = tuple(self._config_provider.required_roles())
        required_role_provides = dict(self._config_provider.required_role_provides())
        signal_run = self._repository.get_strategy_signal_run(db_session, run_id=request.strategy_signal_run_id)
        aggregation = self._repository.get_strategy_evidence_aggregation(
            db_session,
            aggregation_id=request.strategy_evidence_aggregation_id,
        )
        strategy_results = self._repository.list_strategy_signal_results(
            db_session,
            run_id=request.strategy_signal_run_id,
        )
        existing_quality = self._repository.get_existing_quality_check(
            db_session,
            evidence_aggregation_id=request.strategy_evidence_aggregation_id,
            trigger_source=request.trigger_source,
        )

        failed_checks, warning_checks, strategy_quality, role_quality = self._evaluate_quality(
            request=request,
            signal_run=signal_run,
            aggregation=aggregation,
            strategy_results=tuple(strategy_results),
            active_strategies=active_strategies,
            required_roles=required_roles,
            required_role_provides=required_role_provides,
        )
        result = self._build_result(
            request=request,
            failed_checks=tuple(failed_checks),
            warning_checks=tuple(warning_checks),
            strategy_quality=strategy_quality,
            role_quality=role_quality,
            config_snapshot=config_snapshot(
                active_strategies=active_strategies,
                required_roles=required_roles,
                required_role_provides=required_role_provides,
                gate_enabled=True,
                alert_enabled=bool(getattr(self._settings, "strategy_evidence_quality_gate_alert_enabled", True)),
            ),
            existing_quality=existing_quality,
        )
        result = self._persist_and_return(db_session, result=result)
        if result.should_block_pipeline:
            result = self._send_alert_for_blocking_result(
                db_session,
                result=result,
                existing_quality=existing_quality,
            )
        return result

    def query_strategy_evidence_quality_results(
        self,
        db_session: Any,
        *,
        request: StrategyEvidenceQualityQueryRequest,
    ) -> StrategyEvidenceQualityQueryReport:
        """Read existing 26B rows for the auxiliary CLI without writes/Hermes."""

        rows = self._repository.list_quality_check_results(db_session, request=request)
        exit_code = EXIT_FAILED if any(row.should_block_pipeline or row.status == "failed" for row in rows) else EXIT_SUCCESS
        return StrategyEvidenceQualityQueryReport(request=request, rows=rows, exit_code=exit_code)

    def _evaluate_quality(
        self,
        *,
        request: StrategyEvidenceQualityGateRequest,
        signal_run: Any | None,
        aggregation: Any | None,
        strategy_results: tuple[Any, ...],
        active_strategies: tuple[NormalOperatingStrategyDefinition, ...],
        required_roles: tuple[str, ...],
        required_role_provides: Mapping[str, tuple[str, ...]],
    ) -> tuple[list[StrategyEvidenceQualityCheckIssue], list[StrategyEvidenceQualityCheckIssue], dict[str, Any], dict[str, Any]]:
        failed: list[StrategyEvidenceQualityCheckIssue] = []
        warnings: list[StrategyEvidenceQualityCheckIssue] = []
        strategy_quality: dict[str, Any] = {}
        role_quality: dict[str, Any] = {}

        if signal_run is None:
            failed.append(issue("strategy_signal_run_missing", "本轮 strategy_signal_run 缺失。"))
        if aggregation is None:
            failed.append(issue("strategy_evidence_aggregation_missing", "本轮 strategy_evidence_aggregation_result 缺失。"))
        else:
            failed.extend(chain_scope_issues(request=request, aggregation=aggregation))
            role_quality.update(role_quality_from_aggregation(aggregation, required_roles=required_roles))
            failed.extend(
                required_role_issues(
                    role_quality=role_quality,
                    required_roles=required_roles,
                    required_role_provides=required_role_provides,
                )
            )

        rows_by_strategy_map = rows_by_strategy(strategy_results)
        for definition in active_strategies:
            row = rows_by_strategy_map.get(definition.strategy_name)
            item_quality, item_failures = evaluate_active_strategy_result(
                definition=definition,
                row=row,
                required_role_provides=required_role_provides,
                expected_run_id=request.strategy_signal_run_id,
            )
            strategy_quality[definition.strategy_name] = item_quality
            failed.extend(item_failures)

        if not active_strategies:
            warnings.append(
                issue(
                    "normal_operating_strategy_config_empty",
                    "未从策略配置识别到 active decision_participant / can_veto 策略，请检查 registry。",
                    severity=StrategyEvidenceQualitySeverity.WARNING.value,
                )
            )

        role_quality.setdefault(
            "_slot_mismatch_detection",
            {
                "status": "limited_by_schema",
                "reason": "strategy_signal_run 与 strategy_evidence_aggregation_result 当前没有直接 kline_slot_utc 字段；26B 使用 pipeline slot 记录并校验 SSR/SEA/scope。",
            },
        )
        return failed, warnings, strategy_quality, role_quality

    def _build_result(
        self,
        *,
        request: StrategyEvidenceQualityGateRequest,
        failed_checks: tuple[StrategyEvidenceQualityCheckIssue, ...],
        warning_checks: tuple[StrategyEvidenceQualityCheckIssue, ...],
        strategy_quality: Mapping[str, Any],
        role_quality: Mapping[str, Any],
        config_snapshot: Mapping[str, Any],
        existing_quality: Any | None,
    ) -> StrategyEvidenceQualityGateResult:
        should_block = bool(failed_checks)
        quality_check_id = existing_quality_check_id(existing_quality) or build_quality_check_id(
            evidence_aggregation_id=request.strategy_evidence_aggregation_id,
            trace_id=request.trace_id,
        )
        alert_status_value = "pending" if should_block else "not_required"
        existing_alert_status = str(getattr(existing_quality, "alert_status", "") or "")
        existing_alert_message_id = int_or_none(getattr(existing_quality, "alert_message_id", None))
        if should_block and existing_alert_status in ALERT_TERMINAL_STATUSES:
            alert_status_value = existing_alert_status

        return StrategyEvidenceQualityGateResult(
            status=StrategyEvidenceQualityStatus.FAILED if should_block else StrategyEvidenceQualityStatus.PASSED,
            quality_check_id=quality_check_id,
            pipeline_run_id=request.pipeline_run_id,
            strategy_signal_run_id=request.strategy_signal_run_id,
            strategy_evidence_aggregation_id=request.strategy_evidence_aggregation_id,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=request.kline_slot_utc,
            should_block_pipeline=should_block,
            severity=(
                StrategyEvidenceQualitySeverity.CRITICAL
                if should_block
                else StrategyEvidenceQualitySeverity.INFO
            ),
            error_code=STRATEGY_EVIDENCE_QUALITY_ERROR_CODE if should_block else None,
            error_message=build_error_message(failed_checks) if should_block else None,
            failed_checks=failed_checks,
            warning_checks=warning_checks,
            failed_strategies=unique_non_empty(item.strategy_name for item in failed_checks),
            failed_roles=unique_non_empty(item.strategy_role for item in failed_checks),
            missing_fields=unique_non_empty(field_label(item) for item in failed_checks),
            alert_required=should_block,
            alert_status=alert_status_value,
            alert_message_id=existing_alert_message_id if alert_status_value == existing_alert_status else None,
            trace_id=request.trace_id,
            details={
                "strategy_quality": dict(strategy_quality),
                "role_quality": dict(role_quality),
                "config_snapshot": dict(config_snapshot),
            },
        )

    def _build_skipped_result(self, request: StrategyEvidenceQualityGateRequest) -> StrategyEvidenceQualityGateResult:
        warning = issue(
            "gate_skipped_by_config",
            "STRATEGY_EVIDENCE_QUALITY_GATE_ENABLED=false，26B 闸门按配置跳过。",
            severity=StrategyEvidenceQualitySeverity.WARNING.value,
        )
        return StrategyEvidenceQualityGateResult(
            status=StrategyEvidenceQualityStatus.WARNING,
            quality_check_id=build_quality_check_id(
                evidence_aggregation_id=request.strategy_evidence_aggregation_id,
                trace_id=request.trace_id,
            ),
            pipeline_run_id=request.pipeline_run_id,
            strategy_signal_run_id=request.strategy_signal_run_id,
            strategy_evidence_aggregation_id=request.strategy_evidence_aggregation_id,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            kline_slot_utc=request.kline_slot_utc,
            should_block_pipeline=False,
            severity=StrategyEvidenceQualitySeverity.WARNING,
            error_code="gate_skipped_by_config",
            error_message=warning.reason,
            warning_checks=(warning,),
            alert_required=False,
            alert_status="skipped_by_config",
            trace_id=request.trace_id,
            details={
                "gate_skipped_by_config": True,
                "config_snapshot": {
                    "gate_enabled": False,
                    "alert_enabled": bool(getattr(self._settings, "strategy_evidence_quality_gate_alert_enabled", True)),
                },
            },
        )

    def _persist_and_return(
        self,
        db_session: Any,
        *,
        result: StrategyEvidenceQualityGateResult,
    ) -> StrategyEvidenceQualityGateResult:
        payload = persistence_payload_from_result(result)
        _, action = self._repository.upsert_quality_check_result(db_session, payload=payload)
        _commit_if_possible(db_session)
        return replace(result, database_written=True, database_action=action)

    def _send_alert_for_blocking_result(
        self,
        db_session: Any,
        *,
        result: StrategyEvidenceQualityGateResult,
        existing_quality: Any | None,
    ) -> StrategyEvidenceQualityGateResult:
        existing_alert_status = str(getattr(existing_quality, "alert_status", "") or "")
        if existing_alert_status in ALERT_TERMINAL_STATUSES:
            return result
        if not bool(getattr(self._settings, "strategy_evidence_quality_gate_alert_enabled", True)):
            self._repository.update_quality_alert_status(
                db_session,
                quality_check_id=result.quality_check_id,
                alert_status="skipped_by_config",
                alert_message_id=None,
            )
            _commit_if_possible(db_session)
            return replace(result, alert_status="skipped_by_config")

        try:
            alert_result = self._alert_dispatcher(
                db_session,
                quality_result=result,
                settings=self._settings,
                send_real_alert=True,
            )
            alert_status_value = alert_status(alert_result)
            alert_message_id_value = alert_message_id(alert_result)
            self._repository.update_quality_alert_status(
                db_session,
                quality_check_id=result.quality_check_id,
                alert_status=alert_status_value,
                alert_message_id=alert_message_id_value,
            )
            _commit_if_possible(db_session)
            return replace(
                result,
                alert_status=alert_status_value,
                alert_message_id=alert_message_id_value,
                alert_error_message=getattr(alert_result, "error_message", None),
            )
        except Exception as exc:  # noqa: BLE001 - Hermes failure must not rollback 26B quality result.
            self._logger.error("26B Hermes alert failed, quality_check_id=%s error=%s", result.quality_check_id, exc)
            try:
                self._repository.update_quality_alert_status(
                    db_session,
                    quality_check_id=result.quality_check_id,
                    alert_status="submit_failed",
                    alert_message_id=None,
                )
                _commit_if_possible(db_session)
            except Exception as update_exc:  # noqa: BLE001 - keep blocking result even if status update fails.
                self._logger.error(
                    "26B alert status update failed, quality_check_id=%s error=%s",
                    result.quality_check_id,
                    update_exc,
                )
            return replace(result, alert_status="submit_failed", alert_error_message=str(exc))


def create_default_strategy_evidence_quality_gate_service(
    *,
    settings: AppSettings | Any | None = None,
) -> StrategyEvidenceQualityGateService:
    """Create the default 26B service used by the stage-25 pipeline."""

    return StrategyEvidenceQualityGateService(settings=settings)


def run_strategy_evidence_quality_gate(
    db_session: Any,
    *,
    request: StrategyEvidenceQualityGateRequest,
    service: StrategyEvidenceQualityGateService | None = None,
) -> StrategyEvidenceQualityGateResult:
    """Convenience wrapper for pipeline stage factories and tests."""

    active_service = service or create_default_strategy_evidence_quality_gate_service()
    return active_service.run_strategy_evidence_quality_gate(db_session, request=request)


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


__all__ = [
    "StrategyEvidenceQualityConfigProvider",
    "StrategyEvidenceQualityGateService",
    "create_default_strategy_evidence_quality_gate_service",
    "run_strategy_evidence_quality_gate",
]
