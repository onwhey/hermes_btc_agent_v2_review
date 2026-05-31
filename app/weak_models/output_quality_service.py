"""Service for 27B weak model output quality checks.

调用链：

用户 CLI
    ↓
scripts/check_weak_model_output_quality.py::main
    ↓
app/weak_models/output_quality_service.py::WeakModelOutputQualityService.check_weak_model_output_quality
    ↓
app/weak_models/output_quality_repository.py::get_quality_target_by_run_id / list_recent_quality_targets
    ↓
app/weak_models/output_quality_repository.py::upsert_quality_check

本文件属于 `app/weak_models` 模块，负责读取 27A 已落库输出并执行 27B
只读质量审查。它不重新运行弱模型，不修改原始 `weak_model_result` 或
`weak_model_aggregation`，不自动修改 `configs/weak_models/*.yaml`，不接入
18/19/20/21，不接 scheduler。
外部服务：无。MySQL：默认只读；仅 `confirm_write=True` 时写入
`weak_model_quality_check`。Redis：无。Hermes：无。DeepSeek/GPT/Claude：无。
交易执行：不读取账户或仓位，不生成订单，不自动交易。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from app.core.time_utils import ensure_utc_aware
from app.weak_models.output_quality_repository import (
    WeakModelOutputQualityRepository,
    create_default_weak_model_output_quality_repository,
)
from app.weak_models.output_quality_rules import checked_model_summary, evaluate_quality_issues
from app.weak_models.output_quality_types import (
    EXIT_SUCCESS,
    WEAK_MODEL_OUTPUT_QUALITY_VERSION,
    WeakModelQualityCheckReport,
    WeakModelQualityCheckRequest,
    WeakModelQualityCheckResult,
    WeakModelQualityIssue,
    WeakModelQualitySeverity,
    WeakModelQualityStatus,
    WeakModelQualityTarget,
    build_weak_model_quality_check_id,
    quality_persistence_payload_from_result,
    quality_status_from_counts,
)


class WeakModelOutputQualityService:
    """Check persisted 27A weak model outputs for conservative quality issues."""

    def __init__(self, *, repository: WeakModelOutputQualityRepository | Any | None = None) -> None:
        self._repository = repository or create_default_weak_model_output_quality_repository()

    def check_weak_model_output_quality(
        self,
        db_session: Any,
        *,
        request: WeakModelQualityCheckRequest,
    ) -> WeakModelQualityCheckReport:
        """Run 27B checks without rerunning weak models or external services.

        Parameters: caller-owned DB session and request. Return value: a report
        with one result per checked 27A run. Failure scenarios from database
        access propagate to the CLI, which maps them to parameter/database
        error exit code. Data impact: writes only `weak_model_quality_check`
        when `confirm_write=True` and `dry_run=False`.
        """

        targets = self._load_targets(db_session, request=request)
        results = tuple(self._evaluate_target_or_missing(request=request, target=target) for target in targets)
        if request.confirm_write and not request.dry_run:
            results = tuple(self._persist_result(db_session, result=result) for result in results)
            _commit_if_possible(db_session)
        return WeakModelQualityCheckReport(request=request, results=results, exit_code=EXIT_SUCCESS)

    def _load_targets(
        self,
        db_session: Any,
        *,
        request: WeakModelQualityCheckRequest,
    ) -> tuple[WeakModelQualityTarget | None, ...]:
        if request.weak_model_run_id:
            return (
                self._repository.get_quality_target_by_run_id(
                    db_session,
                    weak_model_run_id=request.weak_model_run_id,
                ),
            )
        return self._repository.list_recent_quality_targets(
            db_session,
            symbol=request.symbol,
            base_interval=request.base_interval,
            higher_interval=request.higher_interval,
            limit=request.limit,
        )

    def _evaluate_target_or_missing(
        self,
        *,
        request: WeakModelQualityCheckRequest,
        target: WeakModelQualityTarget | None,
    ) -> WeakModelQualityCheckResult:
        if target is None:
            missing_run_id = request.weak_model_run_id or ""
            issue = WeakModelQualityIssue(
                error_code="weak_model_run_missing",
                reason="未找到指定 weak_model_run，27B 不会重新运行弱模型。",
                severity=WeakModelQualitySeverity.CRITICAL.value,
                field_name="weak_model_run_id",
                observed_value=missing_run_id,
                expected="existing weak_model_run",
            )
            return self._build_result(
                request=request,
                weak_model_run_id=missing_run_id,
                aggregation=None,
                run=None,
                results=(),
                issues=(issue,),
                checked_models=(),
            )
        return self._evaluate_target(request=request, target=target)

    def _evaluate_target(
        self,
        *,
        request: WeakModelQualityCheckRequest,
        target: WeakModelQualityTarget,
    ) -> WeakModelQualityCheckResult:
        aggregation = target.aggregation
        results = tuple(target.results)
        issues = evaluate_quality_issues(aggregation, results)
        checked_models = tuple(checked_model_summary(row) for row in results)
        return self._build_result(
            request=request,
            weak_model_run_id=str(getattr(target.run, "weak_model_run_id", "") or ""),
            aggregation=aggregation,
            run=target.run,
            results=results,
            issues=tuple(issues),
            checked_models=checked_models,
        )

    def _build_result(
        self,
        *,
        request: WeakModelQualityCheckRequest,
        weak_model_run_id: str,
        aggregation: Any | None,
        run: Any | None,
        results: tuple[Any, ...],
        issues: tuple[WeakModelQualityIssue, ...],
        checked_models: tuple[Mapping[str, Any], ...],
    ) -> WeakModelQualityCheckResult:
        warning_count = sum(1 for issue in issues if issue.severity == WeakModelQualitySeverity.WARNING.value)
        critical_count = sum(1 for issue in issues if issue.severity == WeakModelQualitySeverity.CRITICAL.value)
        status, severity = quality_status_from_counts(warning_count, critical_count)
        aggregation_id = _text_or_none(getattr(aggregation, "weak_model_aggregation_id", None))
        strategy_signal_run_id = str(getattr(run, "strategy_signal_run_id", "") or getattr(aggregation, "strategy_signal_run_id", "") or "")
        snapshot_id = _text_or_none(getattr(run, "snapshot_id", None) or getattr(aggregation, "snapshot_id", None))
        symbol = str(getattr(run, "symbol", "") or getattr(aggregation, "symbol", "") or request.symbol)
        base_interval = str(getattr(run, "base_interval", "") or getattr(aggregation, "base_interval", "") or request.base_interval)
        higher_interval = str(getattr(run, "higher_interval", "") or getattr(aggregation, "higher_interval", "") or request.higher_interval)
        kline_slot_utc = ensure_utc_aware(
            getattr(run, "kline_slot_utc", None) or getattr(aggregation, "kline_slot_utc", None)
        )
        issue_count = len(issues)
        summary_text = _summary_text(
            status=status,
            issue_count=issue_count,
            warning_count=warning_count,
            critical_count=critical_count,
        )
        return WeakModelQualityCheckResult(
            status=status,
            severity=severity,
            quality_check_id=build_weak_model_quality_check_id(weak_model_run_id or request.weak_model_run_id or "missing"),
            weak_model_run_id=weak_model_run_id,
            weak_model_aggregation_id=aggregation_id,
            strategy_signal_run_id=strategy_signal_run_id,
            snapshot_id=snapshot_id,
            symbol=symbol,
            base_interval=base_interval,
            higher_interval=higher_interval,
            kline_slot_utc=kline_slot_utc,
            issue_count=issue_count,
            warning_count=warning_count,
            critical_count=critical_count,
            should_block_pipeline=False,
            issues=issues,
            checked_models=checked_models,
            summary_text=summary_text,
            database_written=False,
            database_action="dry_run" if request.dry_run or not request.confirm_write else "not_written",
            trace_id=request.trace_id,
            details={
                "quality_version": WEAK_MODEL_OUTPUT_QUALITY_VERSION,
                "checked_result_count": len(results),
                "calibration_policy": "observe_and_report_only",
                "config_auto_modified": False,
                "not_trading_advice": True,
            },
        )

    def _persist_result(self, db_session: Any, *, result: WeakModelQualityCheckResult) -> WeakModelQualityCheckResult:
        if not result.weak_model_run_id:
            return replace(result, database_written=False, database_action="not_written_missing_run")
        _, action = self._repository.upsert_quality_check(
            db_session,
            payload=quality_persistence_payload_from_result(result),
        )
        return replace(result, database_written=True, database_action=action)


def create_default_weak_model_output_quality_service() -> WeakModelOutputQualityService:
    """Create the default 27B output quality service."""

    return WeakModelOutputQualityService()


def _summary_text(
    *,
    status: WeakModelQualityStatus,
    issue_count: int,
    warning_count: int,
    critical_count: int,
) -> str:
    if status == WeakModelQualityStatus.PASSED:
        return "27B 弱模型输出质量检查通过；本检查不阻断主链路。"
    return (
        "27B 弱模型输出质量检查发现问题："
        f"issue_count={issue_count}, warning_count={warning_count}, critical_count={critical_count}；"
        "本检查只提出校准建议，不自动修改配置，不阻断主链路。"
    )


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _commit_if_possible(db_session: Any) -> None:
    commit = getattr(db_session, "commit", None)
    if callable(commit):
        commit()


__all__ = [
    "WeakModelOutputQualityService",
    "create_default_weak_model_output_quality_service",
]
