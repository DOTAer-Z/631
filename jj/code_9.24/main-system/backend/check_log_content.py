from app.database import get_db
from app.models.log_entry import LogEntry

# 获取数据库会话
db = next(get_db())

# 查询包含特定内容的日志
search_content = "ERROR: Failed to connect to database"
logs = db.query(LogEntry).filter(LogEntry.raw_content.like(f"%{search_content}%")).all()

print(f"查询包含 '{search_content}' 的日志...")
print(f"找到 {len(logs)} 条匹配的日志")

for i, log in enumerate(logs):
    print(f"\n日志 {i+1}:")
    print(f"  内容: {log.raw_content}")
    print(f"  长度: {len(log.raw_content)}")

db.close()
