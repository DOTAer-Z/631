import pymongo
from app.database import get_db
from app.models.log_entry import LogEntry

# 连接 MongoDB
client = pymongo.MongoClient('mongodb://localhost:27017/')
db_mongo = client['log_analysis_results']
collection = db_mongo['logs']

# 获取数据库会话
db_sql = next(get_db())

# 读取 MongoDB 中的日志
print("从 MongoDB 读取日志...")
mongo_logs = list(collection.find({}))
print(f"MongoDB 中的日志数量: {len(mongo_logs)}")

# 读取数据库中的日志内容
existing_logs = db_sql.query(LogEntry.raw_content).all()
existing_contents = set([log.raw_content for log in existing_logs])
print(f"数据库中已存在 {len(existing_contents)} 条日志")

# 检查 MongoDB 中的日志是否已存在
print("\n检查 MongoDB 中的日志是否已存在于数据库中:")
for i, mongo_log in enumerate(mongo_logs):
    content = mongo_log.get('content', '')
    if content in existing_contents:
        print(f"日志 {i+1}: 已存在")
        print(f"  内容: {content[:50]}...")
    else:
        print(f"日志 {i+1}: 新日志")
        print(f"  内容: {content[:50]}...")

client.close()
db_sql.close()
