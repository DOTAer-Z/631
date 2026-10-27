# 启动脚本
import sys
import os

# 添加当前目录到 Python 路径
sys.path.insert(0, os.path.abspath('.'))

# 导入并运行 main.py
from app.main import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
