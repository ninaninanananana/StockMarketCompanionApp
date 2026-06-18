from datetime import datetime
from zoneinfo import ZoneInfo

from database import SessionLocal
from market_snapshot_importer import fetch_and_import as fetch_market_snapshot
from market_topic_importer import fetch_and_import as fetch_market_topics

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

now = datetime.now(TAIPEI_TZ)
print("開始執行")
print(now)

db = SessionLocal()
try:
    # market_snapshot：僅在下午五點（17:xx）執行一次
    if now.hour == 17:
        print("import_market_snapshot 執行開始")
        result = fetch_market_snapshot(db)
        print(result)
        print("import_market_snapshot 執行結束")
    else:
        print(f"import_market_snapshot 略過（目前 {now.hour} 時，僅下午五點執行）")

    print("import_market_topics 執行開始")
    result = fetch_market_topics(db)
    print(result)
    print("import_market_topics 執行結束")

except Exception as e:
    print(e)
finally:
    db.close()
