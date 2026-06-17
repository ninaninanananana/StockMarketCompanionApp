from datetime import datetime

from database import SessionLocal
from market_snapshot_importer import fetch_and_import as fetch_market_snapshot
from market_topic_importer import fetch_and_import as fetch_market_topics

print("開始執行")
print(datetime.now())

db = SessionLocal()
try:
    print("import_market_snapshot 執行開始")
    result = fetch_market_snapshot(db)
    print(result)
    print("import_market_snapshot 執行結束")

    print("import_market_topics 執行開始")
    result = fetch_market_topics(db)
    print(result)
    print("import_market_topics 執行結束")

except Exception as e:
    print(e)
finally:
    db.close()
