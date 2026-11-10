"""
排程進入點，由 crontab 每小時 :30 呼叫 run_stock.sh 執行。

建議 crontab：
    30 9-13,17,21 * * 1-5  <backend>/run_stock.sh >> <backend>/log.txt 2>&1

| 時間（台北）  | 動作                                                              |
|---------------|-------------------------------------------------------------------|
| 09:30–13:30   | market_topic_importer（盤中熱門主題）                              |
| 17:30         | 1. market_topic_importer(force)：收盤結算主題，並計算産業擴散度    |
|               | 2. market_snapshot_importer：寫入當日快照與評分（帶入擴散度）      |
| 21:30         | market_snapshot_importer：補齊盤後才更新的融資融券、當沖比並重算分數 |
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from database import SessionLocal
from market_snapshot_importer import fetch_and_import as fetch_market_snapshot
from market_topic_importer import fetch_and_import as fetch_market_topics

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

CLOSING_HOUR = 17      # 收盤結算
AFTER_HOURS_HOUR = 21  # 盤後資料補齊


def _run(name: str, fn, *args, **kwargs):
    """執行單一步驟；失敗只記錄，不中斷後續步驟。"""
    print(f"{name} 執行開始")
    try:
        result = fn(*args, **kwargs)
        print(result)
        return result
    except Exception as e:
        print(f"{name} 失敗: {e}")
        return None
    finally:
        print(f"{name} 執行結束")


def main() -> None:
    now = datetime.now(TAIPEI_TZ)
    print("開始執行")
    print(now)

    db = SessionLocal()
    try:
        if now.hour == CLOSING_HOUR:
            topics = _run("import_market_topics（收盤結算）", fetch_market_topics, db, force=True)
            diffusion = topics.get("industry_diffusion") if isinstance(topics, dict) else None
            _run("import_market_snapshot", fetch_market_snapshot, db, industry_diffusion=diffusion)
        elif now.hour == AFTER_HOURS_HOUR:
            _run("import_market_snapshot（盤後補齊）", fetch_market_snapshot, db)
        else:
            _run("import_market_topics", fetch_market_topics, db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
