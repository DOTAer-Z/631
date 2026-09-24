import pymongo
import time

# MongoDB 连接配置
MONGO_URI = "mongodb://localhost:27017/"

print("尝试连接 MongoDB...")
try:
    # 连接 MongoDB，设置连接超时时间为 5 秒
    client = pymongo.MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=5000
    )
    print("成功连接 MongoDB")
    
    # 测试连接
    print("测试 MongoDB 连接...")
    client.admin.command('ping')
    print("MongoDB 连接测试成功")
    
    # 列出所有数据库
    print("\n所有数据库:")
    databases = client.list_database_names()
    print(databases)
    
    # 检查 log_analysis_results 数据库是否存在
    if 'log_analysis_results' in databases:
        print("\nlog_analysis_results 数据库存在")
        
        # 连接到 log_analysis_results 数据库
        db = client['log_analysis_results']
        
        # 列出所有集合
        print("\nlog_analysis_results 数据库中的集合:")
        collections = db.list_collection_names()
        print(collections)
        
        # 检查每个集合中的文档数量
        for collection_name in collections:
            collection = db[collection_name]
            count = collection.count_documents({})
            print(f"集合 {collection_name} 中的文档数量: {count}")
            
            # 如果集合中有文档，打印第一个文档的结构
            if count > 0:
                document = collection.find_one()
                print(f"集合 {collection_name} 的文档结构: {list(document.keys())}")
    else:
        print("\nlog_analysis_results 数据库不存在")
        
    # 关闭连接
    client.close()
    print("\n成功关闭 MongoDB 连接")
except Exception as e:
    print(f"连接 MongoDB 失败: {e}")
    import traceback
    traceback.print_exc()
