from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from urllib.parse import quote

from app.config import settings

# =============================================================================
# 主数据库：PostgreSQL（已由 MySQL 迁移而来）
# seed.py 中的 engine / SessionLocal 均指向此处
# =============================================================================
def build_primary_database_url(config=settings) -> str:
    if config.DATABASE_URL:
        return config.DATABASE_URL
    encoded_password = quote(config.POSTGRES_PASSWORD, safe="")
    return (
        f"postgresql+psycopg2://{config.POSTGRES_USER}:{encoded_password}"
        f"@{config.POSTGRES_HOST}:{config.POSTGRES_PORT}/{config.POSTGRES_DB}"
    )


_primary_url = build_primary_database_url()

engine = create_engine(_primary_url, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 依赖注入：提供 PostgreSQL SQLAlchemy session（原 get_db 接口不变）。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# get_mysql_db 保留为 get_db 的别名，供历史接口显式使用（现已指向 PostgreSQL）
get_mysql_db = get_db


# =============================================================================
# 文档存储：原 MongoDB（log_entries / log_windows / log_uploads 等集合）
# 现改为 PostgreSQL JSONB 兼容层（app/mongo_compat.py），复用上面的 engine。
# 对调用方保持 pymongo 风格 API（db["coll"].find/insert_one/... 不变）。
# =============================================================================
_mongo_client = None


def get_mongo_client():
    """返回 PG-JSONB 支撑的 pymongo 兼容 client 单例。"""
    global _mongo_client
    if _mongo_client is None:
        from app.mongo_compat import PgMongoClient
        _mongo_client = PgMongoClient(engine)
    return _mongo_client


def get_mongo_db():
    """返回 PG-JSONB 支撑的 database 对象，可直接操作集合（接口同 pymongo）。"""
    return get_mongo_client()[settings.MONGO_DB]
