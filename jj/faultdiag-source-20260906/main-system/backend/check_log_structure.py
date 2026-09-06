import pymongo

MONGO_URI = "mongodb://localhost:27017/"

print("检查 log_analysis_results 数据库中的数据结构...")
try:
    client = pymongo.MongoClient(MONGO_URI)
    db = client['log_analysis_results']

    collections = db.list_collection_names()
    print(f"集合列表: {collections}")

    for collection_name in collections:
        collection = db[collection_name]
        count = collection.count_documents({})
        print(f"\n集合 {collection_name} 有 {count} 条文档")

        if count > 0:
            # 获取所有文档
            documents = list(collection.find({}))
            print(f"文档数量: {len(documents)}")

            # 打印所有文档的完整结构
            for i, doc in enumerate(documents):
                print(f"\n文档 {i+1}:")
                for key, value in doc.items():
                    print(f"  {key}: {value}")

    client.close()
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
