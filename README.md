# hermes_btc_agent_v2

Hermes + DeepSeek BTC 鍚堢害绛栫暐杈呭姪绯荤粺銆?
鏈」鐩殑褰撳墠闃舵鐩爣鏄厛寤鸿鏁版嵁搴曞骇鍜岃繍琛屽簳搴э細閰嶇疆銆佹棩蹇椼€丮ySQL銆丷edis銆丠ermes 鎶ヨ銆丅inance REST K绾块噰闆嗐€並绾胯川閲忔鏌ャ€佹墜鍔ㄥ洖琛ャ€佸閲忛噰闆嗐€乄ebSocket 10s 浠锋牸鐩戞帶銆佹瘡鏃?K绾夸竴鑷存€у鏍搞€?
## 鏍稿績杈圭晫

1. 鏈郴缁熶笉鏄嚜鍔ㄤ氦鏄撶郴缁熴€?2. 绯荤粺涓嶅緱鑷姩涓嬪崟銆佸钩浠撱€佽皟浠撱€佸姞浠撱€佸噺浠撱€佹挙鍗曘€?3. 姝ｅ紡 K绾挎暟鎹彧鑳芥潵鑷?Binance REST 瀹樻柟 K绾挎帴鍙ｃ€?4. 10s 鏈€鏂颁环鏍肩洃鎺т娇鐢?Binance WebSocket锛屼笉浣跨敤 REST 姣?10 绉掕疆璇环鏍笺€?5. K绾垮紓甯稿彧鑳芥姤璀︼紝涓嶈兘浜哄伐鏀规暟锛屼笉鑳借嚜鍔ㄤ慨澶嶃€?6. DeepSeek 鍜屽叾浠栧ぇ妯″瀷涓嶅弬涓庡熀纭€閲囬泦銆佸熀纭€鎶ヨ銆佸洖琛ャ€佸鏍搞€佷环鏍肩洃鎺с€?
## 蹇呰鏂囨。椤哄簭

Codex 鎴栧叾浠?AI 缂栫▼鍔╂墜寮€鍙戝墠蹇呴』鎸夐『搴忛槄璇伙細

1. `docs/rules/project_invariants.md`
2. `AGENTS.md`
3. 褰撳墠瑕佸疄鐜扮殑 `docs/plans/*.md`
4. 鐩稿叧 `docs/decisions/*.md`
5. 鐩稿叧 `docs/requirements/*.md`
6. 鐩稿叧 `docs/architecture/*.md`
7. 鍓嶅簭闃舵鐨?`docs/implementation/*.md`

## Runtime

- Python: 3.11.x
- Package management: `pyproject.toml`
- Virtual environment: project-local `.venv`
- Do not reuse Hermes internal virtual environment.

## 鏂囨。鐩綍

```text
docs/rules/          椤圭洰閾佸緥
docs/requirements/   涓氬姟闇€姹?docs/architecture/   绯荤粺缁撴瀯銆佹ā鍧楄竟鐣屻€佹暟鎹祦
docs/decisions/      宸茬‘璁ょ殑閲嶈鍐崇瓥
docs/plans/          Codex 鏂藉伐钃濆浘
docs/implementation/ 姣忎釜妯″潡瀹屾垚鍚庣殑瀹炵幇璇存槑
```

## 褰撳墠 plans

```text
01_project_skeleton.md
02_core_config_logging.md
03_infra_mysql_redis.md
04_alerting_through_hermes.md
05_binance_rest_client.md
06_market_kline_4h.md
07_kline_quality_checker.md
08_4h_backfill.md
09_4h_incremental_collector.md
10_price_monitor_10s.md
11_daily_kline_integrity_check.md
15_market_context_snapshot.md
16_strategy_signal_framework.md
17_strategy_signal_scheduler_plan.md
18_strategy_aggregation_material_pack.md
```

## 绗?18 闃舵鎵嬪姩鑱氬悎妫€鏌?
绗?18 鍙仛绛栫暐鑱氬悎鍜屾暟瀛︽潗鏂欏寘鏋勫缓锛屼笉璋冪敤澶фā鍨嬶紝涓嶇敓鎴愭渶缁堜氦鏄撳缓璁紝涓嶈嚜鍔ㄤ氦鏄撱€?

Stage 18 stores `analysis_hypothesis_direction` only as an analysis
hypothesis for later review. `long_hypothesis` / `short_hypothesis` /
`wait_hypothesis` / `stop_trading_hypothesis` are not strategy signals, not
trading advice, and not executable decisions.

榛樿 dry-run锛?
```bash
python -m scripts.run_strategy_aggregation --strategy-signal-run-id <run_id> --trigger-source cli
```

纭鍐欏叆锛?
```bash
python -m scripts.run_strategy_aggregation --strategy-signal-run-id <run_id> --trigger-source cli --confirm-write
```

鑷姩鎺ュ湪绗?17 鍚庨潰闇€瑕佹樉寮忓紑鍚細

```env
STRATEGY_AGGREGATION_AUTO_RUN_ENABLED=true
```

## 01 椤圭洰楠ㄦ灦鏈湴妫€鏌?
绗竴闃舵鍙缓绔嬮」鐩鏋讹紝涓嶅疄鐜颁笟鍔¤兘鍔涖€?
鏈湴妫€鏌ュ懡浠わ細

```bash
python -m scripts.check_project_skeleton
python -m scripts.check_project_invariants
pytest
```

涓婅堪鍛戒护鍙仛鏈湴鏂囦欢銆佸寘瀵煎叆鍜岃鍒欐枃鏈鏌ワ紝涓嶈繛鎺ョ湡瀹?MySQL锛屼笉杩炴帴鐪熷疄 Redis锛屼笉璇锋眰 Binance锛屼笉鍙戦€?Hermes銆?
## Git 宸ヤ綔鏂瑰紡

1. 鍒嗘敮鍒涘缓銆佸垏鎹€佸悎骞躲€佹帹閫佺敱鐢ㄦ埛浜哄伐鎵ц銆?2. Codex 鍙湪鐢ㄦ埛褰撳墠鎸囧畾鍒嗘敮鍜屽綋鍓嶆寚瀹?plan 鑼冨洿鍐呬慨鏀规枃浠躲€?3. 姣忎釜 plan 瀹屾垚鍚庡厛瀹℃煡锛屽啀鍚堝苟鍒?`master`銆?4. 濡傛灉鍙戠幇鏂囨。鍐茬獊鎴栬鍒欎笉娓咃紝鍏堜慨鏂囨。锛屼笉瑕佺‖鍐欎唬鐮併€?
## 瀹夊叏鎻愰啋

绂佹鎻愪氦 `.env`銆佺湡瀹炲瘑閽ャ€佺湡瀹?webhook銆乼oken銆佺敓浜ф棩蹇椼€佽处鎴蜂俊鎭€佽鍗曚俊鎭€佹寔浠撲俊鎭€?
