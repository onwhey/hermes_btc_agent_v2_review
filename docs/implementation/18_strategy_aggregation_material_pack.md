# 18 Strategy Aggregation Material Pack 瀹炵幇璇存槑

## 0. 2026-05-19 boundary correction: analysis hypotheses only

Stage 18 does not implement real strategies, does not independently judge
long/short direction, does not generate strategy signals, does not generate
operation advice, and does not generate executable trading fields.

`long` / `short` / `wait` / `stop_trading` in stage 18 only mean analysis
hypotheses or direction placeholders projected from existing stage-16 rows or
test fixtures. They are not strategy conclusions and not trading advice.

`candidate_scenarios_json` uses `long_hypothesis`, `short_hypothesis`,
`wait_hypothesis`, and `stop_trading_hypothesis`. Every hypothesis must include:

```text
scenario_semantics = analysis_hypothesis_only
is_strategy_signal = false
is_trading_advice = false
is_executable = false
source = fixture_or_existing_signal_projection
strategy_logic_implemented = false
promotion_allowed = false
promotion_requires_future_strategy_and_llm_stage = true
```

Persistent direction fields are `analysis_hypothesis_direction` and
`analysis_hypothesis_confidence`; they are stored with
`analysis_hypothesis_semantics=analysis_hypothesis_only`,
`is_strategy_signal=false`, `is_trading_advice=false`, and
`is_executable=false`.

`context_upside_downside_ratio` is support/resistance observation context only.
It is not an entry/exit metric, not a stop-loss/take-profit basis, not a
strategy win-rate metric, and not a final advice input by itself.

`stop_trading_hypothesis` is emitted only when existing stage-16 fake/mock rows
or the stage-18 risk gate projection explicitly provide that source. It is
tagged with `stop_trading_source=upstream_risk_gate_projection` and remains a
hypothesis, not a final risk-control decision.

Stage-18 swing, ATR, range, support/resistance, and context upside/downside observation
values are deterministic material-pack context only. They must not be read as
real long/short strategy logic. Real Gann, trend, support/resistance, risk
control, and other strategies must be developed later as independent
plugin-style strategy classes.

## 1. 鍔熻兘锛氱瓥鐣ヨ仛鍚堜笌鏉愭枡鍖呮瀯寤?
### 1.1 鍙戣捣鏂瑰紡

鎵嬪姩楠岃瘉锛?
```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <strategy_signal_run.run_id> \
  --trigger-source cli
```

纭鍐欏叆锛?
```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <strategy_signal_run.run_id> \
  --trigger-source cli \
  --confirm-write
```

绗?17 鍚庣疆鑷姩瑙﹀彂锛?
```text
app/scheduler/runner.py::SchedulerRunner._run_strategy_signal_post_collect_if_needed
    -> app/scheduler/jobs/strategy_signal_scheduler_job.py::run_strategy_signal_scheduler_after_collect_job
    -> app/scheduler/runner.py::SchedulerRunner._run_strategy_aggregation_post_signal_if_needed
    -> app/scheduler/jobs/strategy_aggregation_job.py::run_strategy_aggregation_after_signal_job
    -> app/strategy/aggregation/service.py::run_strategy_aggregation
```

鑷姩瑙﹀彂鍙湪绗?17 杩斿洖 `success` / `partial_success` 涓?`STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=true` 鏃跺彂鐢熴€?`waiting_upstream` / `blocked` / `failed` / `skipped` 涓嶈Е鍙戠 18銆?
### 1.2 鍏ュ彛鏂囦欢

鎵嬪姩鍏ュ彛锛?
`scripts/run_strategy_aggregation.py`

鍏ュ彛鏂规硶锛?
`main()`

鏍稿績 service锛?
`app/strategy/aggregation/service.py`

鏍稿績鏂规硶锛?
`StrategyAggregationService.run_strategy_aggregation()`

## 2. 杈撳叆鍜岃竟鐣?
绗?18 鍙鍙栵細

1. `strategy_signal_run`
2. `strategy_signal_result`
3. `snapshot_id` 瀵瑰簲鐨?`MarketContextSnapshot`
4. snapshot 杩樺師鍑虹殑 `market_kline_4h` / `market_kline_1d` 宸叉敹鐩樼獥鍙?
绗?18 涓嶈姹傚閮ㄦ帴鍙ｃ€?绗?18 涓嶈鍙?Redis銆?绗?18 涓嶅啓鍏?Redis銆?绗?18 涓嶄慨鏀?`market_kline_4h`銆?绗?18 涓嶄慨鏀?`market_kline_1d`銆?绗?18 涓嶈皟鐢?DeepSeek銆丟PT銆丆laude 鎴栧叾浠栧ぇ妯″瀷銆?绗?18 涓嶇敓鎴愭渶缁堜氦鏄撳缓璁€?绗?18 涓嶈鍙栬处鎴枫€佽鍗曘€佹寔浠撴垨 API 绉侀挜銆?绗?18 涓嶈嚜鍔ㄤ氦鏄撱€?
绗?18 涓庡墠鍚庨樁娈佃竟鐣岋細

1. 涓庣 17锛氱 17 浠嶅彧璐熻矗绛栫暐淇″彿 scheduler 缂栨帓銆傜 18 涓嶆敼鍙樼 17 target 缁戝畾銆乪vent log 璇箟鎴栫 17 璋冪敤绗?16 鐨勬柟寮忋€?2. 涓庣 16锛氱 18 鍙鍙栧凡钀藉簱鐨勭 16 杩愯缁撴灉锛屼笉閲嶆柊杩愯 StrategySignalService銆?3. 涓庣 15锛氱 18 鍙皟鐢?snapshot repository 鐨勫彧璇昏繕鍘熻兘鍔涳紝涓嶉噸鏂扮敓鎴?MarketContextSnapshot銆?4. 涓庣 19锛氱 18 鍙敓鎴?`analysis_material_pack` 鍜岄棶棰樻竻鍗曪紝涓嶈皟鐢ㄥぇ妯″瀷銆?5. 涓庣 20锛氱 18 涓嶈繘鍏?advice lifecycle锛屼笉鍒涘缓銆佹洿鏂版垨鍏抽棴鏈€缁堝缓璁€?
`analysis_hypothesis_direction` 鍙槸鑱氬悎灞傚€欓€夋柟鍚戯紝涓嶆槸 `final_advice`銆?
## 3. 鏍稿績璋冪敤閾捐矾

```text
scripts/run_strategy_aggregation.py::main
    -> app/strategy/aggregation/service.py::run_strategy_aggregation
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.get_strategy_signal_run
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.list_strategy_signal_results
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.restore_snapshot_kline_windows
    -> app/strategy/aggregation/material_builder.py::build_future_leakage_guard
    -> app/strategy/aggregation/service.py::_classify_strategy_results
    -> app/strategy/aggregation/service.py::_build_aggregation_decision
    -> app/strategy/aggregation/candidate_scenario_builder.py::build_candidate_scenarios
    -> app/strategy/aggregation/material_builder.py::build_material_pack
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.create_aggregation_run
    -> app/strategy/aggregation/repository.py::StrategyAggregationRepository.create_material_pack
```

Hermes 寮€鍚椂棰濆璋冪敤锛?
```text
app/strategy/aggregation/service.py::_send_or_skip_hermes
    -> app/strategy/aggregation/hermes_formatter.py::build_strategy_aggregation_visible_body
    -> app/alerting/service.py::send_alert
```

## 4. 鏂板琛?
Migration锛?
`migrations/versions/20260518_18_create_strategy_aggregation_material_pack.py`

ORM锛?
`app/storage/mysql/models/strategy_aggregation.py`

### 4.1 strategy_aggregation_run

鐢ㄩ€旓細淇濆瓨涓€娆＄ 18 鑱氬悎杩愯銆佸€欓€夋柟鍚戙€侀闄╅棬绂併€佸啿绐併€佽瘉鎹€佸€欓€夊満鏅拰閫氱煡鐘舵€併€?
鍏抽敭瀛楁锛?
```text
aggregation_run_id
strategy_signal_run_id
snapshot_id
symbol / base_interval / higher_interval
aggregation_version
material_schema_version
indicator_version
candidate_scenario_version
status
analysis_hypothesis_direction
risk_level
risk_gate_status
conflict_level
input_*_count
effective_strategy_count
long_strategies_json
short_strategies_json
neutral_strategies_json
risk_strategies_json
candidate_scenarios_json
summary_json
evidence_json
conflict_json
validation_plan_json
message / error_message
hermes_* fields
created_at_utc / updated_at_utc
```

骞傜瓑鍞竴閿細

```text
strategy_signal_run_id
aggregation_version
material_schema_version
indicator_version
candidate_scenario_version
```

### 4.2 analysis_material_pack

鐢ㄩ€旓細淇濆瓨绗?19 浣跨敤鐨勭‘瀹氭€ф暟瀛︽潗鏂欏寘鍜岄棶棰樻竻鍗曘€?
鍏抽敭瀛楁锛?
```text
material_pack_id
aggregation_run_id
strategy_signal_run_id
snapshot_id
material_schema_version
indicator_version
material_json
question_json
validation_plan_json
summary_json
data_window_json
future_leakage_guard_json
status
created_at_utc / updated_at_utc
```

鏈〃涓嶄繚瀛樺ぇ妯″瀷杈撳嚭锛屼笉淇濆瓨鏈€缁堝缓璁紝涓嶄繚瀛樿处鎴锋垨浜ゆ槗鎵ц鏁版嵁銆?
## 5. 鑱氬悎瑙勫垯

绗竴鐗堟槸纭畾鎬ц鍒欙細

1. `strategy_signal_run.status` 鍙帴鍙?`success` / `partial_success`銆?2. `strategy_signal_result.strategy_status=success/no_signal` 瑙嗕负鍙弬涓庤仛鍚堛€?3. `not_implemented` 涓嶅鑷磋仛鍚堝け璐ワ紝浼氳鍏?`partial_success` 鑳屾櫙銆?4. `failed` / `invalid` 涓嶅弬涓庢柟鍚戞姇绁紝浣嗗啓鍏ヨ川閲忛棶棰樺垎缁勩€?5. `bullish_bias` 褰掑叆澶氬ご鍊欓€夎瘉鎹€?6. `bearish_bias` 褰掑叆绌哄ご鍊欓€夎瘉鎹€?7. `not_applicable` 涓斿甫椋庨櫓绛夌骇鐨勭瓥鐣ュ綊鍏ラ闄╃瓥鐣ャ€?8. 楂?鏋侀珮椋庨櫓浼樺厛鍚﹀喅鏂瑰悜锛屽€欓€夋柟鍚戦檷绾т负 `wait` 鎴?`stop_trading`銆?9. 澶氱┖鏄庢樉鍐茬獊鏃?`conflict_level=high`锛屽€欓€夋柟鍚戝€惧悜 `wait`銆?10. 鏈夋晥绛栫暐鏁伴噺涓?0 鏃?blocked銆?
鍊欓€夋柟鍚戣緭鍑猴細

```text
long
short
wait
stop_trading
```

鍊欓€夊満鏅繚瀛樻垚绔嬫潯浠躲€佸け鏁堟潯浠躲€佺洰鏍囪瀵熷尯銆佸垵姝ラ闄╂敹鐩婃瘮銆佷富瑕佽瘉鎹€佸弽鏂硅瘉鎹€侀鎺х姸鎬佸拰楠岃瘉璁″垝銆?杩欎簺瀛楁鍙敤浜庡悗缁獙璇佸拰绗?19 鍒嗘瀽锛屼笉鏄紑浠撱€佸钩浠撱€佸姞浠撱€佸噺浠撱€佹鐩堟垨姝㈡崯鎸囦护銆?
## 6. 鏁板鏉愭枡鍖?
`app/strategy/aggregation/material_builder.py::build_material_pack()` 浣跨敤 snapshot 杩樺師鍑虹殑 4h / 1d 绐楀彛纭畾鎬ц绠楋細

1. 鏈€杩?swing high / swing low銆?2. HH / HL / LH / LL 缁撴瀯鐘舵€併€?3. ATR_14銆?4. ATR 鐧惧垎姣斻€?5. 鏈€杩?3 / 6 / 20 鏍瑰钩鍧囨尟骞呫€?6. 鎸箙鎵╁紶鐘舵€併€?7. 鍩轰簬 swing 鐨勬敮鎾戝帇鍔涘€欓€夈€?8. 鍊欓€夋柟鍚戙€?9. 鍊欓€夊け鏁堟潯浠躲€?10. 鍊欓€夌洰鏍囪瀵熷尯銆?11. 鍒濇椋庨櫓鏀剁泭姣斻€?12. 绛栫暐鍐茬獊鐐广€?13. 鍙嶆柟璇佹嵁銆?14. 缁欑 19 鐨勯棶棰樻竻鍗曘€?
绗?18 涓嶆妸杩欎簺鎸囨爣浜ょ粰 prompt 涓存椂璁＄畻銆?
## 7. 绂佹鏈潵鍑芥暟

闃叉湭鏉ュ嚱鏁版鏌ュ彂鐢熷湪锛?
`app/strategy/aggregation/material_builder.py::build_future_leakage_guard()`

妫€鏌ュ唴瀹癸細

1. `max_base_open_time_used_ms <= market_context_snapshot.end_4h_open_time_ms`
2. `max_higher_open_time_used_ms <= market_context_snapshot.end_1d_open_time_ms`

鑻ュ彂鐜?snapshot 涔嬪悗鐨?K绾胯繘鍏ヨ繕鍘熺獥鍙ｏ紝绗?18 杩斿洖锛?
```text
status=blocked
error_code=future_leakage_guard_failed
```

骞朵笖涓嶄細鍐欏叆 `analysis_material_pack`銆?
姝ｅ父鏉愭枡鍖呬細鍐欏叆 `future_leakage_guard_json`锛岃褰曟渶澶т娇鐢?K绾挎椂闂淬€乻napshot 鐩爣杈圭晫鍜?`uses_future_klines=false`銆?
## 8. dry-run 涓?confirm-write

dry-run锛?
1. 榛樿妯″紡銆?2. 璇诲彇绗?16 run/result 鍜?snapshot K绾跨獥鍙ｃ€?3. 璁＄畻鑱氬悎鍊欓€夊拰鏉愭枡鍖呫€?4. 涓嶅啓 `strategy_aggregation_run`銆?5. 涓嶅啓 `analysis_material_pack`銆?6. 涓嶅彂閫?Hermes銆?
confirm-write锛?
1. 蹇呴』鏄惧紡浼犲叆 `--confirm-write`銆?2. 鍐欏叆 `strategy_aggregation_run`銆?3. 鎴愬姛鎴栭儴鍒嗘垚鍔熸椂鍐欏叆 `analysis_material_pack`銆?4. blocked 鏃跺彧鍐欒仛鍚堝璁¤锛屼笉鍐欐潗鏂欏寘銆?5. 鍐欏叆鍚庢寜閰嶇疆鍐冲畾鏄惁鍙戦€?Hermes銆?6. Hermes 澶辫触鍙褰曢€氱煡鐘舵€侊紝涓嶆敼鍙樿仛鍚堢姸鎬併€?
## 9. 鑷姩鎺ュ叆绗?17

鏂板閰嶇疆锛?
```env
STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=false
```

鎺ュ叆浣嶇疆锛?
`app/scheduler/runner.py::SchedulerRunner._run_strategy_aggregation_post_signal_if_needed()`

瑙﹀彂鏉′欢锛?
1. 绗?17 宸插畬鎴愩€?2. 绗?17 鐘舵€佷负 `success` 鎴?`partial_success`銆?3. 绗?17 缁撴灉鍖呭惈 `run_id`銆?4. `STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=true`銆?
鑷姩瑙﹀彂浣跨敤锛?
```text
trigger_source=scheduler
dry_run=false
confirm_write=true
created_by=strategy_signal_scheduler
```

绗?18 鑷姩澶辫触涓嶄細鏀瑰啓绗?17 event log锛屼篃涓嶄細鏀瑰啓 collector 缁撴灉銆?
## 10. Hermes 閰嶇疆

鏂板閰嶇疆锛?
```env
STRATEGY_AGGREGATION_HERMES_ENABLED=false
STRATEGY_AGGREGATION_HERMES_NOTIFY_SUCCESS=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_PARTIAL_SUCCESS=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_BLOCKED=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_FAILED=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_SKIPPED=false
```

閫氱煡绫诲瀷锛?
`AlertType.STRATEGY_AGGREGATION`

閫氱煡鍐呭鏄庣‘璇存槑锛?
1. 杩欐槸绛栫暐鑱氬悎缁撴灉銆?2. `analysis_hypothesis_direction` 鍙槸鍊欓€夋柟鍚戙€?3. 涓嶆槸鏈€缁堜氦鏄撳缓璁€?4. 鏈皟鐢ㄥぇ妯″瀷銆?5. 鏈繘鍏ュ缓璁敓鍛藉懆鏈熴€?6. 绯荤粺鏈嚜鍔ㄤ氦鏄撱€?
Hermes 缁撴灉鍐欏洖 `strategy_aggregation_run.hermes_status`銆乣hermes_message`銆乣hermes_error`銆乣hermes_sent_at_utc`銆?
## 11. 寮傚父澶勭悊

blocked锛?
1. `strategy_signal_run` 涓嶅瓨鍦ㄣ€?2. `strategy_signal_run.status` 涓嶆槸 `success` / `partial_success`銆?3. 缂哄皯 `snapshot_id`銆?4. `strategy_signal_result` 涓虹┖銆?5. 鏈夋晥绛栫暐鏁伴噺涓?0銆?6. snapshot 杩樺師澶辫触銆?7. snapshot K绾跨獥鍙ｄ笉瓒炽€?8. 闃叉湭鏉ュ嚱鏁版鏌ュけ璐ャ€?
failed锛?
1. 鏁版嵁搴撴煡璇㈠紓甯搞€?2. JSON 搴忓垪鍖栧紓甯搞€?3. 鏉愭枡璁＄畻鍑虹幇涓嶅彲鎭㈠浠ｇ爜寮傚父銆?4. 鎸佷箙鍖栧紓甯搞€?
partial_success锛?
1. 鑱氬悎鍜屾潗鏂欏寘鐢熸垚鎴愬姛銆?2. 浣嗚緭鍏ラ噷瀛樺湪 `failed` / `invalid` / `not_implemented` 绛栫暐銆?
skipped锛?
鍚屼竴涓細

```text
strategy_signal_run_id
aggregation_version
material_schema_version
indicator_version
candidate_scenario_version
```

宸叉湁绗?18 璁板綍鏃惰烦杩囥€傜涓€鐗堜笉浼氳嚜鍔ㄥ弽澶嶉噸璺?blocked / failed銆?
## 12. 鏌ョ湅缁撴灉

```sql
SELECT *
FROM strategy_aggregation_run
ORDER BY id DESC
LIMIT 5;
```

```sql
SELECT *
FROM analysis_material_pack
ORDER BY id DESC
LIMIT 5;
```

閲嶇偣鏌ョ湅锛?
1. `strategy_signal_run_id`
2. `snapshot_id`
3. `analysis_hypothesis_direction`
4. `risk_gate_status`
5. `conflict_level`
6. `candidate_scenarios_json`
7. `validation_plan_json`
8. `material_json`
9. `question_json`
10. `future_leakage_guard_json`

## 13. 鏈樁娈垫槑纭病鏈夊疄鐜?
鏈樁娈垫病鏈夐噸鏂拌繍琛岀 16 绛栫暐淇″彿銆?鏈樁娈垫病鏈夐噸鏂扮敓鎴愮 15 snapshot銆?鏈樁娈垫病鏈夎姹?Binance REST銆?鏈樁娈垫病鏈夎姹?Binance WebSocket銆?鏈樁娈垫病鏈変慨鏀规寮?K绾胯〃銆?鏈樁娈垫病鏈夎皟鐢?DeepSeek銆丟PT銆丆laude 鎴栧叾浠栧ぇ妯″瀷銆?鏈樁娈垫病鏈夌敓鎴愭渶缁堜氦鏄撳缓璁€?鏈樁娈垫病鏈夌鐞?active advice 鐢熷懡鍛ㄦ湡銆?鏈樁娈垫病鏈夎嚜鍔ㄤ氦鏄撱€?鏈樁娈垫病鏈夎鍙栬处鎴枫€佽鍗曘€佹寔浠撴垨 API 绉侀挜銆?
## 14. 娴嬭瘯

鏂板娴嬭瘯锛?
```text
tests/strategy_aggregation/test_strategy_aggregation_service.py
tests/scheduler/test_strategy_aggregation_auto_hook.py
```

瑕嗙洊鍐呭锛?
1. `success` / `partial_success` 鐨?strategy_signal_run 鍙互鑱氬悎銆?2. `blocked` / `failed` 涓嶅厑璁歌仛鍚堛€?3. Gann placeholder / not_implemented 涓嶅鑷磋仛鍚堝け璐ャ€?4. 鏈夋晥绛栫暐涓嶈冻浼?blocked銆?5. 涓婃父 fixture / 绗?16 缁撴灉鏄庣‘鍋忓 + 椋庨櫓浣?涓椂锛屼粎鎶曞奖 `long_hypothesis`銆?6. 涓婃父 fixture / 绗?16 缁撴灉鏄庣‘鍋忕┖鏃讹紝浠呮姇褰?`short_hypothesis`銆?7. 瓒嬪娍鍋忓 + 椋庨櫓鏋侀珮闄嶇骇 wait / stop_trading銆?8. 澶氱┖鍐茬獊鎻愬崌 conflict_level銆?9. material pack 鍖呭惈 swing銆丄TR銆佹尟骞呫€佹敮鎾戝帇鍔涖€佸€欓€夊満鏅拰闂娓呭崟銆?10. future-leakage guard 闃绘柇 snapshot 涔嬪悗鐨?K绾裤€?11. 鍚屼竴鐗堟湰缁勫悎涓嶉噸澶嶇敓鎴愩€?12. Hermes 鍏抽棴涓嶅彂閫侊紝寮€鍚彂閫佸苟璁板綍鐘舵€併€?13. CLI dry-run 涓嶅啓搴擄紝confirm-write 鎵嶅啓搴撱€?14. 绗?18 涓嶈皟鐢ㄧ 15 service銆佷笉璋冪敤绗?16 service銆佷笉璇锋眰 Binance銆佷笉璋冪敤澶фā鍨嬨€?15. 绗?18 涓嶇敓鎴?`final_advice` 瀛楁銆?16. scheduler 鍙湪绗?17 success / partial_success 鍚庢寜閰嶇疆瑙﹀彂绗?18銆?
榛樿娴嬭瘯浣跨敤 fake repository銆乫ake session 鍜?fake alert sender锛屼笉璁块棶鐪熷疄 MySQL銆丷edis銆丅inance銆丠ermes 鎴栧ぇ妯″瀷銆?
杩愯锛?
```bash
python -m pytest tests/strategy_aggregation tests/scheduler tests/strategy
```
