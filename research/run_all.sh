#!/bin/zsh
# 依序執行研究流程（Phase 0 → 3）。資料回補請先完成，見 README.md。
cd "${0:A:h}" || exit 1
PY=../backend/venv/bin/python
SCHEME="${1:-A}"   # Phase 3 要解釋的權重方案（看完 Phase 1 報告再決定）

$PY fetch_market_index.py 2023-06-01 || exit 1
$PY dimensions.py || exit 1
$PY phase1_temperature.py || exit 1
$PY phase2_forecast.py --schemes A,B,C || exit 1
$PY phase3_explain.py --scheme "$SCHEME" || exit 1
echo "完成，報告在 outputs/：phase1_report.md、phase2_report.md、phase3_report_${SCHEME}.md"
