import pymongo

# 连接 MongoDB
client = pymongo.MongoClient('mongodb://localhost:27017/')
db = client['log_analysis_results']
collection = db['logs']

# 读取 MongoDB 中的日志
print("从 MongoDB 读取日志内容...")
mongo_logs = list(collection.find({}))

for i, log in enumerate(mongo_logs):
    content = log.get('content', '')
    print(f"\nMongoDB 日志 {i+1}:")
    print(f"  内容: '{content}'")
    print(f"  长度: {len(content)}")

client.close()
