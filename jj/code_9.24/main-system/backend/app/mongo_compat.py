"""
PostgreSQL-backed MongoDB compatibility shim.

原系统用 MongoDB 存储 log_entries / log_windows / log_uploads / log_analysis_results
等文档集合。迁移到「单 PostgreSQL」后，这里用一张 JSONB 表 `mongo_docs` 承载所有
集合，并实现 pymongo 在本项目里实际用到的子集 API，使得 `get_mongo_db()` 的调用方
（约 20 处 db["coll"].find/insert_one/...）无需改动。

覆盖的能力：
  - insert_one / insert_many
  - find_one(filter, projection) / find(filter, projection) -> cursor.sort/skip/limit
  - count_documents(filter)
  - update_one(filter, {"$set": ...}, upsert=)
  - replace_one(filter, doc, upsert=)
  - delete_one / delete_many
  - create_index(...)  (no-op)
  - 过滤算子：等值、点号嵌套键、$eq/$ne/$gt/$gte/$lt/$lte/$in/$nin/$exists
  - projection：include / exclude 两种模式 + _id 控制
  - sort：字符串或 [(field, dir), ...]；skip / limit
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text


_MISSING = object()

# datetime 在 JSONB 中无原生类型，写入时打标签 {"__dt__": iso}，读出时还原为 datetime，
# 使 log_window_service 等依赖真实 datetime 做时间运算/$type 判断的代码正常工作。
_DT_TAG = "__dt__"


def _encode(obj: Any) -> Any:
    """写入前：递归把 datetime/date 转成 {"__dt__": iso} 标签。"""
    if isinstance(obj, datetime):
        return {_DT_TAG: obj.isoformat()}
    if isinstance(obj, date):
        return {_DT_TAG: obj.isoformat()}
    if isinstance(obj, dict):
        return {k: _encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    return obj


def _decode(obj: Any) -> Any:
    """读出后：递归把 {"__dt__": iso} 还原为 datetime。"""
    if isinstance(obj, dict):
        if len(obj) == 1 and _DT_TAG in obj:
            v = obj[_DT_TAG]
            if isinstance(v, str):
                dt = _try_parse_dt(v)
                if dt is not None:
                    return dt
            return v
        return {k: _decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(v) for v in obj]
    return obj


def _dumps(doc: Dict[str, Any]) -> str:
    """序列化为可写入 JSONB 的字符串（datetime 先打标签）。"""
    return json.dumps(_encode(doc), ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# 建表（首次使用时幂等创建）
# ---------------------------------------------------------------------------
def ensure_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS mongo_docs (
                pk         BIGSERIAL PRIMARY KEY,
                collection TEXT NOT NULL,
                doc_id     TEXT NOT NULL,
                run_id     TEXT,
                doc        JSONB NOT NULL,
                CONSTRAINT uq_mongo_docs UNIQUE (collection, doc_id)
            )
            """
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_mongo_docs_coll_run "
            "ON mongo_docs (collection, run_id)"
        ))


# ---------------------------------------------------------------------------
# 值规整 / 比较辅助
# ---------------------------------------------------------------------------
def _get_path(doc: Any, dotted: str) -> Any:
    cur = doc
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _try_parse_dt(s: str):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _coerce_pair(a: Any, b: Any) -> Tuple[Any, Any]:
    """当一侧是 datetime、另一侧是 ISO 字符串时，尽量对齐成可比较类型。"""
    if isinstance(a, datetime) and isinstance(b, str):
        pb = _try_parse_dt(b)
        if pb is not None:
            b = pb
    elif isinstance(b, datetime) and isinstance(a, str):
        pa = _try_parse_dt(a)
        if pa is not None:
            a = pa
    return a, b


def _lt(a: Any, b: Any) -> Optional[bool]:
    a, b = _coerce_pair(a, b)
    try:
        return a < b
    except TypeError:
        return None


def _eq(a: Any, b: Any) -> bool:
    if a is _MISSING:
        return b is None
    if isinstance(a, (datetime, date)) and isinstance(b, str):
        a, b = _coerce_pair(a, b)
    elif isinstance(b, (datetime, date)) and isinstance(a, str):
        a, b = _coerce_pair(a, b)
    return a == b


def _match_op(actual: Any, op: str, operand: Any) -> bool:
    if op in ("$eq",):
        return _eq(actual, operand)
    if op == "$ne":
        return not _eq(actual, operand)
    if op == "$in":
        return any(_eq(actual, v) for v in (operand or []))
    if op == "$nin":
        return not any(_eq(actual, v) for v in (operand or []))
    if op == "$exists":
        present = actual is not _MISSING
        return present if operand else not present
    if op == "$type":
        # 仅需支持 BSON "date" 类型判断（log_window_service 用它筛有效时间戳条目）
        if operand in ("date", "datetime", 9):
            return isinstance(actual, (datetime, date))
        if operand in ("string", 2):
            return isinstance(actual, str)
        if operand in ("int", "long", "double", "number", 1, 16, 18):
            return isinstance(actual, (int, float)) and not isinstance(actual, bool)
        if operand in ("bool", 8):
            return isinstance(actual, bool)
        return actual is not _MISSING
    if op in ("$gt", "$gte", "$lt", "$lte"):
        if actual is _MISSING:
            return False
        lt = _lt(actual, operand)
        gt = _lt(operand, actual)
        if lt is None or gt is None:
            return False
        if op == "$gt":
            return gt
        if op == "$gte":
            return gt or not lt and not gt
        if op == "$lt":
            return lt
        if op == "$lte":
            return lt or not lt and not gt
    # 未知算子：保守返回 False
    return False


def _match_value(actual: Any, cond: Any) -> bool:
    if isinstance(cond, dict) and any(k.startswith("$") for k in cond.keys()):
        return all(_match_op(actual, op, operand) for op, operand in cond.items())
    return _eq(actual, cond)


def _matches(doc: Dict[str, Any], filt: Dict[str, Any]) -> bool:
    if not filt:
        return True
    for key, cond in filt.items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in (cond or [])):
                return False
            continue
        if key == "$and":
            if not all(_matches(doc, sub) for sub in (cond or [])):
                return False
            continue
        if not _match_value(_get_path(doc, key), cond):
            return False
    return True


# ---------------------------------------------------------------------------
# projection / sort
# ---------------------------------------------------------------------------
def _project(doc: Dict[str, Any], projection: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not projection:
        return dict(doc)

    non_id = {k: v for k, v in projection.items() if k != "_id"}
    include_mode = any(bool(v) for v in non_id.values())

    if include_mode:
        out: Dict[str, Any] = {}
        for k, v in projection.items():
            if k == "_id" or not v:
                continue
            val = _get_path(doc, k)
            if val is not _MISSING:
                out[k] = val
        if projection.get("_id", 1) and "_id" in doc:
            out["_id"] = doc["_id"]
        return out

    # exclude 模式
    out = dict(doc)
    for k, v in projection.items():
        if not v:
            out.pop(k, None)
    return out


def _sort_key_factory(field: str):
    def key(doc: Dict[str, Any]):
        v = _get_path(doc, field)
        if v is _MISSING or v is None:
            return (0, 0)
        if isinstance(v, str):
            pv = _try_parse_dt(v)
            return (1, pv) if pv is not None else (1, v)
        return (1, v)
    return key


def _apply_sort(rows: List[Dict[str, Any]], sort_spec: List[Tuple[str, int]]) -> List[Dict[str, Any]]:
    out = list(rows)
    for field, direction in reversed(sort_spec):
        try:
            out.sort(key=_sort_key_factory(field), reverse=(int(direction) < 0))
        except TypeError:
            # 同字段混合不可比类型时，退化为不排序该字段
            pass
    return out


# ---------------------------------------------------------------------------
# 结果对象（贴近 pymongo 返回值）
# ---------------------------------------------------------------------------
class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id
        self.acknowledged = True


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids
        self.acknowledged = True


class _UpdateResult:
    def __init__(self, matched, modified, upserted_id=None):
        self.matched_count = matched
        self.modified_count = modified
        self.upserted_id = upserted_id
        self.acknowledged = True


class _DeleteResult:
    def __init__(self, deleted):
        self.deleted_count = deleted
        self.acknowledged = True


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------
class PgMongoCursor:
    def __init__(self, rows: List[Dict[str, Any]], projection: Optional[Dict[str, Any]]):
        self._rows = rows  # 已通过 filter 的原始 doc 列表
        self._projection = projection
        self._sort: List[Tuple[str, int]] = []
        self._skip = 0
        self._limit = 0  # 0 = 不限制（同 pymongo 语义）

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, str):
            self._sort = [(key_or_list, 1 if direction is None else int(direction))]
        else:
            self._sort = [(f, int(d)) for f, d in key_or_list]
        return self

    def skip(self, n):
        self._skip = int(n or 0)
        return self

    def limit(self, n):
        self._limit = int(n or 0)
        return self

    def _materialize(self) -> List[Dict[str, Any]]:
        rows = _apply_sort(self._rows, self._sort) if self._sort else list(self._rows)
        if self._skip:
            rows = rows[self._skip:]
        if self._limit:
            rows = rows[: self._limit]
        return [_project(d, self._projection) for d in rows]

    def __iter__(self):
        return iter(self._materialize())

    def __len__(self):
        return len(self._materialize())


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
class PgMongoCollection:
    def __init__(self, engine, name: str):
        self.engine = engine
        self.name = name

    # --- 内部：取候选行（按 collection + 可能的 run_id 预过滤，再 Python 过滤）---
    def _candidates(self, filt: Optional[Dict[str, Any]]) -> List[Tuple[int, Dict[str, Any]]]:
        run_id = None
        if filt:
            rv = filt.get("run_id")
            if isinstance(rv, str):
                run_id = rv
        sql = "SELECT pk, doc FROM mongo_docs WHERE collection = :c"
        params: Dict[str, Any] = {"c": self.name}
        if run_id is not None:
            sql += " AND run_id = :r"
            params["r"] = run_id
        with self.engine.connect() as conn:
            raw = conn.execute(text(sql), params).fetchall()
        out: List[Tuple[int, Dict[str, Any]]] = []
        for pk, doc in raw:
            if isinstance(doc, str):
                doc = json.loads(doc)
            if doc is None:
                continue
            doc = _decode(doc)
            if _matches(doc, filt or {}):
                out.append((pk, doc))
        return out

    def _write_doc(self, conn, doc: Dict[str, Any]) -> str:
        doc_id = str(doc.get("_id") if doc.get("_id") is not None else uuid.uuid4().hex)
        doc["_id"] = doc.get("_id", doc_id)
        run_id = doc.get("run_id")
        run_id = run_id if isinstance(run_id, str) else None
        conn.execute(
            text(
                """
                INSERT INTO mongo_docs (collection, doc_id, run_id, doc)
                VALUES (:c, :i, :r, CAST(:d AS jsonb))
                ON CONFLICT (collection, doc_id)
                DO UPDATE SET doc = EXCLUDED.doc, run_id = EXCLUDED.run_id
                """
            ),
            {
                "c": self.name,
                "i": doc_id,
                "r": run_id,
                "d": _dumps(doc),
            },
        )
        return doc["_id"]

    # --- 写 ---
    def insert_one(self, doc: Dict[str, Any], *args, **kwargs) -> _InsertOneResult:
        doc = dict(doc)
        with self.engine.begin() as conn:
            inserted_id = self._write_doc(conn, doc)
        return _InsertOneResult(inserted_id)

    def insert_many(self, docs, *args, **kwargs) -> _InsertManyResult:
        ids = []
        with self.engine.begin() as conn:
            for d in docs:
                ids.append(self._write_doc(conn, dict(d)))
        return _InsertManyResult(ids)

    def update_one(self, filt, update, upsert: bool = False, *args, **kwargs) -> _UpdateResult:
        matches = self._candidates(filt)
        set_fields = (update or {}).get("$set", {}) or {}
        if matches:
            pk, doc = matches[0]
            for k, v in set_fields.items():
                _set_path(doc, k, v)
            with self.engine.begin() as conn:
                conn.execute(
                    text("UPDATE mongo_docs SET doc = CAST(:d AS jsonb), run_id = :r WHERE pk = :pk"),
                    {
                        "d": _dumps(doc),
                        "r": doc.get("run_id") if isinstance(doc.get("run_id"), str) else None,
                        "pk": pk,
                    },
                )
            return _UpdateResult(1, 1)
        if upsert:
            new_doc: Dict[str, Any] = {}
            for k, v in (filt or {}).items():
                if not k.startswith("$") and not isinstance(v, dict):
                    _set_path(new_doc, k, v)
            for k, v in set_fields.items():
                _set_path(new_doc, k, v)
            with self.engine.begin() as conn:
                up_id = self._write_doc(conn, new_doc)
            return _UpdateResult(0, 0, upserted_id=up_id)
        return _UpdateResult(0, 0)

    def replace_one(self, filt, replacement, upsert: bool = False, *args, **kwargs) -> _UpdateResult:
        replacement = dict(replacement)
        matches = self._candidates(filt)
        if matches:
            pk, doc = matches[0]
            if "_id" not in replacement and "_id" in doc:
                replacement["_id"] = doc["_id"]
            with self.engine.begin() as conn:
                conn.execute(
                    text("UPDATE mongo_docs SET doc = CAST(:d AS jsonb), run_id = :r WHERE pk = :pk"),
                    {
                        "d": _dumps(replacement),
                        "r": replacement.get("run_id") if isinstance(replacement.get("run_id"), str) else None,
                        "pk": pk,
                    },
                )
            return _UpdateResult(1, 1)
        if upsert:
            with self.engine.begin() as conn:
                up_id = self._write_doc(conn, replacement)
            return _UpdateResult(0, 0, upserted_id=up_id)
        return _UpdateResult(0, 0)

    def delete_one(self, filt, *args, **kwargs) -> _DeleteResult:
        matches = self._candidates(filt)
        if not matches:
            return _DeleteResult(0)
        pk = matches[0][0]
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM mongo_docs WHERE pk = :pk"), {"pk": pk})
        return _DeleteResult(1)

    def delete_many(self, filt, *args, **kwargs) -> _DeleteResult:
        matches = self._candidates(filt)
        if not matches:
            return _DeleteResult(0)
        pks = [m[0] for m in matches]
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM mongo_docs WHERE pk = ANY(:pks)"),
                {"pks": pks},
            )
        return _DeleteResult(len(pks))

    # --- 读 ---
    def count_documents(self, filt=None, *args, **kwargs) -> int:
        return len(self._candidates(filt or {}))

    def find_one(self, filt=None, projection=None, *args, **kwargs):
        cur = self.find(filt, projection, sort=kwargs.get("sort"))
        for doc in cur.limit(1):
            return doc
        return None

    def find(self, filt=None, projection=None, *args, sort=None, **kwargs) -> PgMongoCursor:
        rows = [doc for _pk, doc in self._candidates(filt or {})]
        cur = PgMongoCursor(rows, projection)
        if sort is not None:
            cur.sort(sort)
        return cur

    # --- 索引：兼容 no-op ---
    def create_index(self, *args, **kwargs):
        return "noop_index"

    def create_indexes(self, *args, **kwargs):
        return ["noop_index"]


def _set_path(doc: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = doc
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


# ---------------------------------------------------------------------------
# Database / Client
# ---------------------------------------------------------------------------
class PgMongoDatabase:
    def __init__(self, engine):
        self._engine = engine

    def __getitem__(self, name: str) -> PgMongoCollection:
        return PgMongoCollection(self._engine, name)

    def __getattr__(self, name: str) -> PgMongoCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return PgMongoCollection(self._engine, name)

    def list_collection_names(self) -> List[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(text("SELECT DISTINCT collection FROM mongo_docs")).fetchall()
        return [r[0] for r in rows]

    def command(self, *args, **kwargs) -> Dict[str, Any]:
        """兼容 pymongo 的 db.command('ping')；底层 PG 已连，恒返回 ok。"""
        return {"ok": 1}


class PgMongoClient:
    """pymongo MongoClient 的最小替身，底层是 PostgreSQL JSONB。"""

    def __init__(self, engine):
        self._engine = engine
        ensure_schema(engine)

    def __getitem__(self, _db_name: str) -> PgMongoDatabase:
        return PgMongoDatabase(self._engine)

    def get_database(self, _db_name: str = None) -> PgMongoDatabase:
        return PgMongoDatabase(self._engine)

    def close(self):
        pass
