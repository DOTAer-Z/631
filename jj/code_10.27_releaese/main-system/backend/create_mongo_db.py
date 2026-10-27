import pymongo

# MongoDB 连接配置
MONGO_URI = "mongodb://localhost:27017/"

print("尝试连接 MongoDB...")
try:
    # 连接 MongoDB
    client = pymongo.MongoClient(MONGO_URI)
    print("成功连接 MongoDB")
    
    # 测试连接
    client.admin.command('ping')
    print("MongoDB 连接测试成功")
    
    # 创建 log_analysis_results 数据库
    db = client['log_analysis_results']
    print("成功创建 log_analysis_results 数据库")
    
    # 创建 logs 集合
    collection = db['logs']
    print("成功创建 logs 集合")
    
    # 插入一些测试数据
    test_data = [
        {"content": "ERROR: Failed to connect to database", "level": "ERROR"},
        {"content": "WARN: High CPU usage detected", "level": "WARN"},
        {"content": "INFO: Application started successfully", "level": "INFO"},
        {"content": "ERROR: Network connection lost", "level": "ERROR"},
        {"content": "WARN: Memory usage above threshold", "level": "WARN"},
        {"content": "INFO: User logged in", "level": "INFO"},
        {"content": "ERROR: File not found", "level": "ERROR"},
        {"content": "WARN: Disk space running low", "level": "WARN"},
        {"content": "INFO: Data backup completed", "level": "INFO"}
    ]
    
    result = collection.insert_many(test_data)
    print(f"成功插入 {len(result.inserted_ids)} 条测试数据")
    
    # 验证数据是否插入成功
    count = collection.count_documents({})
    print(f"集合中现在有 {count} 条文档")
    
    # 打印插入的文档
    print("\n插入的文档:")
    for document in collection.find():
        print(document)
    
    # 关闭连接
    client.close()
    print("\n成功关闭 MongoDB 连接")
except Exception as e:
    print(f"操作 MongoDB 失败: {e}")
    import traceback
    traceback.print_exc()
