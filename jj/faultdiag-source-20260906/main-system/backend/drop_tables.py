from app.database import engine
from sqlalchemy import text

# 手动删除 runs 和 ingestions 表
print("Dropping tables...")
try:
    with engine.connect() as conn:
        # 删除 ingestions 表（如果存在外键依赖，需要先删除）
        conn.execute(text("DROP TABLE IF EXISTS ingestions"))
        print("ingestions table dropped successfully!")
        
        # 删除 runs 表
        conn.execute(text("DROP TABLE IF EXISTS runs"))
        print("runs table dropped successfully!")
    
    print("All tables dropped successfully!")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
