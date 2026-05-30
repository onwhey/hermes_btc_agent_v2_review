# 24C model review input compaction implementation

## 1. Function

This document records the 24C fix that prevents model-review input from
exceeding the configured 10000 character hard limit.

## 2. Entry

User entry remains:

```text
python -m scripts.run_model_analysis --material-pack-id AMP-xxx --trigger-source cli --dry-run
python -m scripts.run_model_analysis --material-pack-id AMP-xxx --trigger-source cli --use-real-model --model-key <configured-model-key> --confirm-real-model-cost --confirm-write
```

The script remains a thin CLI entry. It does not build prompts, does not call a
provider directly, does not write MySQL directly, does not send Hermes directly,
does not generate advice, and does not trade.

## 3. Core Call Chain

```text
scripts/run_model_analysis.py::main
    -> app/model_analysis/service.py::ModelAnalysisService.run_model_analysis
    -> app/model_analysis/repository.py::ModelAnalysisRepository.get_material_pack_by_id
    -> app/model_analysis/material_pack_reviewability.py::validate_material_pack_reviewability
    -> app/model_analysis/prompt_builder.py::build_model_review_prompt
    -> app/model_analysis/input_compactor.py::build_compacted_model_review_input_summary
    -> app/model_analysis/providers/mock.py::MockModelReviewProvider.review_material
       or app/model_analysis/providers/deepseek.py::DeepSeekReviewProvider.call_review_model
    -> app/model_analysis/schema_validator.py::validate_model_review_output
```

## 4. Compaction Rules

`app/model_analysis/input_compactor.py` builds a model-facing summary instead of
sending full `material_json` or full `strategy_evidence`.

It preserves:

```text
analysis_time_utc
analysis_time_prc
latest_base_kline_close_time_utc
latest_higher_kline_close_time_utc
data_freshness_status
strategy_evidence.source
aggregation_id
strategy_signal_run_id
status
candidate_bias
candidate_confidence
decision_readiness
risk_gate_summary
evidence_missing
model_review_focus
```

It summarizes or truncates:

```text
strategy_evidence_summary
decision_source_chain
role_coverage_matrix
participation_summary
observe_only_summary
strategy_conflict_summary
risk_gate_summary.reason_text
legacy strategy_summaries
material summary details
math material details
```

The prompt builder first renders standard compact input. If the rendered prompt
is above 8000 characters, it rebuilds with aggressive compaction. If the prompt
is still above 10000 characters, it rebuilds with emergency compaction. The
service still applies the configured hard limit and returns:

```text
error_code = input_char_limit_exceeded
```

if the final input remains too large.

## 5. Data Access

Reads:

```text
analysis_material_pack.material_json
analysis_material_pack.summary_json
analysis_material_pack.question_json
analysis_material_pack.validation_plan_json
analysis_material_pack.data_window_json
analysis_material_pack.future_leakage_guard_json
analysis_material_pack.created_at_utc
analysis_material_pack.updated_at_utc
```

Writes:

```text
dry-run: no MySQL writes
confirm-write: existing model_analysis_run / model_analysis_result writes only
```

Redis: not read or written.

Hermes: no final strategy notification is sent by this fix. Existing model
analysis error alerts are unchanged.

External services: no Binance request and no extra external data source.

Large model: only the existing provider path is used after reviewability and
input-size guards pass.

## 6. Boundaries

This fix does not read `strategy_payload_json`, does not copy 23F aggregation
logic, does not re-run strategies, does not modify 23B/23C/23D/23E/23F, does
not generate advice, does not generate trade_setup, does not read account or
position data, and does not perform automatic trading.

## 7. Tests

Updated tests:

```text
tests/model_analysis/test_model_analysis_service.py
```

Coverage includes:

```text
large material_json and strategy_evidence are compacted under the hard limit
candidate_bias and decision_readiness are preserved
aggregation_id and strategy_signal_run_id are preserved
time anchors are preserved
risk_gate_summary is preserved
evidence_missing is preserved
model_review_focus is preserved
private payload sentinel text is not included in prompt input
dry-run does not write MySQL and does not send final Hermes notification
configured hard limit still blocks with input_char_limit_exceeded
```

Default pytest does not request Binance, does not connect to real MySQL, does
not connect to real Redis, does not send real Hermes, and does not call a real
large model.
