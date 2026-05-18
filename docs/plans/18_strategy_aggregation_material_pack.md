# 18_strategy_aggregation_material_pack.md

## 0. 2026-05-19 boundary correction: analysis hypotheses only

This plan must be read with the following corrected boundary:

1. Stage 18 does not implement real trading strategies.
2. Stage 18 does not independently judge long/short direction from Klines,
   support/resistance, context upside/downside, ATR, swing structure, or any indicator.
3. Stage 18 does not generate strategy signals, operation advice, or executable
   trading instructions.
4. `long` / `short` / `wait` / `stop_trading` in this stage are analysis
   hypotheses or direction placeholders projected from existing stage-16 rows
   or test fixtures only.
5. Scenario names should use `long_hypothesis`, `short_hypothesis`,
   `wait_hypothesis`, and `stop_trading_hypothesis`.
6. Every hypothesis must explicitly mark:

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

Persistent direction fields must use `analysis_hypothesis_direction` and
`analysis_hypothesis_confidence`. These fields are not strategy signals, not
trading advice, not executable decisions, and cannot be promoted directly by
Hermes, Admin, replay, or later modules.

Any support/resistance ratio must be named as context only, such as
`context_upside_downside_ratio`, with semantics
`support_resistance_context_only_not_entry_exit_signal`.

`stop_trading_hypothesis` is allowed only as an upstream risk-gate projection
from existing stage-16 rows or explicit test fixtures. Stage 18 must not read
Klines, volatility, or support/resistance and independently decide to stop
trading.

Real Gann, trend, support/resistance, risk-control, and other strategies must
be developed later as independent plugin-style strategy classes.

## 1. 闃舵鍚嶇О

绗?18 闃舵锛歚strategy_aggregation_material_pack`

涓枃鍚嶇О锛氱瓥鐣ヨ仛鍚堛€佸€欓€夊満鏅笌澶фā鍨嬫暟瀛︽潗鏂欏寘鏋勫缓銆?
鏈増瀹氫綅锛氬湪鍘熺 18 plans 鍩虹涓婏紝寮哄寲鈥滅瓥鐣ユ湁鏁堟€с€佸垽鏂纭€с€佸悗缁彲楠岃瘉銆佸悗缁彲澶嶇洏鈥濈殑瑕佹眰銆傜 18 涓嶅彧鏄妸澶氫釜绛栫暐淇″彿鍋氭憳瑕侊紝鑰屾槸瑕佹妸姣忔鍊欓€夊垽鏂彉鎴愬彲浠ヨ鍚庣画澶фā鍨嬪鏌ャ€佺敓鍛藉懆鏈熷眰寮曠敤銆佸鐩樼郴缁熻瘎浼扮殑缁撴瀯鍖栬瘉鎹€?
---

## 2. 闃舵鐩爣

绗?18 闃舵鍦ㄧ 16 鐙珛绛栫暐淇″彿宸茬粡鐢熸垚銆佺 17 绛栫暐淇″彿璋冨害宸茬粡瀹屾垚涔嬪悗锛岃礋璐ｅ畬鎴愪笁浠朵簨锛?
1. **绛栫暐鑱氬悎**锛氬澶氫釜鐙珛绛栫暐淇″彿杩涜纭畾鎬ц仛鍚堬紝褰㈡垚鍊欓€夋柟鍚戙€侀闄╃姸鎬併€佺瓥鐣ヤ竴鑷存€с€佺瓥鐣ュ啿绐併€侀鎺у惁鍐崇粨鏋溿€?2. **鍊欓€夊満鏅瀯寤?*锛氬熀浜庣瓥鐣ヤ俊鍙峰拰甯傚満蹇収锛屽舰鎴愬彲楠岃瘉鐨勫€欓€夊満鏅紝鍖呮嫭鎴愮珛鏉′欢銆佸け鏁堟潯浠躲€佺洰鏍囪瀵熷尯銆佸垵姝ラ闄╂敹鐩婃瘮銆佷富瑕佽瘉鎹€佸弽鏂硅瘉鎹€?3. **鏁板鏉愭枡鍖呮瀯寤?*锛氬熀浜?`MarketContextSnapshot` 瀵瑰簲鐨?K绾跨獥鍙ｏ紝璁＄畻 swing銆丄TR銆佹尟骞呫€佹敮鎾戝帇鍔涖€佺粨鏋勭姸鎬併€侀棶棰樻竻鍗曪紝骞跺啓鍏?`analysis_material_pack`锛屼緵绗?19 澶фā鍨嬪垎鏋愬眰浣跨敤銆?
绗?18 闃舵涓嶆槸鏈€缁堝缓璁眰锛屼笉璐熻矗寤鸿鐢熷懡鍛ㄦ湡锛屼笉璋冪敤 DeepSeek / GPT / Claude 绛夊ぇ妯″瀷锛屼笉鑷姩浜ゆ槗锛屼笉璇诲彇璐︽埛銆佽鍗曟垨鎸佷粨銆?
---

## 3. 閾捐矾瀹氫綅

```text
绗?15 灞傦細MarketContextSnapshot 甯傚満涓婁笅鏂囧揩鐓?    鈫?绗?16 灞傦細StrategySignalRun / StrategySignalResult 鐙珛绛栫暐淇″彿
    鈫?绗?17 灞傦細StrategySignalScheduler 绛栫暐淇″彿璋冨害缂栨帓
    鈫?绗?18 灞傦細StrategyAggregationRun + AnalysisMaterialPack 绛栫暐鑱氬悎銆佸€欓€夊満鏅笌鏁板鏉愭枡鍖?    鈫?绗?19 灞傦細LLMAnalysisRun 澶фā鍨嬪垎鏋?    鈫?绗?20 灞傦細AdviceLifecycle 鏈€缁堝缓璁敓鍛藉懆鏈?```

绗?18 鍙秷璐瑰凡鏈夌粨鏋滐細

```text
strategy_signal_run
strategy_signal_result
snapshot_id
MarketContextSnapshot 瀵瑰簲鐨?4h / 1d K绾跨獥鍙ｆ垨蹇収寮曠敤鑼冨洿
```

绗?18 涓嶅緱锛?
```text
閲嶆柊璺戠 16 绛栫暐淇″彿
閲嶆柊鐢熸垚绗?15 snapshot
璇锋眰 Binance REST / WebSocket
璋冪敤澶фā鍨?鐢熸垚鏈€缁堜氦鏄撳缓璁?绠＄悊 active advice 鐢熷懡鍛ㄦ湡
```

濡傛灉绗?15 蹇収鍙繚瀛樼獥鍙ｈ寖鍥存垨寮曠敤鍏崇郴锛岃€屾病鏈変繚瀛樺畬鏁?K绾垮唴瀹癸紝绗?18 鍙互鏍规嵁 `snapshot_id` 瀵瑰簲鐨勬椂闂磋寖鍥翠粠鏈湴鏁版嵁搴撹鍙栧凡鏀剁洏 K绾匡紝浣嗗繀椤绘弧瓒筹細

```text
鍙兘璇诲彇 snapshot 鏃剁偣鍙婁箣鍓嶅凡缁忕‘璁ゆ敹鐩樼殑鏁版嵁
涓嶅緱璇诲彇 target close 涔嬪悗鐨勬湭鏉?K绾?涓嶅緱淇敼浠讳綍 K绾挎暟鎹?璁＄畻缁撴灉蹇呴』鍐欏叆 analysis_material_pack
```

---

## 4. 鏍稿績鍘熷垯

### 4.1 鍊欓€夋柟鍚戜笉鏄渶缁堝缓璁?
绗?18 鍙互杈撳嚭锛?
```text
analysis_hypothesis_direction = long / short / wait / stop_trading / neutral / mixed
```

浣嗚繖鍙槸鈥滆仛鍚堝眰鍊欓€夋柟鍚戔€濓紝涓嶆槸鏈€缁堜氦鏄撳缓璁€?
绗?18 涓ョ杈撳嚭鎴栨殫绀猴細

```text
寤鸿寮€澶?寤鸿寮€绌?寤鸿鍔犱粨
寤鸿鍑忎粨
寤鸿骞充粨
姝㈢泩鎸囦护
姝㈡崯鎸囦护
```

绗?18 鍙互杈撳嚭锛?
```text
鍊欓€夋垚绔嬫潯浠?鍊欓€夊け鏁堟潯浠?鍊欓€夌洰鏍囪瀵熷尯
鍒濇椋庨櫓鏀剁泭姣?```

浣嗗繀椤绘槑纭叾鎬ц川鏄€欓€夊満鏅紝涓嶆槸鎿嶄綔鎸囦护銆?
### 4.2 鎵€鏈夊€欓€夊垽鏂繀椤诲彲楠岃瘉

浠讳綍 `analysis_hypothesis_direction` 閮戒笉鑳藉彧缁欎竴涓柟鍚戙€傚繀椤婚厤濂椾繚瀛橈細

```text
鍒嗘瀽鍋囪瑙傚療鏉′欢 activation_check
鍒嗘瀽鍋囪澶辨晥妫€鏌?invalidation_check
鐩爣瑙傚療鍖?target_observation_zone
鍒濇椋庨櫓鏀剁泭姣?context_upside_downside_ratio
涓昏璇佹嵁 supporting_evidence
鍙嶆柟璇佹嵁 opposing_evidence
椋庨櫓璇存槑 risk_notes
鍚庣画楠岃瘉璁″垝 validation_plan
```

绀轰緥锛?
```json
{
  "analysis_hypothesis_direction": "long",
  "activation_check": "浠呬緵鍚庣画鍒嗘瀽灞傝瀵燂紝涓嶆槸鎵ц瑙﹀彂鏉′欢",
  "invalidation_check": "浠呬緵鍚庣画鍒嗘瀽灞傛鏌ワ紝涓嶆槸浜ゆ槗姝㈡崯鎸囦护",
  "target_observation_zone": "鏈€杩?swing high 鑷充笂鏂瑰帇鍔涘尯闂?,
  "context_upside_downside_ratio": 1.8,
  "supporting_evidence": ["瓒嬪娍缁撴瀯鍋忓", "浠锋牸浠嶄綅浜庢渶杩?higher low 涓婃柟"],
  "opposing_evidence": ["涓婃柟鍘嬪姏鎺ヨ繎", "鐭湡鎸箙鎵╁紶"],
  "risk_notes": ["璇ュ€欓€夋柟鍚戜笉鑳借В閲婁负绔嬪嵆寮€浠撴寚浠?],
  "validation_plan": ["鍚庣画瑙傚療 1 鍒?6 鏍?4h K绾挎槸鍚﹁Е鍙戞垚绔嬫垨澶辨晥鏉′欢"]
}
```

### 4.3 绂佹鏈潵鍑芥暟

绗?18 鐨勬墍鏈夋寚鏍囧拰鍊欓€夊満鏅彧鑳藉熀浜?`snapshot_id` 瀵瑰簲鏃剁偣鍙鐨勬暟鎹€?
绂佹锛?
```text
璇诲彇 target close 涔嬪悗鐨?K绾垮弬涓?swing / ATR / 鏀拺鍘嬪姏璁＄畻
浣跨敤鍚庣画浠锋牸璧板娍鍙嶆帹褰撴椂鐨勫€欓€夋柟鍚?鐢ㄥ綋鍓嶆暟鎹簱鏈€鏂?K绾挎薄鏌撳巻鍙?snapshot 鐨勬潗鏂欏寘
```

蹇呴』淇濊瘉锛?
```text
鍚屼竴涓?strategy_signal_run_id + snapshot_id 鍦ㄤ笉鍚屾椂闂撮噸璺戯紝绗?18 鐨勬牳蹇冩潗鏂欏簲璇ョǔ瀹氫竴鑷淬€?```

濡傛灉搴曞眰 K绾垮彂鐢熷悎娉曚慨璁㈡垨琛ラ綈锛屽簲閫氳繃鏂扮殑 material version / rerun 鏈哄埗璁板綍锛屼笉寰楁棤澹拌鐩栨棫鏉愭枡銆?
### 4.4 鎸囨爣銆佽仛鍚堝拰鏉愭枡鍖呭繀椤荤増鏈寲

绗?18 蹇呴』璁板綍锛?
```text
aggregation_version
material_schema_version
indicator_version
candidate_scenario_version
```

鍘熷洜锛欰TR銆乻wing銆佹敮鎾戝帇鍔涖€侀闄╂敹鐩婃瘮鍜岄鎺у惁鍐宠鍒欐湭鏉ヤ竴瀹氫細璋冩暣銆傛病鏈夌増鏈彿锛屽悗缁鐩樻棤娉曞垽鏂煇娆″€欓€夊垽鏂槸鎸夊摢涓畻娉曠敓鎴愮殑銆?
### 4.5 椋庢帶鍙互鍚﹀喅鏂瑰悜

椋庢帶绫荤瓥鐣ュ拰娉㈠姩鐜囬闄╃瓥鐣ュ彲浠ユ妸鍊欓€夋柟鍚戜粠 `long / short` 鏀逛负锛?
```text
wait
stop_trading
```

绀轰緥锛?
```text
瓒嬪娍缁撴瀯鍋忓锛屼絾娉㈠姩鐜囬闄╂瀬楂?=> analysis_hypothesis_direction = wait
=> risk_gate_status = blocked_by_volatility
```

鑱氬悎灞傚繀椤绘槑纭憡璇夌敤鎴峰拰鍚庣画妯″瀷锛氬埌搴曟槸鍝被椋庨櫓瀵艰嚧绛夊緟鎴栧仠姝氦鏄撱€?
### 4.6 璁板綍鍒嗘锛屼笉鍙緭鍑虹粨璁?
绗?18 蹇呴』璁板綍锛?
```text
鏀寔澶氬ご鐨勭瓥鐣?鏀寔绌哄ご鐨勭瓥鐣?鏀寔绛夊緟鐨勭瓥鐣?鍙彁绀洪闄╃殑绛栫暐
鏈疄鐜扮瓥鐣?澶辫触鎴栨棤鏁堢瓥鐣?鍐茬獊绛夌骇
椋庢帶鍚﹀喅鐘舵€?```

鍚庣画澶嶇洏鏃讹紝涓嶅彧鐪嬧€滆仛鍚堟渶缁堝亸澶?鍋忕┖鈥濓紝杩樿鑳借瘎浼帮細

```text
鍝釜绛栫暐璐＄尞浜嗘纭垽鏂?鍝釜绛栫暐缁忓父鍒堕€犲櫔闊?椋庢帶鍚﹀喅鏄惁鐪熺殑鍑忓皯浜嗛敊璇氦鏄?绛栫暐涔嬮棿鏄惁閲嶅琛ㄨ揪鍚屼竴涓洜瀛?```

### 4.7 Hermes 瀹屽叏閰嶇疆鍖?
绗?18 鏀寔 Hermes 閫氱煡锛屼絾鍙戦€佷笌鍚︾敱 `.env` 鎺у埗銆傜敤鎴峰彲浠ュ悓鏃跺紑鍚 17銆佺 18銆佺 19銆佺 20 鐨勯€氱煡锛屼篃鍙互鍏ㄩ儴鍏抽棴銆?
绗?18 涓嶅己鍒跺彧鍙戜竴鏉℃秷鎭紝浣嗘枃妗ｅ繀椤绘彁閱掞細鎴愮啛闃舵閫氬父寤鸿鍏抽棴搴曞眰閫氱煡锛屽彧淇濈暀鏈€楂樺喅绛栧眰閫氱煡鍜屽紓甯搁€氱煡銆?
---

## 5. 杈撳叆鏁版嵁

绗?18 鐨勬牳蹇冭緭鍏ワ細

```text
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
strategy_signal_result 鍒楄〃
MarketContextSnapshot 瀵瑰簲鐨?4h / 1d K绾跨獥鍙ｆ垨蹇収寮曠敤鑼冨洿
```

鏈夋晥杈撳叆鐘舵€侊細

```text
strategy_signal_run.status in success / partial_success
```

绂佹杈撳叆鐘舵€侊細

```text
blocked
failed
skipped
running
```

濡傛灉杈撳叆鐨?`strategy_signal_run` 鐘舵€佷笉鍚堟硶锛岀 18 搴旇繑鍥?`blocked`锛屽苟璁板綍鍘熷洜銆?
---

## 6. 杈撳嚭鏁版嵁

绗?18 鑷冲皯鏂板涓ょ被鎸佷箙鍖栫粨鏋滐細

```text
strategy_aggregation_run
analysis_material_pack
```

### 6.1 strategy_aggregation_run

鑱岃矗锛氳褰曟湰杞瓥鐣ヨ仛鍚堛€佸€欓€夋柟鍚戙€侀闄╁惁鍐炽€佸啿绐佹儏鍐靛拰鍊欓€夊満鏅憳瑕併€?
寤鸿瀛楁锛?
```text
id
aggregation_run_id
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
aggregation_version
material_schema_version
indicator_version
candidate_scenario_version
status
input_strategy_count
input_success_count
input_failed_count
input_invalid_count
input_not_implemented_count
effective_strategy_count
analysis_hypothesis_direction
analysis_hypothesis_confidence
risk_level
risk_gate_status
conflict_level
direction_consensus
supporting_strategies_json
opposing_strategies_json
risk_strategies_json
not_implemented_strategies_json
failed_strategies_json
invalid_strategies_json
candidate_scenarios_json
validation_plan_json
summary_json
message
error_code
error_message
trace_id
trigger_source
created_by
created_at_utc
updated_at_utc
```

寤鸿鐘舵€佸€硷細

```text
success
partial_success
blocked
failed
skipped
```

璇存槑锛?
```text
success锛氳仛鍚堟垚鍔燂紝杈撳叆绛栫暐淇″彿璐ㄩ噺婊¤冻瑕佹眰銆?partial_success锛氳仛鍚堝畬鎴愶紝浣嗗瓨鍦ㄩ儴鍒嗙瓥鐣?failed / invalid / not_implemented銆?blocked锛氳緭鍏ユ潯浠朵笉婊¤冻锛屼緥濡?strategy_signal_run 鐘舵€佷笉鍚堟硶銆佺己灏?snapshot_id銆佹湁鏁堢瓥鐣ヤ笉瓒炽€並绾跨獥鍙ｄ笉瓒炽€?failed锛氭暟鎹簱寮傚父銆丣SON 搴忓垪鍖栧紓甯搞€佷唬鐮佸紓甯告垨涓嶅彲鎭㈠璁＄畻寮傚父銆?skipped锛氬箓绛夊懡涓紝宸叉湁鐩稿悓杈撳叆鐨勮仛鍚堢粨鏋溿€?```

### 6.2 analysis_material_pack

鑱岃矗锛氳褰曠粰绗?19 澶фā鍨嬩娇鐢ㄧ殑缁撴瀯鍖栨暟瀛︽潗鏂欏寘鍜岄棶棰樻竻鍗曘€?
寤鸿瀛楁锛?
```text
id
material_pack_id
aggregation_run_id
strategy_signal_run_id
snapshot_id
symbol
base_interval
higher_interval
schema_version
indicator_version
status
material_json
question_json
summary_json
data_window_json
future_leakage_guard_json
trace_id
created_by
created_at_utc
updated_at_utc
```

`material_json` 淇濆瓨纭畾鎬ц绠楁潗鏂欍€?
`question_json` 淇濆瓨绗?19 闃舵瑕侀棶澶фā鍨嬬殑闂娓呭崟銆?
`data_window_json` 蹇呴』璁板綍浣跨敤鐨?K绾胯寖鍥达紝渚嬪锛?
```json
{
  "base_interval": "4h",
  "base_open_time_start_utc": "...",
  "base_open_time_end_utc": "...",
  "base_kline_count": 180,
  "higher_interval": "1d",
  "higher_open_time_start_utc": "...",
  "higher_open_time_end_utc": "...",
  "higher_kline_count": 180
}
```

`future_leakage_guard_json` 蹇呴』璁板綍闃叉湭鏉ュ嚱鏁版鏌ョ粨鏋滐紝渚嬪锛?
```json
{
  "max_base_open_time_used_utc": "...",
  "snapshot_target_base_open_time_utc": "...",
  "uses_future_klines": false
}
```

---

## 7. 鑱氬悎閫昏緫绗竴鐗?
绗?18 绗竴鐗堥噰鐢ㄧ‘瀹氭€ц鍒欙紝涓嶅紩鍏ユ満鍣ㄥ涔狅紝涓嶈皟鐢ㄥぇ妯″瀷銆?
### 7.1 鏈夋晥绛栫暐璇嗗埆

鏍规嵁 `strategy_signal_result.strategy_status` 鍖哄垎锛?
```text
success锛氭湁鏁堢瓥鐣ヤ俊鍙?failed锛氱瓥鐣ユ墽琛屽け璐?invalid锛氱瓥鐣ヨ緭鍑烘棤鏁?not_implemented锛氱瓥鐣ユ湭瀹炵幇
```

`not_implemented` 涓嶅簲瀵艰嚧鑱氬悎澶辫触銆傚綋鍓嶉樁娈垫睙鎭╃瓥鐣ュ彲鑳戒粛涓哄崰浣嶇瓥鐣ワ紝鍥犳 `partial_success` 鏄彲鎺ュ彈鐘舵€併€?
### 7.2 鏂瑰悜褰掔被

鑱氬悎灞傝鍙栫瓥鐣ョ粨鏋滀腑鐨勶細

```text
direction_bias
signal_strength
risk_level
strategy_status
reason_json
evidence_json
```

绗竴鐗堝彲浠ユ寜绠€鍗曡鍒欏綊绫伙細

```text
bullish / long_bias锛氭敮鎸佸澶?bearish / short_bias锛氭敮鎸佺┖澶?neutral / range / wait锛氭敮鎸佺瓑寰呮垨涓€?risk_only锛氬彧鎻愮ず椋庨櫓锛屼笉鐩存帴鍙備笌澶氱┖鎶曠エ
not_implemented锛氫笉鍙備笌鏂瑰悜鎶曠エ锛屼絾璁″叆鏈疄鐜扮瓥鐣?failed / invalid锛氫笉鍙備笌鏂瑰悜鎶曠エ锛屼絾璁″叆璐ㄩ噺闂
```

### 7.3 鍊欓€夋柟鍚戣鍒?
鍩虹瑙勫垯锛?
```text
澶氬ご鏈夋晥绛栫暐鏁伴噺 > 绌哄ご鏈夋晥绛栫暐鏁伴噺锛屼笖椋庢帶鏈惁鍐筹細analysis_hypothesis_direction = long
绌哄ご鏈夋晥绛栫暐鏁伴噺 > 澶氬ご鏈夋晥绛栫暐鏁伴噺锛屼笖椋庢帶鏈惁鍐筹細analysis_hypothesis_direction = short
澶氱┖鎺ヨ繎鎴栨湁鏁堢瓥鐣ヤ笉瓒筹細analysis_hypothesis_direction = wait / mixed
椋庢帶鏋侀珮鎴栭闄╃瓥鐣ュ惁鍐筹細analysis_hypothesis_direction = wait 鎴?stop_trading
```

鍊欓€夋柟鍚戠疆淇″害寤鸿锛?
```text
low / medium / high
```

绗竴鐗堜笉瑕佽繃搴︾簿缁嗐€傜疆淇″害鍙兘琛ㄧず鑱氬悎灞備俊鍙蜂竴鑷存€у己寮憋紝涓嶄唬琛ㄧ泩鍒╂鐜囥€?
### 7.4 椋庢帶浼樺厛瑙勫垯

寤鸿瀛楁锛?
```text
risk_gate_status = pass / caution / blocked_by_volatility / blocked_by_conflict / insufficient_data
```

绀轰緥锛?
```text
瓒嬪娍鍋忓锛屾尝鍔ㄧ巼椋庨櫓楂橈細analysis_hypothesis_direction = wait锛宺isk_gate_status = blocked_by_volatility
瓒嬪娍鍋忕┖锛岄闄╁彲鎺э細analysis_hypothesis_direction = short锛宺isk_gate_status = pass
澶氱┖绛栫暐涓ラ噸鍐茬獊锛歝andidate_direction = wait锛宺isk_gate_status = blocked_by_conflict
鏈夋晥鏁版嵁涓嶈冻锛歝andidate_direction = wait锛宺isk_gate_status = insufficient_data
```

### 7.5 鍐茬獊绛夌骇

寤鸿鍊硷細

```text
none
low
medium
high
```

绗竴鐗堣鍒欙細

```text
鏈夋晥绛栫暐鍏ㄩ儴鍚屽悜锛歯one / low
鏈変竴涓富瑕佺瓥鐣ョ浉鍙嶏細medium
澶氱┖绛栫暐鏁伴噺鎺ヨ繎锛屼笖淇″彿寮哄害閮戒笉浣庯細high
椋庢帶鍚﹀喅鏂瑰悜锛歮edium / high
鏈夋晥绛栫暐杩囧皯锛歮edium锛屼笖 risk_gate_status = insufficient_data
```

---

## 8. 鏁板鏉愭枡鍖呯涓€鐗?
绗?18 绗竴鐗堣嚦灏戣绠椾互涓嬫潗鏂欙紝骞跺啓鍏?`analysis_material_pack.material_json`銆?
### 8.1 K绾跨獥鍙ｆ憳瑕?
浠?`MarketContextSnapshot` 瀵瑰簲鐨?4h / 1d K绾跨獥鍙ｄ腑鎻愬彇锛?
```text
latest_open
latest_high
latest_low
latest_close
latest_volume
recent_base_klines_summary
recent_higher_klines_summary
base_window_count
higher_window_count
```

涓嶅緱璇锋眰 Binance REST 鎴?WebSocket銆?
### 8.2 swing high / swing low

绗竴鐗堜娇鐢ㄧ‘瀹氭€у眬閮ㄩ珮浣庣偣瑙勫垯銆?
寤鸿鍙傛暟锛?
```text
swing_left_bars = 2
swing_right_bars = 2
```

瀹氫箟锛?
```text
swing high锛氭煇鏍?K绾?high 楂樹簬宸︿晶 N 鏍瑰拰鍙充晶 N 鏍?high
swing low锛氭煇鏍?K绾?low 浣庝簬宸︿晶 N 鏍瑰拰鍙充晶 N 鏍?low
```

杈撳嚭锛?
```json
{
  "recent_swing_highs": [],
  "recent_swing_lows": [],
  "structure_labels": ["HH", "HL", "LH", "LL"],
  "structure_state": "uptrend / downtrend / range / mixed / insufficient_data"
}
```

### 8.3 ATR 涓庢尝鍔ㄧ巼

璁＄畻 4h 鐨?ATR_14锛?
```text
TR = max(high - low, abs(high - previous_close), abs(low - previous_close))
ATR_14 = 鏈€杩?14 鏍?TR 骞冲潎鍊?ATR_PERCENT = ATR_14 / latest_close * 100
```

杈撳嚭锛?
```json
{
  "atr_14": 0,
  "atr_percent": 0,
  "volatility_state": "low / normal / expanded / extreme"
}
```

### 8.4 鎸箙鍙樺寲

鍗曟牴鎸箙锛?
```text
range_percent = (high - low) / close * 100
```

璁＄畻锛?
```text
鏈€杩?3 鏍瑰钩鍧囨尟骞?鏈€杩?6 鏍瑰钩鍧囨尟骞?鏈€杩?20 鏍瑰钩鍧囨尟骞?```

杈撳嚭锛?
```json
{
  "avg_range_percent_3": 0,
  "avg_range_percent_6": 0,
  "avg_range_percent_20": 0,
  "range_expansion_state": "contracting / normal / expanding / extreme"
}
```

### 8.5 鏀拺鍘嬪姏鍊欓€?
绗竴鐗堝熀浜庢渶杩?swing high / swing low 鐢熸垚鍊欓€夋敮鎾戝帇鍔涳細

```text
鏈€杩戞湁鏁?swing lows 鈫?support_candidates
鏈€杩戞湁鏁?swing highs 鈫?resistance_candidates
```

杈撳嚭搴斿寘鍚細

```text
price
open_time_utc
source_interval
distance_to_latest_close_percent
source = swing_high / swing_low
```

### 8.6 鍊欓€夊満鏅?
`candidate_scenarios_json` 鑷冲皯鍖呭惈锛?
```json
{
  "analysis_hypothesis_direction": "long / short / wait / stop_trading / mixed",
  "candidate_scenarios": [
    {
      "scenario_type": "long_hypothesis / short_hypothesis / wait_hypothesis / stop_trading_hypothesis",
      "activation_check": "鍒嗘瀽鍋囪瑙傚療鏉′欢",
      "invalidation_check": "鍒嗘瀽鍋囪澶辨晥妫€鏌?,
      "target_observation_zone": "鍊欓€夌洰鏍囪瀵熷尯",
      "context_upside_downside_ratio": 0,
      "supporting_evidence": [],
      "opposing_evidence": [],
      "risk_notes": [],
      "validation_plan": []
    }
  ]
}
```

娉ㄦ剰锛?
```text
invalidation_check 涓嶆槸浜ゆ槗姝㈡崯鎸囦护
target_observation_zone 涓嶆槸姝㈢泩鎸囦护
context_upside_downside_ratio 鍙槸鍊欓€夊満鏅川閲忚瘎浼帮紝涓嶆槸涓嬪崟渚濇嵁
```

### 8.7 澶фā鍨嬮棶棰樻竻鍗?
`question_json` 鑷冲皯鍖呭惈锛?
```text
1. 褰撳墠鍊欓€夋柟鍚戞槸鍚﹁浠锋牸缁撴瀯鏀寔锛?2. 褰撳墠娉㈠姩鐜囨槸鍚︽敮鎸佸€欓€夊け鏁堟潯浠剁殑璺濈锛?3. 褰撳墠鐩爣瑙傚療鍖轰笌鍊欓€夊け鏁堟潯浠朵箣闂寸殑鍒濇椋庨櫓鏀剁泭姣旀槸鍚﹀悎鐞嗭紵
4. 褰撳墠缁撴瀯鏄惁瀛樺湪鍋囩獊鐮存垨杩芥定/杩借穼椋庨櫓锛?5. 澶氫釜绛栫暐鏄惁鐪熸鐙珛锛岃繕鏄噸澶嶈〃杈惧悓涓€涓秼鍔垮洜瀛愶紵
6. 濡傛灉绛栫暐淇″彿涓庨鎺у啿绐侊紝搴斾紭鍏堢瓑寰呰繕鏄仠姝氦鏄擄紵
7. 鍝簺鏉′欢蹇呴』鎴愮珛锛屾墠鍏佽浠?wait 杞负 long 鎴?short锛?8. 褰撳墠鍊欓€夊満鏅殑鍙嶆柟璇佹嵁鏄惁瓒充互鍚﹀喅鏂瑰悜锛?9. 濡傛灉褰撳墠鍊欓€夊垽鏂敊璇紝鏈€鍙兘閿欏湪鍝噷锛?```

绗?19 澶фā鍨嬪眰蹇呴』浼樺厛璇诲彇杩欎簺闂锛岃€屼笉鏄妯″瀷鑷敱鍙戞尌鍐欎綔鏂囥€?
---

## 9. 鍚庣画璇勪及棰勭暀

绗?18 涓嶅仛瀹屾暣澶嶇洏锛屼絾蹇呴』涓哄悗缁鐩樼暀涓嬪彲璇勪及鏉愭枡銆?
姣忎釜鍊欓€夊満鏅繀椤昏兘鍦ㄥ悗缁璇勪及锛?
```text
鏄惁婊¤冻 activation_check 瀵瑰簲鐨勮瀵熸潯浠?鏄惁鍏堟弧瓒?invalidation_check 瀵瑰簲鐨勫け鏁堟鏌?鍚庣画 1 / 3 / 6 鏍?4h K绾挎渶澶ф诞鐩?鍚庣画 1 / 3 / 6 鏍?4h K绾挎渶澶ф诞浜?鐩爣瑙傚療鍖烘槸鍚﹀埌杈?椋庨櫓鏀剁泭姣旀槸鍚︾幇瀹?椋庢帶鍚﹀喅鏄惁鏈夋晥
```

绗?18 鍙互鍦?`validation_plan_json` 涓鐣欙細

```json
{
  "evaluation_horizons_base_bars": [1, 3, 6],
  "activation_check": "鍩轰簬 4h 鏀剁洏浠峰垽鏂垚绔嬫潯浠舵槸鍚﹁Е鍙?,
  "invalidation_check": "鍩轰簬 4h 鏀剁洏浠峰垽鏂け鏁堟潯浠舵槸鍚﹁Е鍙?,
  "floating_pnl_check": "浠ュ悗缁?K绾?high/low 浼扮畻鏈€澶ф湁鍒?涓嶅埄娉㈠姩",
  "notes": "鏈樁娈靛彧鐢熸垚楠岃瘉璁″垝锛屼笉鎵ц澶嶇洏"
}
```

杩欎笉浼氭彁鍓嶅疄鐜板鐩樼郴缁燂紝浣嗚兘淇濊瘉绗?18 鐨勮緭鍑轰互鍚庡彲浠ヨ璇勪及銆?
---

## 10. 鑷姩瑙﹀彂瑙勫垯

绗?18 鍙互鑷姩鎺ュ湪绗?17 鍚庨潰杩愯锛屼絾蹇呴』閫氳繃 `.env` 閰嶇疆鎺у埗銆?
鏂板閰嶇疆锛?
```env
STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=false
```

瑙勫垯锛?
```text
绗?17 status = success / partial_success
    鈫?涓?STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=true
    鈫?鑷姩璋冪敤绗?18 StrategyAggregationService
```

浠ヤ笅绗?17 鐘舵€佷笉寰楄嚜鍔ㄨ繍琛岀 18锛?
```text
waiting_upstream
blocked
failed
skipped
running
```

绗?18 鑷姩瑙﹀彂鏃讹紝涓嶅緱褰卞搷绗?17 鐨?event log 鐘舵€併€傜 17 涓庣 18 蹇呴』淇濇寔鐙珛瀹¤閾捐矾銆?
---

## 11. CLI 鎵嬪姩鍏ュ彛

鏂板鎵嬪姩鍏ュ彛锛?
```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <run_id> \
  --trigger-source cli \
  --dry-run
```

纭鍐欏叆锛?
```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <run_id> \
  --trigger-source cli \
  --confirm-write
```

CLI 瑙勫垯锛?
```text
榛樿 dry-run
dry-run 涓嶅啓 strategy_aggregation_run
dry-run 涓嶅啓 analysis_material_pack
confirm-write 鎵嶅厑璁稿啓鍏?涓嶅厑璁?CLI 鐩存帴璋冪敤绗?15
涓嶅厑璁?CLI 鐩存帴璋冪敤绗?16
涓嶅厑璁?CLI 璇锋眰 Binance
涓嶅厑璁?CLI 淇敼 K绾?```

CLI 杈撳嚭鑷冲皯鍖呭惈锛?
```text
status
exit_code
aggregation_run_id
material_pack_id
strategy_signal_run_id
snapshot_id
analysis_hypothesis_direction
risk_gate_status
conflict_level
message
error_message
```

---

## 12. Hermes 閫氱煡

绗?18 鏀寔 Hermes 閫氱煡锛屼絾蹇呴』閰嶇疆鍖栥€?
鏂板閰嶇疆锛?
```env
STRATEGY_AGGREGATION_HERMES_ENABLED=false
STRATEGY_AGGREGATION_HERMES_NOTIFY_SUCCESS=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_PARTIAL_SUCCESS=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_BLOCKED=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_FAILED=true
STRATEGY_AGGREGATION_HERMES_NOTIFY_SKIPPED=false
```

绗?18 Hermes 閫氱煡鍐呭瀹氫綅锛?
```text
绛栫暐鑱氬悎缁撴灉閫氱煡
```

涓嶅緱鍖呰鎴愭渶缁堜氦鏄撳缓璁€?
閫氱煡鍐呭蹇呴』鏄庣‘锛?
```text
杩欐槸绛栫暐鑱氬悎灞傚€欓€夊垽鏂紝涓嶆槸鏈€缁堜氦鏄撳缓璁€?鏈皟鐢ㄥぇ妯″瀷銆?鏈繘鍏ュ缓璁敓鍛藉懆鏈熴€?绯荤粺鏈嚜鍔ㄤ氦鏄撱€?```

鍏佽鐢ㄦ埛閫氳繃 `.env` 鍚屾椂寮€鍚 17 鍜岀 18 閫氱煡銆備竴涓?4h 鍛ㄦ湡鍙兘鏀跺埌绗?17 绛栫暐淇″彿閫氱煡鍜岀 18 鑱氬悎閫氱煡銆傛槸鍚﹀紑鍚敱鐢ㄦ埛鑷繁鎺у埗銆?
Hermes 鍙戦€佺粨鏋滃繀椤诲啓鍏?`strategy_aggregation_run` 鎴栭厤濂楅€氱煡瀛楁锛屼緥濡傦細

```text
hermes_enabled
hermes_status
hermes_error
hermes_sent_at_utc
```

---

## 13. 骞傜瓑瑙勫垯

绗?18 蹇呴』闃叉閲嶅鑱氬悎銆?
鍞竴韬唤寤鸿锛?
```text
strategy_signal_run_id + aggregation_version + material_schema_version + indicator_version + candidate_scenario_version
```

濡傛灉宸茬粡瀛樺湪 `success / partial_success` 鐨勮仛鍚堢粨鏋滐紝鍒欎笉閲嶅鍐欏叆銆?
绗竴鐗堝 `blocked / failed` 涓嶈嚜鍔ㄩ噸璺戙€傚悗缁闇€閲嶈窇锛屽簲澧炲姞鏄庣‘鐨勪汉宸ラ噸璺戝叆鍙ｆ垨 retry 绛栫暐銆?
---

## 14. 鐘舵€佷笌閿欒澶勭悊

### 14.1 blocked 鍦烘櫙

浠ヤ笅鎯呭喌搴?blocked锛?
```text
strategy_signal_run 涓嶅瓨鍦?strategy_signal_run.status 涓嶆槸 success / partial_success
strategy_signal_run 娌℃湁 snapshot_id
strategy_signal_result 涓虹┖
鏈夋晥绛栫暐鏁伴噺涓嶈冻
snapshot 瀵瑰簲 K绾跨獥鍙ｄ笉瓒充互璁＄畻鍩虹鏉愭枡
闃叉湭鏉ュ嚱鏁版鏌ュけ璐?鏈湴鏁版嵁搴撶己灏戝繀瑕佸凡鏀剁洏 K绾?```

### 14.2 failed 鍦烘櫙

浠ヤ笅鎯呭喌搴?failed锛?
```text
鏁版嵁搴撳紓甯?JSON 搴忓垪鍖栧紓甯?浠ｇ爜杩愯寮傚父
涓嶅彲鎭㈠鐨勮绠楀紓甯?```

### 14.3 partial_success 鍦烘櫙

浠ヤ笅鎯呭喌鍙互 partial_success锛?
```text
鑱氬悎涓绘祦绋嬪畬鎴?浣嗛儴鍒嗙瓥鐣?failed / invalid / not_implemented
鏉愭枡鍖呬富瀛楁鐢熸垚鎴愬姛锛屼絾鏌愪簺闈炴牳蹇冩潗鏂欎笉瓒?鍊欓€夊満鏅敓鎴愭垚鍔燂紝浣嗛闄╂敹鐩婃瘮鍥犵己灏戠洰鏍囦綅鍙兘鏍囪涓?null
```

---

## 15. 绂佹浜嬮」

绗?18 闃舵涓ョ锛?
```text
璋冪敤 DeepSeek / GPT / Claude 绛夊ぇ妯″瀷
鐢熸垚鏈€缁堜氦鏄撳缓璁?绠＄悊 active advice 鐢熷懡鍛ㄦ湡
寮€浠撱€佸钩浠撱€佸姞浠撱€佸噺浠撱€佹挙鍗?璇诲彇璐︽埛銆佽鍗曘€佹寔浠撱€丄PI 绉侀挜
璇锋眰 Binance REST
璇锋眰 Binance WebSocket
淇敼 market_kline_4h 鎴?market_kline_1d 姝ｅ紡 K绾胯〃
鏂板 manual_repair
浜哄伐淇敼 K绾挎暟鎹?閲嶆柊璺戠 16 绛栫暐淇″彿
閲嶆柊鐢熸垚绗?15 MarketContextSnapshot
闄嶄綆绗?15 蹇収璐ㄩ噺闂ㄧ
淇敼绗?16 dry-run / confirm-write 璇箟
浣跨敤 target close 涔嬪悗鐨勬湭鏉?K绾?```

---

## 16. 寤鸿浠ｇ爜缁撴瀯

鍙寜鐜版湁椤圭洰缁撴瀯璋冩暣锛屽缓璁柊澧炴垨淇敼锛?
```text
app/strategy/aggregation/
  types.py
  service.py
  repository.py
  material_builder.py
  indicators.py
  candidate_scenario_builder.py
  hermes_formatter.py

scripts/run_strategy_aggregation.py

tests/strategy_aggregation/
  test_strategy_aggregation_service.py
  test_material_builder.py
  test_indicators.py
  test_candidate_scenario_builder.py
  test_strategy_aggregation_cli.py
```

濡傞」鐩凡鏈夋洿鍚堥€傜殑鐩綍瑙勮寖锛屼紭鍏堥伒瀹?`AGENTS.md` 鍜岀幇鏈夋ā鍧楄竟鐣屻€?
---

## 17. 杩佺Щ瑕佹眰

鏂板 Alembic migration锛屽垱寤猴細

```text
strategy_aggregation_run
analysis_material_pack
```

琛ㄧ粨鏋勫繀椤绘敮鎸侊細

```text
run_id 杩借釜
snapshot_id 杩借釜
strategy_signal_run_id 杩借釜
trace_id 杩借釜
鐗堟湰瀛楁
JSON 鏉愭枡淇濆瓨
鍊欓€夊満鏅繚瀛?楠岃瘉璁″垝淇濆瓨
鐘舵€佷繚瀛?閿欒淇℃伅淇濆瓨
Hermes 鎶曢€掔姸鎬佷繚瀛?骞傜瓑绾︽潫
```

涓嶅緱鐩存帴鎵嬪啓鐢熶骇鏁版嵁搴?SQL 缁曡繃 Alembic銆?
---

## 18. 娴嬭瘯瑕佹眰

鑷冲皯瑕嗙洊锛?
```text
1. success 鐨?strategy_signal_run 鍙互杩涘叆鑱氬悎銆?2. partial_success 鐨?strategy_signal_run 鍙互杩涘叆鑱氬悎銆?3. blocked / failed 鐨?strategy_signal_run 涓嶅厑璁歌仛鍚堛€?4. strategy_signal_run 缂哄け snapshot_id 鏃?blocked銆?5. strategy_signal_result 涓虹┖鏃?blocked銆?6. Gann placeholder / not_implemented 涓嶅鑷磋仛鍚堝け璐ャ€?7. 瓒嬪娍鍋忓 + 椋庨櫓浣庯紝analysis_hypothesis_direction 鍙互涓?long銆?8. 瓒嬪娍鍋忓 + 椋庨櫓鏋侀珮锛宑andidate_direction 搴斿彉鎴?wait 鎴?stop_trading銆?9. 澶氱┖绛栫暐鍐茬獊鏃?conflict_level 鍗囬珮銆?10. material_pack 鍖呭惈 swing high / swing low銆?11. material_pack 鍖呭惈 ATR_14 鍜?ATR_PERCENT銆?12. material_pack 鍖呭惈 3 / 6 / 20 鏍瑰钩鍧囨尟骞呫€?13. material_pack 鍖呭惈鏀拺鍘嬪姏鍊欓€夈€?14. material_pack 鍖呭惈鍊欓€夊満鏅€?15. 鍊欓€夊満鏅寘鍚垚绔嬫潯浠躲€佸け鏁堟潯浠躲€佺洰鏍囪瀵熷尯銆佸垵姝ラ闄╂敹鐩婃瘮銆?16. material_pack 鍖呭惈澶фā鍨嬮棶棰樻竻鍗曘€?17. material_pack 鍖呭惈 data_window_json銆?18. material_pack 鍖呭惈 future_leakage_guard_json銆?19. 闃叉湭鏉ュ嚱鏁版鏌ヨ兘闃绘浣跨敤 target close 涔嬪悗鐨?K绾裤€?20. 鍚屼竴涓?strategy_signal_run_id + 鐗堟湰缁勫悎涓嶉噸澶嶇敓鎴愯仛鍚堢粨鏋溿€?21. dry-run 涓嶅啓 strategy_aggregation_run銆?22. dry-run 涓嶅啓 analysis_material_pack銆?23. confirm-write 鎵嶅啓鍏ャ€?24. Hermes 鍏抽棴鏃朵笉鍙戦€併€?25. Hermes 寮€鍚椂鍙戦€佺瓥鐣ヨ仛鍚堥€氱煡骞惰褰曞彂閫佺粨鏋溿€?26. 绗?18 涓嶈皟鐢ㄧ 15銆?27. 绗?18 涓嶈皟鐢ㄧ 16銆?28. 绗?18 涓嶈皟鐢ㄥぇ妯″瀷銆?29. 绗?18 涓嶈姹?Binance銆?30. 绗?18 涓嶇敓鎴愭渶缁堜氦鏄撳缓璁瓧娈点€?```

---

## 19. 楠屾敹鍛戒护

寮€鍙戝畬鎴愬悗鑷冲皯杩愯锛?
```bash
python -m compileall app migrations scripts tests
python -m pytest tests/strategy_aggregation tests/strategy tests/scheduler
python -m scripts.check_project_invariants
python -m alembic upgrade head
```

濡?`tests/strategy_aggregation` 灏氫笉瀛樺湪锛屽簲鍒涘缓瀵瑰簲娴嬭瘯鐩綍銆?
---

## 20. 鎵嬪姩楠岃瘉娴佺▼

绗?16 / 绗?17 宸茬粡鐢熸垚 `strategy_signal_run` 鍚庯紝鍙互鎵嬪姩杩愯锛?
```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <run_id> \
  --trigger-source cli \
  --dry-run
```

纭杈撳嚭鍚堢悊鍚庡啀鎵ц锛?
```bash
python -m scripts.run_strategy_aggregation \
  --strategy-signal-run-id <run_id> \
  --trigger-source cli \
  --confirm-write
```

鐒跺悗鏌ュ簱锛?
```sql
SELECT * FROM strategy_aggregation_run ORDER BY id DESC LIMIT 5;
SELECT * FROM analysis_material_pack ORDER BY id DESC LIMIT 5;
```

閲嶇偣纭锛?
```text
aggregation_run.strategy_signal_run_id 姝ｇ‘
aggregation_run.snapshot_id 姝ｇ‘
analysis_hypothesis_direction 鍚堢悊
risk_gate_status 鍚堢悊
conflict_level 鍚堢悊
candidate_scenarios_json 闈炵┖
validation_plan_json 闈炵┖
analysis_material_pack.material_json 闈炵┖
analysis_material_pack.question_json 闈炵┖
analysis_material_pack.data_window_json 闈炵┖
analysis_material_pack.future_leakage_guard_json 鏄剧ず鏈娇鐢ㄦ湭鏉?K绾?娌℃湁鏈€缁堜氦鏄撳缓璁瓧娈?娌℃湁鑷姩浜ゆ槗琛屼负
```

---

## 21. 涓庡悗缁樁娈靛叧绯?
绗?18 鐨勮緭鍑哄皢浣滀负绗?19 澶фā鍨嬪垎鏋愬眰鐨勮緭鍏ャ€?
绗?19 涓嶅簲閲嶆柊浠?K绾胯〃涓存椂鎷兼帴鏍稿績鏁板鏉愭枡锛岃€屽簲璇诲彇锛?
```text
analysis_material_pack.material_json
analysis_material_pack.question_json
strategy_aggregation_run.summary_json
strategy_aggregation_run.candidate_scenarios_json
```

绗?20 鏈€缁堝缓璁敓鍛藉懆鏈熷眰鍐嶈鍙栵細

```text
strategy_signal_run
strategy_aggregation_run
analysis_material_pack
llm_analysis_run
```

鏈€缁堝喅瀹氾細

```text
new / continue / update / close / invalidate / complete / wait
```

绗?18 涓嶅仛杩欎簺鐢熷懡鍛ㄦ湡鍔ㄤ綔銆?
---

## 22. 缁撴潫鏍囧噯

绗?18 闃舵瀹屾垚鏍囧噯锛?
```text
1. 鍙互鍩轰簬宸叉湁 strategy_signal_run 鐢熸垚 strategy_aggregation_run銆?2. 鍙互鍩轰簬 snapshot / K绾跨獥鍙ｇ敓鎴?analysis_material_pack銆?3. 鍙互鐢熸垚鍙獙璇佺殑 candidate_scenarios_json銆?4. 鍙互鐢熸垚 validation_plan_json锛屼负鍚庣画澶嶇洏棰勭暀渚濇嵁銆?5. 鏀寔 CLI dry-run 涓?confirm-write銆?6. 鏀寔绗?17 鍚庣疆鑷姩瑙﹀彂锛屼絾鍙?.env 鎺у埗銆?7. 鏀寔 Hermes 绛栫暐鑱氬悎閫氱煡锛屼絾鍙?.env 鎺у埗銆?8. 骞傜瓑瑙勫垯鏈夋晥锛屽悓涓€ strategy_signal_run + 鐗堟湰缁勫悎涓嶉噸澶嶇敓鎴愯仛鍚堢粨鏋溿€?9. 鍗曞厓娴嬭瘯瑕嗙洊鎴愬姛銆侀樆鏂€佸け璐ャ€佸箓绛夈€丠ermes銆佹潗鏂欒绠椼€侀槻鏈潵鍑芥暟銆佸€欓€夊満鏅€?10. 涓嶈皟鐢ㄥぇ妯″瀷锛屼笉鐢熸垚鏈€缁堝缓璁紝涓嶈嚜鍔ㄤ氦鏄撱€?```
