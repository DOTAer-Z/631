from app.database import get_db
from app.models.log_entry import LogEntry

# 获取数据库会话
db = next(get_db())

# 查询日志数量
log_count = db.query(LogEntry).count()
print(f"数据库中日志数量: {log_count}")

# 查询前5条日志的内容
logs = db.query(LogEntry).limit(5).all()
print("\n前5条日志内容:")
for i, log in enumerate(logs):
    print(f"\n日志 {i+1}:")
    print(f"  内容: {log.raw_content}")
    print(f"  摘要: {log.summary}")
    print(f"  创建时间: {log.created_at}")

db.close()
