import json
import redis
from app.config import settings

_redis_client = None

GRAPH_CACHE_KEY = "knowledge_graph_data"
GRAPH_CACHE_TTL = 300  # 5 分钟


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def cache_get(key: str):
    try:
        r = get_redis()
        val = r.get(key)
        return json.loads(val) if val else None
    except Exception:
        return None


def cache_set(key: str, value, ttl: int = 300):
    try:
        r = get_redis()
        r.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception:
        pass


def cache_delete(key: str):
    try:
        r = get_redis()
        r.delete(key)
    except Exception:
        pass
