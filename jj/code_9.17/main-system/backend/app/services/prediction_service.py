import logging
import re
import uuid
import json
import csv
import time
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
import pymongo
import numpy as np

from app.models.prediction_record import PredictionRecord
from app.models.log_entry import LogEntry
from app.models.monitor_data import MonitorData
from app.services.llm_service import LLMService
from app.config import settings

logger = logging.getLogger(__name__)

# 风险等级排序，用于"不降级"逻辑
_SEVERITY_ORDER: Dict[str, int] = {"green": 0, "yellow": 1, "red": 2}

# 日志行级别匹配（不区分大小写）
_LEVEL_RE = re.compile(r"\b(CRITICAL|FATAL|PANIC|ERROR|WARN(?:ING)?|INFO|DEBUG)\b", re.IGNORECASE)

# 合法日志级别（用于「保留来源已带级别、不被内容正则覆盖」）
_VALID_LEVELS = {"CRITICAL", "FATAL", "PANIC", "ERROR", "WARN", "INFO", "DEBUG", "TRACE"}

# 支持的文件类型
SUPPORTED_FILE_TYPES = {".json", ".txt", ".csv"}


def _max_status(a: str, b: str) -> str:
    """返回两个 health_status 中风险更高的那个。"""
    return a if _SEVERITY_ORDER.get(a, 0) >= _SEVERITY_ORDER.get(b, 0) else b


class PredictionService:
    def __init__(self):
        try:
            self.llm = LLMService()
        except Exception as exc:
            logger.warning(
                "[PredictionService] LLM init failed, running rule-only mode: %s", exc
            )
            self.llm = None

    # =========================================================================
    # 多源状态数据接入相关方法
    # =========================================================================

    def process_log_file(self, file_content: str, filename: str, config: Dict, db: Session) -> Dict:
        """
        处理上传的日志文件，解析内容，写入数据库，生成接入报告
        
        Args:
            file_content: 文件内容
            filename: 文件名
            config: 接入配置
            db: 数据库会话
            
        Returns:
            包含处理结果和报告的字典
        """
        try:
            # 检查文件类型
            file_ext = self._get_file_extension(filename)
            if file_ext not in SUPPORTED_FILE_TYPES:
                return {
                    "success": False,
                    "message": f"不支持的文件类型，只支持: {', '.join(SUPPORTED_FILE_TYPES)}"
                }
            
            # 解析日志文件
            log_entries = self._parse_log_file(file_content, file_ext)
            if not log_entries:
                return {
                    "success": False,
                    "message": "解析日志文件失败或文件为空"
                }
            
            # 去重和过滤无效数据
            filtered_entries = self._filter_and_deduplicate(log_entries)
            if not filtered_entries:
                return {
                    "success": False,
                    "message": "过滤后无有效日志数据"
                }
            
            # 标注异常日志
            annotated_entries = self._annotate_logs(filtered_entries)
            
            # 写入数据库
            db_entries = self._write_logs_to_db(annotated_entries, filename, db)
            
            # 生成接入报告
            report = self._generate_access_report(db_entries, filtered_entries, config)

            # 存储接入记录到 MongoDB（带 report_id + 聚合文本）
            aggregated_text = self._aggregate_entries_text(annotated_entries)
            report_id = self._store_access_record(report, config, "file", aggregated_text)
            report["report_id"] = report_id
            report["run_id"] = report_id
            
            return {
                "success": True,
                "message": "日志文件处理成功",
                "report": report
            }
        except Exception as exc:
            logger.error(f"处理日志文件失败: {exc}")
            return {
                "success": False,
                "message": f"处理日志文件失败: {str(exc)}"
            }

    def get_logs_for_prediction(self, db: Session, limit: int = 1000) -> List[Dict]:
        """
        从数据库读取日志数据用于预测
        
        Args:
            db: 数据库会话
            limit: 限制返回的日志数量
            
        Returns:
            日志数据列表
        """
        try:
            # 读取最近的日志条目
            logs = db.query(LogEntry).order_by(LogEntry.created_at.desc()).limit(limit).all()
            
            # 转换为字典列表
            result = []
            for log in logs:
                # 提取关键信息
                log_data = {
                    "id": log.id,
                    "filename": log.filename,
                    "created_at": log.created_at.isoformat(),
                    "is_indexed": log.is_indexed
                }
                
                # 分析日志内容，提取异常信息
                analysis = self._analyze_log_content(log.raw_content)
                log_data.update(analysis)
                
                result.append(log_data)
            
            return result
        except Exception as exc:
            logger.error(f"读取日志数据失败: {exc}")
            return []

    # =========================================================================
    # 基础监控数据接入相关方法
    # =========================================================================

    def process_monitor_data(self, data: Dict, db: Session) -> Dict:
        """
        处理监控数据，支持接口数据和文件数据
        
        Args:
            data: 监控数据
            db: 数据库会话
            
        Returns:
            处理结果
        """
        try:
            # 检查数据类型
            if data.get('method') == 'api':
                # 处理接口数据
                return self._process_api_data(data, db)
            elif data.get('method') == 'file':
                # 处理文件数据
                return self._process_file_data(data, db)
            else:
                return {
                    "success": False,
                    "message": "不支持的接入方式"
                }
        except Exception as exc:
            logger.error(f"处理监控数据失败: {exc}")
            return {
                "success": False,
                "message": f"处理监控数据失败: {str(exc)}"
            }

    def _process_api_data(self, data: Dict, db: Session) -> Dict:
        """
        处理接口数据
        """
        try:
            api_url = data.get('apiUrl')
            if not api_url:
                return {
                    "success": False,
                    "message": "监控接口地址不能为空"
                }
            
            # 从接口获取数据，带重试机制
            api_data = self._fetch_api_data(api_url)
            if not api_data:
                return {
                    "success": False,
                    "message": "无法从接口获取数据"
                }
            
            # 处理指标缺失
            processed_data = self._handle_missing_metrics(api_data)
            
            # 写入数据库，带缓存机制
            count = self._write_monitor_data(processed_data, "api", db)
            
            return {
                "success": True,
                "message": f"成功接入 {count} 条监控数据",
                "count": count
            }
        except Exception as exc:
            logger.error(f"处理接口数据失败: {exc}")
            return {
                "success": False,
                "message": f"处理接口数据失败: {str(exc)}"
            }

    def _process_file_data(self, data: Dict, db: Session) -> Dict:
        """
        处理文件数据
        """
        try:
            file_content = data.get('file_content')
            filename = data.get('filename')
            if not file_content:
                return {
                    "success": False,
                    "message": "文件内容不能为空"
                }
            
            # 解析文件数据
            monitor_data = self._parse_monitor_file(file_content, filename)
            if not monitor_data:
                return {
                    "success": False,
                    "message": "解析监控数据文件失败"
                }
            
            # 处理指标缺失
            processed_data = self._handle_missing_metrics(monitor_data)
            
            # 写入数据库，带缓存机制
            count = self._write_monitor_data(processed_data, "file", db)
            
            return {
                "success": True,
                "message": f"成功接入 {count} 条监控数据",
                "count": count
            }
        except Exception as exc:
            logger.error(f"处理文件数据失败: {exc}")
            return {
                "success": False,
                "message": f"处理文件数据失败: {str(exc)}"
            }

    def _fetch_api_data(self, api_url: str, max_retries: int = 3) -> List[Dict]:
        """
        从接口获取数据，带重试机制
        
        Args:
            api_url: 接口地址
            max_retries: 最大重试次数
            
        Returns:
            监控数据列表
        """
        import requests
        
        for attempt in range(max_retries):
            try:
                response = requests.get(api_url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return [data]
                else:
                    return []
            except Exception as exc:
                logger.warning(f"获取接口数据失败 (尝试 {attempt + 1}/{max_retries}): {exc}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # 指数退避
                else:
                    logger.error(f"接口超时，已重试 {max_retries} 次，仍失败")
                    # 这里可以添加告警逻辑
        
        return []

    def _parse_monitor_file(self, content: str, filename: str) -> List[Dict]:
        """
        解析监控数据文件
        """
        try:
            file_ext = os.path.splitext(filename)[1].lower()
            
            if file_ext == ".json":
                data = json.loads(content)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return [data]
                else:
                    return []
            elif file_ext == ".csv":
                import io
                reader = csv.DictReader(io.StringIO(content))
                return [dict(row) for row in reader]
            elif file_ext == ".txt":
                # 解析 txt 格式的监控数据
                # 假设每行是一个 JSON 对象或键值对
                lines = content.strip().split('\n')
                data = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        # 尝试解析为 JSON 对象
                        item = json.loads(line)
                        if isinstance(item, dict):
                            data.append(item)
                    except json.JSONDecodeError:
                        # 尝试解析为键值对格式
                        try:
                            item = {}
                            pairs = line.split(',')
                            for pair in pairs:
                                if '=' in pair:
                                    key, value = pair.split('=', 1)
                                    item[key.strip()] = value.strip()
                            if item:
                                data.append(item)
                        except Exception:
                            pass
                return data
            else:
                return []
        except Exception as exc:
            logger.error(f"解析监控数据文件失败: {exc}")
            return []

    def _handle_missing_metrics(self, data: List[Dict]) -> List[Dict]:
        """
        处理指标缺失，将缺失的指标标记为 NULL
        """
        processed_data = []
        
        for item in data:
            processed_item = item.copy()
            
            # 检查关键指标，缺失的标记为 None
            metrics = ['cpu_usage', 'memory_usage', 'disk_usage', 'temperature', 'network_in', 'network_out']
            for metric in metrics:
                if metric not in processed_item or processed_item[metric] in ['', 'null', 'None', None]:
                    processed_item[metric] = None
            
            processed_data.append(processed_item)
        
        return processed_data

    def _write_monitor_data(self, data: List[Dict], source: str, db: Session) -> int:
        """
        写入监控数据到数据库，带缓存机制
        
        Args:
            data: 监控数据列表
            source: 数据来源
            db: 数据库会话
            
        Returns:
            写入成功的记录数
        """
        count = 0
        failed_data = []
        
        for item in data:
            try:
                # 创建 MonitorData 对象
                monitor_entry = MonitorData(
                    device_id=item.get('device_id', 'unknown'),
                    timestamp=item.get('timestamp') or datetime.utcnow(),
                    cpu_usage=item.get('cpu_usage'),
                    memory_usage=item.get('memory_usage'),
                    disk_usage=item.get('disk_usage'),
                    temperature=item.get('temperature'),
                    network_in=item.get('network_in'),
                    network_out=item.get('network_out'),
                    other_metrics=item.get('other_metrics'),
                    source=source,
                    status=item.get('status', 'normal')
                )
                
                db.add(monitor_entry)
                count += 1
            except Exception as exc:
                logger.error(f"写入监控数据失败: {exc}")
                failed_data.append(item)
        
        try:
            db.commit()
        except Exception as exc:
            logger.error(f"数据库提交失败: {exc}")
            db.rollback()
            # 缓存失败的数据到本地
            if failed_data:
                self._cache_to_local(failed_data, "monitor_data")
            return 0
        
        # 重试之前失败的写入
        if failed_data:
            self._retry_failed_writes(db)
        
        return count

    def _cache_to_local(self, data: List[Dict], data_type: str):
        """
        缓存数据到本地
        """
        try:
            cache_dir = os.path.join(os.path.dirname(__file__), "..", "..", "cache")
            os.makedirs(cache_dir, exist_ok=True)
            
            timestamp = int(time.time())
            cache_file = os.path.join(cache_dir, f"{data_type}_{timestamp}.json")
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"已缓存 {len(data)} 条数据到本地: {cache_file}")
        except Exception as exc:
            logger.error(f"缓存数据到本地失败: {exc}")

    def _retry_failed_writes(self, db: Session):
        """
        重试失败的写入
        """
        try:
            cache_dir = os.path.join(os.path.dirname(__file__), "..", "..", "cache")
            if not os.path.exists(cache_dir):
                return
            
            for filename in os.listdir(cache_dir):
                if filename.startswith("monitor_data_") and filename.endswith(".json"):
                    cache_file = os.path.join(cache_dir, filename)
                    
                    try:
                        with open(cache_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        
                        # 尝试写入数据库
                        success_count = 0
                        for item in data:
                            try:
                                monitor_entry = MonitorData(
                                    device_id=item.get('device_id', 'unknown'),
                                    timestamp=item.get('timestamp') or datetime.utcnow(),
                                    cpu_usage=item.get('cpu_usage'),
                                    memory_usage=item.get('memory_usage'),
                                    disk_usage=item.get('disk_usage'),
                                    temperature=item.get('temperature'),
                                    network_in=item.get('network_in'),
                                    network_out=item.get('network_out'),
                                    other_metrics=item.get('other_metrics'),
                                    source=item.get('source', 'cache'),
                                    status=item.get('status', 'normal')
                                )
                                db.add(monitor_entry)
                                success_count += 1
                            except Exception as exc:
                                logger.error(f"重试写入失败: {exc}")
                        
                        if success_count > 0:
                            db.commit()
                            os.remove(cache_file)
                            logger.info(f"成功重试写入 {success_count} 条数据")
                    except Exception as exc:
                        logger.error(f"处理缓存文件失败: {exc}")
        except Exception as exc:
            logger.error(f"重试失败的写入失败: {exc}")

    # =========================================================================
    # 数据标准化相关方法
    # =========================================================================

    def do_standardization(self, config: Dict, db: Session) -> Dict:
        """
        数据标准化，包括时间对齐、单位统一、缺失值填充、异常值剔除
        
        Args:
            config: 标准化配置
            db: 数据库会话
            
        Returns:
            标准化结果
        """
        try:
            # 读取日志数据和监控数据
            log_data = self._get_log_data(db)
            monitor_data = self._get_monitor_data(db)
            
            if not log_data and not monitor_data:
                return {
                    "success": False,
                    "message": "无数据可标准化",
                    "report": {}
                }
            
            # 时间对齐
            aligned_data = self._align_time(log_data, monitor_data, config.get('timeAlign', 'minute'))
            
            # 单位统一
            unified_data = self._unify_units(aligned_data)
            
            # 缺失值填充
            filled_data = self._fill_missing_values(unified_data, config.get('missingFill', 'mean'))
            
            # 异常值剔除
            cleaned_data = self._remove_outliers(filled_data)
            
            # 日志文本向量化预处理
            vectorized_data = self._vectorize_logs(cleaned_data)
            
            # 归一化处理
            normalized_data = self._normalize_data(vectorized_data, config.get('normalization', 'minmax'))
            
            # 准备存储到 MongoDB 的向量数据
            vectors_to_store = []
            for item in normalized_data:
                vector_item = {
                    'timestamp': item['timestamp'],
                    'logs': item['logs'],
                    'monitor': item['monitor'],
                    'created_at': datetime.utcnow()
                }
                vectors_to_store.append(vector_item)
            
            # 存储向量到 MongoDB
            stored = self._store_vectors_to_mongo(vectors_to_store)
            
            # 向量相似度比较
            similar_vectors_info = []
            if vectors_to_store:
                # 取第一个向量进行相似度比较
                sample_vector = vectors_to_store[0]
                similar_vectors = self._compare_vectors(sample_vector)
                similar_vectors_info = [
                    {
                        'similarity': v['similarity'],
                        'timestamp': v['timestamp'].isoformat() if hasattr(v['timestamp'], 'isoformat') else str(v['timestamp'])
                    }
                    for v in similar_vectors[:5]  # 只返回前 5 个最相似的
                ]
            
            # 生成标准化报告
            report = self._generate_standardization_report(
                log_count=len(log_data),
                monitor_count=len(monitor_data),
                aligned_count=len(aligned_data),
                cleaned_count=len(cleaned_data)
            )
            
            # 添加 MongoDB 存储和相似度比较信息
            report['mongo_stored'] = stored
            report['similar_vectors_count'] = len(similar_vectors_info)
            report['similar_vectors'] = similar_vectors_info
            
            return {
                "success": True,
                "message": "数据标准化完成，向量已存储到 MongoDB",
                "report": report
            }
        except Exception as exc:
            logger.error(f"数据标准化失败: {exc}")
            return {
                "success": False,
                "message": f"数据标准化失败: {str(exc)}",
                "report": {}
            }

    def _get_log_data(self, db: Session) -> List[Dict]:
        """
        从数据库读取日志数据
        """
        try:
            logs = db.query(LogEntry).order_by(LogEntry.created_at.desc()).limit(1000).all()
            return [{
                'id': log.id,
                'timestamp': log.created_at,
                'content': log.raw_content,
                'is_indexed': log.is_indexed,
                'type': 'log'
            } for log in logs]
        except Exception as exc:
            logger.error(f"读取日志数据失败: {exc}")
            return []

    def _get_monitor_data(self, db: Session) -> List[Dict]:
        """
        从数据库读取监控数据
        """
        try:
            monitor_data = db.query(MonitorData).order_by(MonitorData.timestamp.desc()).limit(1000).all()
            return [{
                'id': data.id,
                'timestamp': data.timestamp,
                'device_id': data.device_id,
                'cpu_usage': data.cpu_usage,
                'memory_usage': data.memory_usage,
                'disk_usage': data.disk_usage,
                'temperature': data.temperature,
                'network_in': data.network_in,
                'network_out': data.network_out,
                'other_metrics': data.other_metrics,
                'source': data.source,
                'status': data.status,
                'type': 'monitor'
            } for data in monitor_data]
        except Exception as exc:
            logger.error(f"读取监控数据失败: {exc}")
            return []

    def _align_time(self, log_data: List[Dict], monitor_data: List[Dict], align_unit: str = 'minute') -> List[Dict]:
        """
        时间对齐
        """
        # 合并数据
        all_data = log_data + monitor_data
        
        # 按时间排序
        all_data.sort(key=lambda x: x['timestamp'])
        
        # 时间对齐处理
        aligned_data = []
        time_buckets = {}
        
        for item in all_data:
            # 根据对齐单位生成时间桶键
            timestamp = item['timestamp']
            if align_unit == 'minute':
                bucket_key = timestamp.strftime('%Y-%m-%d %H:%M')
            elif align_unit == 'hour':
                bucket_key = timestamp.strftime('%Y-%m-%d %H:00')
            elif align_unit == 'day':
                bucket_key = timestamp.strftime('%Y-%m-%d')
            else:
                bucket_key = timestamp.strftime('%Y-%m-%d %H:%M')
            
            if bucket_key not in time_buckets:
                time_buckets[bucket_key] = {
                    'timestamp': timestamp,
                    'logs': [],
                    'monitor': []
                }
            
            if item['type'] == 'log':
                time_buckets[bucket_key]['logs'].append(item)
            else:
                time_buckets[bucket_key]['monitor'].append(item)
        
        # 转换为对齐后的数据格式
        for bucket in time_buckets.values():
            aligned_item = {
                'timestamp': bucket['timestamp'],
                'logs': bucket['logs'],
                'monitor': bucket['monitor']
            }
            aligned_data.append(aligned_item)
        
        return aligned_data

    def _unify_units(self, data: List[Dict]) -> List[Dict]:
        """
        单位统一
        """
        for item in data:
            # 统一监控数据单位
            for monitor in item['monitor']:
                # 确保百分比数据在 0-100 之间
                for metric in ['cpu_usage', 'memory_usage', 'disk_usage']:
                    if monitor.get(metric) is not None:
                        value = monitor[metric]
                        # 如果值大于 1，假设是百分比
                        if value > 1:
                            monitor[metric] = min(value, 100)
                        # 如果值小于等于 1，假设是小数，转换为百分比
                        else:
                            monitor[metric] = value * 100
            
        return data

    def _fill_missing_values(self, data: List[Dict], fill_method: str = 'mean') -> List[Dict]:
        """
        缺失值填充
        """
        # 计算各指标的均值
        metrics = ['cpu_usage', 'memory_usage', 'disk_usage', 'temperature', 'network_in', 'network_out']
        metric_values = {metric: [] for metric in metrics}
        
        # 收集所有非空值
        for item in data:
            for monitor in item['monitor']:
                for metric in metrics:
                    if monitor.get(metric) is not None:
                        metric_values[metric].append(monitor[metric])
        
        # 计算均值
        metric_means = {}
        for metric, values in metric_values.items():
            if values:
                metric_means[metric] = sum(values) / len(values)
            else:
                metric_means[metric] = 0
        
        # 填充缺失值
        for item in data:
            for monitor in item['monitor']:
                for metric in metrics:
                    if monitor.get(metric) is None:
                        if fill_method == 'mean':
                            monitor[metric] = metric_means[metric]
                        elif fill_method == 'zero':
                            monitor[metric] = 0
                        elif fill_method == 'median':
                            if metric_values[metric]:
                                sorted_values = sorted(metric_values[metric])
                                mid = len(sorted_values) // 2
                                monitor[metric] = sorted_values[mid] if len(sorted_values) % 2 == 1 else (sorted_values[mid-1] + sorted_values[mid]) / 2
                            else:
                                monitor[metric] = 0
        
        return data

    def _remove_outliers(self, data: List[Dict]) -> List[Dict]:
        """
        异常值剔除
        """
        # 使用 IQR 方法检测异常值
        metrics = ['cpu_usage', 'memory_usage', 'disk_usage', 'temperature', 'network_in', 'network_out']
        metric_values = {metric: [] for metric in metrics}
        
        # 收集所有非空值
        for item in data:
            for monitor in item['monitor']:
                for metric in metrics:
                    if monitor.get(metric) is not None:
                        metric_values[metric].append(monitor[metric])
        
        # 计算每个指标的 IQR
        metric_iqr = {}
        for metric, values in metric_values.items():
            if len(values) >= 4:
                sorted_values = sorted(values)
                q1 = sorted_values[int(len(values) * 0.25)]
                q3 = sorted_values[int(len(values) * 0.75)]
                iqr = q3 - q1
                metric_iqr[metric] = (q1 - 1.5 * iqr, q3 + 1.5 * iqr)
            else:
                metric_iqr[metric] = (0, float('inf'))
        
        # 剔除异常值
        cleaned_data = []
        for item in data:
            cleaned_item = {
                'timestamp': item['timestamp'],
                'logs': item['logs'],
                'monitor': []
            }
            
            for monitor in item['monitor']:
                cleaned_monitor = monitor.copy()
                for metric in metrics:
                    if cleaned_monitor.get(metric) is not None:
                        min_val, max_val = metric_iqr[metric]
                        if cleaned_monitor[metric] < min_val or cleaned_monitor[metric] > max_val:
                            cleaned_monitor[metric] = None  # 标记为异常值
                cleaned_item['monitor'].append(cleaned_monitor)
            
            cleaned_data.append(cleaned_item)
        
        return cleaned_data

    def _vectorize_logs(self, data: List[Dict]) -> List[Dict]:
        """
        日志文本向量化预处理
        """
        for item in data:
            for log in item['logs']:
                # 简单的文本向量化处理
                # 实际项目中可以使用更复杂的NLP模型
                content = log.get('content', '')
                # 计算文本长度、单词数等特征
                log['vector_features'] = {
                    'length': len(content),
                    'word_count': len(content.split()),
                    'has_error': 1 if 'error' in content.lower() else 0,
                    'has_warning': 1 if 'warning' in content.lower() else 0,
                    'has_exception': 1 if 'exception' in content.lower() else 0
                }
        
        return data

    def _normalize_data(self, data: List[Dict], method: str = 'minmax') -> List[Dict]:
        """
        归一化处理
        """
        metrics = ['cpu_usage', 'memory_usage', 'disk_usage', 'temperature', 'network_in', 'network_out']
        metric_values = {metric: [] for metric in metrics}
        
        # 收集所有非空值
        for item in data:
            for monitor in item['monitor']:
                for metric in metrics:
                    if monitor.get(metric) is not None:
                        metric_values[metric].append(monitor[metric])
        
        # 计算归一化参数
        metric_params = {}
        for metric, values in metric_values.items():
            if values:
                if method == 'minmax':
                    min_val = min(values)
                    max_val = max(values)
                    if max_val > min_val:
                        metric_params[metric] = (min_val, max_val)
                    else:
                        metric_params[metric] = (0, 100)
                elif method == 'zscore':
                    mean = sum(values) / len(values)
                    std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
                    if std > 0:
                        metric_params[metric] = (mean, std)
                    else:
                        metric_params[metric] = (mean, 1)
        
        # 归一化处理
        for item in data:
            for monitor in item['monitor']:
                for metric in metrics:
                    if monitor.get(metric) is not None and metric in metric_params:
                        if method == 'minmax':
                            min_val, max_val = metric_params[metric]
                            monitor[f'{metric}_normalized'] = (monitor[metric] - min_val) / (max_val - min_val)
                        elif method == 'zscore':
                            mean, std = metric_params[metric]
                            monitor[f'{metric}_normalized'] = (monitor[metric] - mean) / std
        
        return data

    def _generate_standardization_report(self, log_count: int, monitor_count: int, aligned_count: int, cleaned_count: int) -> Dict:
        """
        生成标准化报告
        """
        return {
            'original_log_count': log_count,
            'original_monitor_count': monitor_count,
            'aligned_count': aligned_count,
            'cleaned_count': cleaned_count,
            'completion_rate': (cleaned_count / aligned_count * 100) if aligned_count > 0 else 0,
            'timestamp': datetime.utcnow().isoformat()
        }

    def _get_mongo_client(self):
        """
        获取文档存储客户端连接。

        已由 MongoDB 迁移为 PostgreSQL JSONB 兼容层（app/mongo_compat.py），
        这里返回共享的 PG 支撑 client（接口同 pymongo：client[db][coll].find/insert...）。
        """
        try:
            from app.database import get_mongo_client
            return get_mongo_client()
        except Exception as exc:
            logger.error(f"连接文档存储（PostgreSQL JSONB）失败: {exc}")
            return None

    def _store_vectors_to_mongo(self, vectors: List[Dict]):
        """
        将数据向量存储到 MongoDB
        """
        client = self._get_mongo_client()
        if not client:
            return False
        
        try:
            db = client[settings.MONGO_DB]
            collection = db['standardized_vectors']
            
            # 批量插入向量数据
            if vectors:
                collection.insert_many(vectors)
                logger.info(f"成功存储 {len(vectors)} 个向量到 MongoDB")
            
            return True
        except Exception as exc:
            logger.error(f"存储向量到 MongoDB 失败: {exc}")
            return False
        finally:
            if client:
                client.close()

    def _compare_vectors(self, new_vector: Dict, threshold: float = 0.8) -> List[Dict]:
        """
        对比新向量与 MongoDB 中已存储的向量，返回相似度高于阈值的向量
        """
        client = self._get_mongo_client()
        if not client:
            return []
        
        try:
            db = client[settings.MONGO_DB]
            collection = db['standardized_vectors']
            
            # 提取新向量的特征值
            new_features = self._extract_vector_features(new_vector)
            if not new_features:
                return []
            
            # 查询所有向量并计算相似度
            similar_vectors = []
            for vector in collection.find():
                # 提取存储向量的特征值
                stored_features = self._extract_vector_features(vector)
                if stored_features:
                    # 计算余弦相似度
                    similarity = self._cosine_similarity(new_features, stored_features)
                    if similarity >= threshold:
                        vector['similarity'] = similarity
                        similar_vectors.append(vector)
            
            # 按相似度排序
            similar_vectors.sort(key=lambda x: x['similarity'], reverse=True)
            
            return similar_vectors
        except Exception as exc:
            logger.error(f"对比向量失败: {exc}")
            return []
        finally:
            if client:
                client.close()

    def _extract_vector_features(self, vector: Dict) -> Optional[np.ndarray]:
        """
        提取向量的特征值，转换为 numpy 数组
        """
        try:
            # 提取监控数据的归一化值
            features = []
            metrics = ['cpu_usage', 'memory_usage', 'disk_usage', 'temperature', 'network_in', 'network_out']
            
            # 处理监控数据
            if 'monitor' in vector:
                for monitor in vector['monitor']:
                    for metric in metrics:
                        normalized_key = f'{metric}_normalized'
                        if normalized_key in monitor:
                            features.append(monitor[normalized_key])
            
            # 处理日志特征
            if 'logs' in vector:
                for log in vector['logs']:
                    if 'vector_features' in log:
                        log_features = log['vector_features']
                        features.extend([
                            log_features.get('length', 0),
                            log_features.get('word_count', 0),
                            log_features.get('has_error', 0),
                            log_features.get('has_warning', 0),
                            log_features.get('has_exception', 0)
                        ])
            
            if features:
                return np.array(features)
            else:
                return None
        except Exception as exc:
            logger.error(f"提取向量特征失败: {exc}")
            return None

    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """
        计算两个向量的余弦相似度
        """
        try:
            # 确保向量长度相同
            if len(vec1) != len(vec2):
                # 填充较短的向量
                max_len = max(len(vec1), len(vec2))
                vec1 = np.pad(vec1, (0, max_len - len(vec1)), 'constant')
                vec2 = np.pad(vec2, (0, max_len - len(vec2)), 'constant')
            
            # 计算余弦相似度
            dot_product = np.dot(vec1, vec2)
            norm_vec1 = np.linalg.norm(vec1)
            norm_vec2 = np.linalg.norm(vec2)
            
            if norm_vec1 > 0 and norm_vec2 > 0:
                return dot_product / (norm_vec1 * norm_vec2)
            else:
                return 0.0
        except Exception as exc:
            logger.error(f"计算余弦相似度失败: {exc}")
            return 0.0

    # =========================================================================
    # 公开方法（接口不变）
    # =========================================================================

    def predict(
        self,
        log_text: str,
        metrics: dict,
        db: Session,
        run_id: str = None,
        floor_status: str = None,
        floor_summary: str = None,
    ) -> dict:
        # Step 1: 获取结构化行列表
        lines = self._get_lines(log_text)

        # Step 2: 可选写入 MongoDB（任何失败不阻断主流程）
        self._try_ingest(log_text)

        # Step 3: 规则打分（始终返回完整结构，不依赖 LLM）
        rule_result = self._rule_score(metrics, lines)

        # Step 4: LLM 润色（失败时回退到规则结果）
        llm_result = self._llm_polish(log_text, metrics, rule_result)
        final = llm_result if llm_result is not None else rule_result

        # Step 4.5: 报告级统计下限（grade_report 传入）——报告自带的 abnormal_logs 计数
        # 是接入时算定的权威值，防止 LLM/文本采样漏看散落异常而把有错误的报告判「一般」。
        if floor_status in _SEVERITY_ORDER and _SEVERITY_ORDER[floor_status] > _SEVERITY_ORDER.get(
            final.get("health_status"), 0
        ):
            final["health_status"] = floor_status
            # 被统计下限抬升时，优先用规则摘要（含具体风险项）；规则也无项才用统计摘要，
            # 避免最终等级是 严重/紧急 却仍显示 LLM 的「系统运行正常，无异常日志」。
            if rule_result.get("risk_details"):
                final["risk_summary"] = rule_result["risk_summary"]
                final["risk_details"] = rule_result["risk_details"]
            elif floor_summary:
                final["risk_summary"] = floor_summary

        # Step 5: 规范化 risk_details（固定 type/level/detail 结构）
        final["risk_details"] = self._normalize_risk_details(final.get("risk_details"))

        # Step 6: 持久化（原有结构保留，仅将 llm_result 替换为 final）
        record = PredictionRecord(
            input_log=log_text[:50000],
            run_id=run_id,
            cpu_usage=metrics.get("cpu_usage"),        # None 就是 NULL，不填默认值
            memory_usage=metrics.get("memory_usage"),
            disk_usage=metrics.get("disk_usage"),
            temperature=metrics.get("temperature"),
            health_status=final["health_status"],
            risk_summary=final["risk_summary"],
            risk_details=final["risk_details"],
        )
        if run_id:
            db.query(PredictionRecord).filter(PredictionRecord.run_id == run_id).delete(synchronize_session=False)
        db.add(record)
        db.commit()
        db.refresh(record)

        return {
            "id": record.id,
            "run_id": record.run_id,
            "health_status": record.health_status,
            "risk_summary": record.risk_summary,
            "risk_details": record.risk_details,
            "cpu_usage": record.cpu_usage,
            "memory_usage": record.memory_usage,
            "disk_usage": record.disk_usage,
            "temperature": record.temperature,
            "created_at": record.created_at,
        }

    def assess(self, log_text: str, metrics: dict = None) -> dict:
        """对日志做软件状态分级但【不落库】，供故障诊断前置门控复用。

        大模型可用时由大模型判定 health_status（green/yellow/red）；不可用时回退规则评分。
        与 predict() 共用打分/润色逻辑，但不写 PredictionRecord、不写 MongoDB。
        """
        metrics = metrics or {}
        lines = self._get_lines(log_text)
        rule_result = self._rule_score(metrics, lines)
        llm_result = self._llm_polish(log_text, metrics, rule_result)
        final = llm_result if llm_result is not None else rule_result
        final["risk_details"] = self._normalize_risk_details(final.get("risk_details"))
        return final

    def grade_run(
        self,
        run_id: str,
        db: Session,
        floor_status: str = None,
        floor_summary: str = None,
    ) -> dict:
        """按已接入日志(run_id)做大模型软件状态分级。

        从 MongoDB log_windows 取该 run 的窗口文本拼接后交给 predict() 评级；
        窗口缺失时退回 log_entries。LLM 不可用时 predict() 自带规则兜底，仍返回
        green/yellow/red，保证优雅降级。floor_status/floor_summary 由 grade_report
        透传（报告级异常统计下限）。
        """
        from app.database import get_mongo_db
        mongo_db = get_mongo_db()
        windows = list(
            mongo_db["log_windows"].find(
                {"run_id": run_id, "stats.n_entries": {"$gt": 0}},
                {"text": 1, "_id": 0},
            )
        )
        text = "\n".join(w.get("text", "") for w in windows if w.get("text"))
        if not text.strip():
            entries = list(
                mongo_db["log_entries"].find(
                    {"run_id": run_id},
                    {"message": 1, "raw_line": 1, "_id": 0},
                )
            )
            text = "\n".join(
                (e.get("message") or e.get("raw_line") or "") for e in entries
            )
        return self.predict(
            text, {}, db, run_id=run_id,
            floor_status=floor_status, floor_summary=floor_summary,
        )

    # =========================================================================
    # 私有辅助方法
    # =========================================================================

    def _get_lines(self, log_text: str) -> List[str]:
        """用 preprocessing_service 清洗并切行；失败时降级为 splitlines()。"""
        try:
            from app.services.preprocessing_service import preprocessing_service
            return preprocessing_service.preprocess(log_text).lines
        except Exception as exc:
            logger.warning("[PredictionService] preprocessing fallback to splitlines: %s", exc)
            return log_text.splitlines()

    def _try_ingest(self, log_text: str) -> None:
        """
        将日志写入 MongoDB log_entries（可选增强步骤）。
        MongoDB 不可用、依赖缺失、任何异常均静默跳过，不影响主流程。
        """
        try:
            from app.services.preprocessing_service import preprocessing_service
            from app.services.log_ingest_service import log_ingest_service, RunMeta
            from app.database import get_mongo_db

            preprocessed = preprocessing_service.preprocess(log_text)
            run_meta = RunMeta(run_id=str(uuid.uuid4()), source_type="upload")
            mongo_db = get_mongo_db()
            log_ingest_service.ingest(preprocessed, run_meta, mongo_db)
        except Exception as exc:
            logger.warning("[PredictionService] ingest skipped: %s", exc)

    def _rule_score(self, metrics: dict, lines: List[str]) -> dict:
        """
        纯规则打分。始终返回完整结构，不返回 None，不缺字段。

        返回：{"health_status": str, "risk_summary": str, "risk_details": list}
        """
        # ── 统计日志级别 ──────────────────────────────────────────────────────
        counts: Dict[str, int] = {}
        for line in lines:
            for m in _LEVEL_RE.finditer(line):
                lvl = m.group(1).upper()
                if lvl == "WARNING":
                    lvl = "WARN"
                counts[lvl] = counts.get(lvl, 0) + 1

        details: List[dict] = []
        status_flags: List[str] = []

        # ── CRITICAL / FATAL / PANIC → 直接 red ──────────────────────────────
        critical_n = (
            counts.get("CRITICAL", 0)
            + counts.get("FATAL", 0)
            + counts.get("PANIC", 0)
        )
        if critical_n > 0:
            details.append({
                "type": "日志异常",
                "level": "high",
                "detail": f"发现 {critical_n} 条 CRITICAL/FATAL/PANIC 级别日志",
            })
            status_flags.append("red")

        # ── ERROR 计数 ────────────────────────────────────────────────────────
        # 语义：报告里只要出现真实 ERROR 就不该判「一般」。>10 条视为紧急(red)，
        # 1~10 条视为严重(yellow)。这样「选了故障/异常标签、报告含错误」不再误判一般。
        error_n = counts.get("ERROR", 0)
        if error_n > 10:
            details.append({
                "type": "日志异常",
                "level": "high",
                "detail": f"ERROR 日志 {error_n} 条（阈值 10）",
            })
            status_flags.append("red")
        elif error_n >= 1:
            details.append({
                "type": "日志异常",
                "level": "medium",
                "detail": f"ERROR 日志 {error_n} 条",
            })
            status_flags.append("yellow")

        # ── WARN 计数 ─────────────────────────────────────────────────────────
        warn_n = counts.get("WARN", 0)
        if warn_n > 20:
            details.append({
                "type": "日志警告",
                "level": "medium",
                "detail": f"WARN 日志 {warn_n} 条（阈值 20）",
            })
            status_flags.append("yellow")

        # ── 指标阈值（None 时完全跳过，不参与计算） ───────────────────────────
        for key, label, unit in [
            ("cpu_usage", "CPU 使用率", "%"),
            ("memory_usage", "内存使用率", "%"),
            ("disk_usage", "磁盘使用率", "%"),
        ]:
            val = metrics.get(key)
            if val is None:
                continue
            if val > 90:
                details.append({
                    "type": "指标异常",
                    "level": "high",
                    "detail": f"{label} {val:.1f}{unit}（阈值 90）",
                })
                status_flags.append("red")
            elif val > 75:
                details.append({
                    "type": "指标异常",
                    "level": "medium",
                    "detail": f"{label} {val:.1f}{unit}（阈值 75）",
                })
                status_flags.append("yellow")

        temp = metrics.get("temperature")
        if temp is not None:
            if temp > 80:
                details.append({
                    "type": "温度异常",
                    "level": "high",
                    "detail": f"系统温度 {temp:.1f}°C（阈值 80°C）",
                })
                status_flags.append("red")
            elif temp > 60:
                details.append({
                    "type": "温度异常",
                    "level": "medium",
                    "detail": f"系统温度 {temp:.1f}°C（阈值 60°C）",
                })
                status_flags.append("yellow")

        # ── 综合等级（最高优先） ──────────────────────────────────────────────
        if "red" in status_flags:
            health_status = "red"
        elif "yellow" in status_flags:
            health_status = "yellow"
        else:
            health_status = "green"

        # ── 生成摘要（预警口径：面向潜在风险，非"当前故障"）───────────────────
        if details:
            snippets = "；".join(d["detail"] for d in details[:3])
            suffix = f"等共 {len(details)} 项" if len(details) > 3 else ""
            risk_summary = f"预警：发现 {len(details)} 个需关注的风险项：{snippets}{suffix}"
        else:
            risk_summary = "运行平稳，暂无需预警的风险信号"

        return {
            "health_status": health_status,
            "risk_summary": risk_summary,
            "risk_details": details,
        }

    def _build_llm_input(self, log_text: str, max_chars: int = 6000) -> str:
        """构造交给大模型分级的输入：异常行优先 + 级别计数摘要。

        原实现直接 `log_text[:3000]` 取头部，当异常日志散落在大量正常日志之后时，
        大模型只看到头部正常日志 → 误判 green、摘要"无异常日志"。此处改为：
          1) 统计各级别条数，作为显式信号置顶（大模型不会再漏看异常）；
          2) 文本采样【异常行(CRITICAL/FATAL/PANIC/ERROR/WARN)优先】，再补普通行凑预算。
        """
        lines = log_text.splitlines()
        counts: Dict[str, int] = {}
        abnormal_lines: List[str] = []
        normal_lines: List[str] = []
        for ln in lines:
            m = _LEVEL_RE.search(ln)
            lvl = m.group(1).upper() if m else ""
            if lvl == "WARNING":
                lvl = "WARN"
            if lvl:
                counts[lvl] = counts.get(lvl, 0) + 1
            if lvl in ("CRITICAL", "FATAL", "PANIC", "ERROR", "WARN"):
                abnormal_lines.append(ln)
            elif ln.strip():
                normal_lines.append(ln)

        summary = "、".join(f"{k}:{v}" for k, v in counts.items()) or "无显式级别标记"
        picked: List[str] = []
        total = 0
        for ln in abnormal_lines + normal_lines:
            if total + len(ln) + 1 > max_chars:
                break
            picked.append(ln)
            total += len(ln) + 1
        body = "\n".join(picked)
        return f"【日志级别统计】{summary}\n【日志样本（异常行优先）】\n{body}"

    def _llm_polish(
        self, log_text: str, metrics: dict, rule_result: dict
    ) -> Optional[dict]:
        """
        用 LLM 进行健康状态分级。
        - 输入经 _build_llm_input 处理：异常行优先 + 级别计数摘要（避免漏看散落异常）
        - 规则作为【下限】：LLM 可把等级往上抬，但不能把规则已检出的异常压回更低，
          修复「LLM 只看部分样本误判 green → 覆盖规则 → 有错误的报告被判一般」
        - 任何异常返回 None，调用方回退到 rule_result（优雅降级）
        """
        if self.llm is None:
            return None
        try:
            llm_raw = self.llm.predict(self._build_llm_input(log_text), metrics)
            if not isinstance(llm_raw, dict):
                return None

            rule_status = rule_result["health_status"]
            llm_status = llm_raw.get("health_status")
            llm_status = llm_status if llm_status in _SEVERITY_ORDER else rule_status
            # 规则下限（不降级）：取 LLM 与规则中更高的等级
            final_status = _max_status(llm_status, rule_status)

            # 若被规则抬升（LLM 漏看异常），其摘要可能写"正常"——改用规则摘要更准确
            if final_status != llm_status:
                return {
                    "health_status": final_status,
                    "risk_summary": rule_result["risk_summary"],
                    "risk_details": rule_result["risk_details"],
                }
            return {
                "health_status": final_status,
                "risk_summary": llm_raw.get("risk_summary") or rule_result["risk_summary"],
                "risk_details": llm_raw.get("risk_details") or rule_result["risk_details"],
            }
        except Exception as exc:
            logger.warning("[PredictionService] LLM polish failed: %s", exc)
            return None

    @staticmethod
    def _normalize_risk_details(data) -> list:
        """
        将任意来源的 risk_details 规范化为 list[{type, level, detail}]。
        非 list、非 dict 元素均丢弃，字段缺失用安全默认值填充。
        """
        if not isinstance(data, list):
            return []
        result = []
        for item in data:
            if not isinstance(item, dict):
                continue
            result.append({
                "type": str(item.get("type") or "未知风险"),
                "level": str(item.get("level") or "unknown"),
                "detail": str(item.get("detail") or item.get("description") or ""),
            })
        return result

    # =========================================================================
    # 多源状态数据接入辅助方法
    # =========================================================================

    def _get_file_extension(self, filename: str) -> str:
        """
        获取文件扩展名
        """
        import os
        return os.path.splitext(filename)[1].lower()

    def _parse_log_file(self, content: str, file_ext: str) -> List[Dict]:
        """
        解析不同类型的日志文件
        """
        try:
            if file_ext == ".json":
                # 尝试解析为 JSON 数组或单个 JSON 对象
                data = json.loads(content)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return [data]
                else:
                    return []
            elif file_ext == ".txt":
                # 按行解析文本文件
                lines = content.strip().split('\n')
                return [{'content': line.strip()} for line in lines if line.strip()]
            elif file_ext == ".csv":
                # 解析 CSV 文件
                import io
                reader = csv.DictReader(io.StringIO(content))
                return [dict(row) for row in reader]
            else:
                return []
        except Exception as exc:
            logger.error(f"解析日志文件失败: {exc}")
            return []

    def _filter_and_deduplicate(self, entries: List[Dict]) -> List[Dict]:
        """
        去重和过滤无效数据
        """
        seen = set()
        filtered = []
        
        for entry in entries:
            # 提取关键字段作为去重依据
            if isinstance(entry, dict):
                # 构建唯一标识
                key = str(entry.get('content') or entry.get('message') or entry.get('log') or str(entry))
                if key not in seen:
                    seen.add(key)
                    # 过滤空数据
                    if key.strip():
                        filtered.append(entry)
        
        return filtered

    def _annotate_logs(self, entries: List[Dict]) -> List[Dict]:
        """
        标注异常日志
        """
        annotated = []
        
        for entry in entries:
            # 复制原始数据
            annotated_entry = entry.copy()
            
            # 提取日志内容
            log_content = str(entry.get('content') or entry.get('message') or entry.get('log') or str(entry))

            # 标注异常级别
            # 优先保留来源已携带的级别（如「日志分析结果库」里抽取出的 level 字段）；
            # 仅在缺失/非法时才用内容正则推断——否则本就是 ERROR/FATAL 的条目会因
            # 中文正文不含英文级别词而被误判成 INFO，导致后续整体分级偏低（误判"一般"）。
            existing_level = str(entry.get('level') or '').upper()
            if existing_level == 'WARNING':
                existing_level = 'WARN'
            level = existing_level if existing_level in _VALID_LEVELS else self._detect_log_level(log_content)
            annotated_entry['level'] = level
            
            # 标注是否为异常
            is_abnormal = level in ['CRITICAL', 'FATAL', 'PANIC', 'ERROR', 'WARN']
            annotated_entry['is_abnormal'] = is_abnormal
            
            # 标注故障标签
            if is_abnormal:
                fault_label = self._detect_fault_label(log_content)
                annotated_entry['fault_label'] = fault_label
            
            annotated.append(annotated_entry)
        
        return annotated

    def _detect_log_level(self, log_content: str) -> str:
        """
        检测日志级别
        """
        match = _LEVEL_RE.search(log_content)
        if match:
            return match.group(1).upper()
        return 'INFO'

    def _detect_fault_label(self, log_content: str) -> str:
        """
        检测故障标签
        """
        # 简单的故障标签检测规则
        fault_patterns = {
            '网络故障': ['network', 'connection', 'timeout', 'socket'],
            '数据库故障': ['database', 'sql', 'db', 'mysql', 'postgres'],
            '内存故障': ['memory', 'heap', 'stack', 'out of memory'],
            'CPU故障': ['cpu', 'processor', 'high load'],
            '磁盘故障': ['disk', 'storage', 'io error', 'file system'],
            '应用故障': ['application', 'app', 'crash', 'exception'],
            '配置故障': ['config', 'configuration', 'setting'],
        }
        
        log_lower = log_content.lower()
        for label, patterns in fault_patterns.items():
            for pattern in patterns:
                if pattern in log_lower:
                    return label
        
        return '未知故障'

    def _write_logs_to_db(self, entries: List[Dict], filename: str, db: Session) -> List[LogEntry]:
        """
        将日志写入数据库，确保不重复接入
        """
        db_entries = []
        
        # 先查询数据库中已有的日志内容
        existing_logs = db.query(LogEntry.raw_content).all()
        existing_contents = set([log.raw_content for log in existing_logs])
        logger.info(f"数据库中已存在 {len(existing_contents)} 条日志")
        
        for entry in entries:
            # 构建日志内容
            log_content = str(entry.get('content') or '')
            
            # 检查是否已存在相同内容的日志
            if log_content in existing_contents:
                logger.info(f"日志已存在，跳过接入: {log_content[:50]}...")
                continue
            
            # 生成摘要
            summary = self._generate_log_summary(log_content, entry)
            
            # 创建 LogEntry 对象
            log_entry = LogEntry(
                filename=filename,
                raw_content=log_content,
                summary=summary,
                is_indexed=False
            )
            
            db.add(log_entry)
            db_entries.append(log_entry)
            # 更新已存在内容集合
            existing_contents.add(log_content)
        
        # 提交数据库事务
        db.commit()
        logger.info(f"本次接入 {len(db_entries)} 条新日志")
        
        # 刷新对象以获取 ID
        for entry in db_entries:
            db.refresh(entry)
        
        return db_entries

    def _generate_log_summary(self, log_content: str, entry: Dict) -> str:
        """
        生成日志摘要
        """
        level = entry.get('level', 'INFO')
        is_abnormal = entry.get('is_abnormal', False)
        fault_label = entry.get('fault_label', '无')
        
        # 生成摘要
        summary = f"[{level}] "
        if is_abnormal:
            summary += f"[异常] [故障类型: {fault_label}] "
        
        # 截取日志内容的前 100 个字符
        content_preview = log_content[:100] + ('...' if len(log_content) > 100 else '')
        summary += content_preview
        
        return summary

    def _generate_access_report(self, db_entries: List[LogEntry], filtered_entries: List[Dict], config: Dict) -> Dict:
        """
        生成接入报告
        """
        # 统计信息
        total_entries = len(filtered_entries)
        # 实际写入数据库的日志数量
        stored_count = len(db_entries)
        
        # 统计异常日志数量和故障分布
        abnormal_count = 0
        fault_labels = {}
        
        # 从 filtered_entries 中统计，因为 db_entries 是 LogEntry 对象，没有 is_abnormal 和 fault_label 字段
        for entry in filtered_entries:
            if entry.get('is_abnormal', False):
                abnormal_count += 1
                label = entry.get('fault_label', '未知故障')
                fault_labels[label] = fault_labels.get(label, 0) + 1
        
        # 生成报告
        report = {
            "total_logs": total_entries,
            "abnormal_logs": abnormal_count,
            "normal_logs": total_entries - abnormal_count,
            "fault_distribution": fault_labels,
            "stored_in_db": stored_count,
            "file_processed": True,
            "access_strategy": config.get('strategy', 'realtime'),
            "timestamp": datetime.utcnow().isoformat()
        }
        
        return report

    def _analyze_log_content(self, log_content: str) -> Dict:
        """
        分析日志内容，提取异常信息
        """
        # 检测日志级别
        level = self._detect_log_level(log_content)
        
        # 检测是否异常
        is_abnormal = level in ['CRITICAL', 'FATAL', 'PANIC', 'ERROR', 'WARN']
        
        # 检测故障标签
        fault_label = '无'
        if is_abnormal:
            fault_label = self._detect_fault_label(log_content)
        
        return {
            "level": level,
            "is_abnormal": is_abnormal,
            "fault_label": fault_label,
            "content_preview": log_content[:200] + ('...' if len(log_content) > 200 else '')
        }

    def extract_from_log_analysis(self, config: Dict, db: Session) -> Dict:
        """
        从日志分析结果库抽取关键日志
        
        Args:
            config: 接入配置
            db: 数据库会话
            
        Returns:
            包含处理结果和报告的字典
        """
        try:
            logger.info("开始从日志分析结果库抽取日志...")
            logger.info(f"接入配置: {config}")
            
            # 从实际的 MongoDB 数据库中读取日志数据
            log_entries = self._extract_from_log_analysis_db(config)
            
            logger.info(f"从 MongoDB 读取到 {len(log_entries)} 条日志数据")
            
            if not log_entries:
                # 如果没有从 MongoDB 读取到数据，返回错误信息
                logger.warning("从日志分析结果库抽取日志失败，未找到有效数据")
                return {
                    "success": False,
                    "message": "从日志分析结果库抽取日志失败，未找到有效数据"
                }
            
            # 去重和过滤无效数据
            filtered_entries = self._filter_and_deduplicate(log_entries)
            logger.info(f"过滤后剩余 {len(filtered_entries)} 条日志数据")
            
            if not filtered_entries:
                logger.warning("过滤后无有效日志数据")
                return {
                    "success": False,
                    "message": "过滤后无有效日志数据"
                }
            
            # 标注异常日志
            annotated_entries = self._annotate_logs(filtered_entries)
            logger.info(f"标注后有 {len(annotated_entries)} 条日志数据")
            
            # 写入数据库
            db_entries = self._write_logs_to_db(annotated_entries, "log_analysis", db)
            logger.info(f"写入数据库 {len(db_entries)} 条日志数据")
            
            # 生成接入报告（使用实际写入数据库的日志数量）
            report = self._generate_access_report(db_entries, annotated_entries, config)
            logger.info(f"生成接入报告: {report}")

            # 存储接入记录到 MongoDB（每次接入生成一份报告，带 report_id + 聚合文本）
            aggregated_text = self._aggregate_entries_text(annotated_entries)
            report_id = self._store_access_record(report, config, "database", aggregated_text)
            report["report_id"] = report_id
            report["run_id"] = report_id
            logger.info(f"成功存储接入报告到 MongoDB，report_id={report_id}")
            
            logger.info("从日志分析结果库抽取日志成功")
            return {
                "success": True,
                "message": "从日志分析结果库抽取日志成功",
                "report": report
            }
        except Exception as exc:
            logger.error(f"从日志分析结果库抽取日志失败: {exc}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "message": f"从日志分析结果库抽取日志失败: {str(exc)}"
            }

    def _calculate_log_score(self, document: Dict) -> float:
        """
        根据日志级别和其他特征计算日志评分
        返回 0-100 的评分
        """
        level = document.get('level', '').upper()
        content = document.get('content', '').lower()

        base_scores = {
            'FATAL': 100,
            'CRITICAL': 95,
            'ERROR': 85,
            'WARN': 65,
            'WARNING': 65,
            'INFO': 45,
            'DEBUG': 25,
            'TRACE': 10
        }

        score = base_scores.get(level, 50)

        critical_keywords = ['crash', 'panic', 'fatal', 'critical error']
        high_keywords = ['error', 'fail', 'exception', 'timeout']
        medium_keywords = ['warn', 'warning', 'high cpu', 'memory', 'disk']

        for keyword in critical_keywords:
            if keyword in content:
                score = max(score, 90)

        for keyword in high_keywords:
            if keyword in content:
                score = max(score, 70)

        for keyword in medium_keywords:
            if keyword in content:
                score = max(score, 55)

        return min(score, 100)

    def _extract_from_log_analysis_db(self, config: Dict) -> List[Dict]:
        """
        从实际的 MongoDB 数据库中读取日志数据
        根据 scoreThreshold 和 tagTypes 过滤数据
        """
        logs = []

        score_threshold = config.get('scoreThreshold', 0)
        # 处理空字符串 / None / 空白：一律视为 0，避免把所有日志都过滤掉
        if score_threshold is None or (isinstance(score_threshold, str) and not score_threshold.strip()):
            score_threshold = 0
        # 确保是数字
        try:
            score_threshold = float(score_threshold)
        except (ValueError, TypeError):
            score_threshold = 0

        tag_types = config.get('tagTypes', [])

        if isinstance(tag_types, str):
            try:
                tag_types = json.loads(tag_types)
                logger.info(f"解析 tagTypes 字符串为: {tag_types}")
            except Exception as e:
                logger.error(f"解析 tagTypes 失败: {e}")
                tag_types = []
        elif not isinstance(tag_types, list):
            tag_types = []

        tag_level_mapping = {
            'fault': ['ERROR', 'FATAL', 'CRITICAL'],
            'abnormal': ['WARN', 'WARNING'],
            'warning': ['WARN', 'WARNING'],
            'info': ['INFO', 'DEBUG', 'TRACE'],
            'normal': ['INFO', 'DEBUG', 'TRACE'],
        }

        logger.info(f"过滤条件 - 评分阈值: {score_threshold}, 标签类型: {tag_types}")
        logger.info(f"tag_types 类型: {type(tag_types)}, 是否为空: {not tag_types}")

        try:
            logger.info("开始连接 MongoDB...")
            client = self._get_mongo_client()
            if not client:
                logger.error("无法连接到 MongoDB，返回空列表")
                return logs

            try:
                logger.info("尝试访问 log_analysis_results 数据库...")
                db = client['log_analysis_results']
                logger.info("成功访问 log_analysis_results 数据库")
            except Exception as db_exc:
                logger.error(f"无法访问 log_analysis_results 数据库: {db_exc}")
                client.close()
                return logs

            try:
                logger.info("尝试列出所有集合...")
                collections = db.list_collection_names()
                logger.info(f"log_analysis_results 数据库中的集合: {collections}")

                if not collections:
                    logger.warning("log_analysis_results 数据库中没有集合")
                    client.close()
                    return logs
            except Exception as coll_exc:
                logger.error(f"无法列出集合: {coll_exc}")
                client.close()
                return logs

            for collection_name in collections:
                try:
                    logger.info(f"尝试处理集合 {collection_name}...")
                    collection = db[collection_name]
                    count = collection.count_documents({})
                    logger.info(f"集合 {collection_name} 中的文档数量: {count}")

                    documents = list(collection.find({}))
                    logger.info(f"从集合 {collection_name} 读取到 {len(documents)} 个文档")

                    if documents:
                        logger.info(f"集合 {collection_name} 的文档结构: {list(documents[0].keys())}")

                        for document in documents:
                            log_score = self._calculate_log_score(document)
                            document['score'] = log_score

                            logger.info(f"日志评分: {log_score}, 内容: {document.get('content', '')[:50]}")
                            logger.info(f"日志级别: {document.get('level', '').upper()}")

                            if log_score < score_threshold:
                                logger.info(f"日志评分 {log_score} 低于阈值 {score_threshold}，跳过")
                                continue

                            log_level = document.get('level', '').upper()
                            if tag_types:
                                matched = False
                                for tag_type in tag_types:
                                    if tag_type in tag_level_mapping:
                                        logger.info(f"检查标签类型 {tag_type}，对应级别: {tag_level_mapping[tag_type]}")
                                        if log_level in tag_level_mapping[tag_type]:
                                            matched = True
                                            logger.info(f"日志级别 {log_level} 匹配标签类型 {tag_type}")
                                            break
                                if not matched:
                                    logger.info(f"日志级别 {log_level} 不在标签类型 {tag_types} 中，跳过")
                                    continue
                            else:
                                logger.info(f"标签类型为空，跳过标签过滤")

                            if 'content' in document:
                                logs.append({
                                    "content": document['content'],
                                    "level": document.get('level', ''),
                                    "score": log_score
                                })
                                logger.info(f"添加日志到结果列表，内容: {document['content'][:50]}")
                            elif 'message' in document:
                                logs.append({
                                    "content": document['message'],
                                    "level": document.get('level', ''),
                                    "score": log_score
                                })
                                logger.info(f"添加日志到结果列表，内容: {document['message'][:50]}")
                            elif 'log' in document:
                                logs.append({
                                    "content": document['log'],
                                    "level": document.get('level', ''),
                                    "score": log_score
                                })
                                logger.info(f"添加日志到结果列表，内容: {document['log'][:50]}")
                            elif 'text' in document:
                                logs.append({
                                    "content": document['text'],
                                    "level": document.get('level', ''),
                                    "score": log_score
                                })
                                logger.info(f"添加日志到结果列表，内容: {document['text'][:50]}")
                            else:
                                logs.append({
                                    "content": str(document),
                                    "level": document.get('level', ''),
                                    "score": log_score
                                })
                                logger.info(f"添加日志到结果列表，内容: {str(document)[:50]}")

                        if logs:
                            logger.info(f"从集合 {collection_name} 中找到 {len(logs)} 条符合条件的日志，跳出循环")
                            break
                    else:
                        logger.warning(f"集合 {collection_name} 中没有文档")
                except Exception as coll_exc:
                    logger.error(f"处理集合 {collection_name} 时出错: {coll_exc}")
                    import traceback
                    traceback.print_exc()
                    continue
        except Exception as exc:
            logger.error(f"从 MongoDB 读取日志数据失败: {exc}")
            import traceback
            traceback.print_exc()
        finally:
            if 'client' in locals() and client:
                try:
                    client.close()
                    logger.info("成功关闭 MongoDB 连接")
                except Exception as close_exc:
                    logger.error(f"关闭 MongoDB 连接时出错: {close_exc}")

        logger.info(f"从 MongoDB 读取并过滤后的日志数量: {len(logs)}")

        # ── 兜底：log_analysis_results 中没有任何符合条件的数据时，
        #         回退到 log_entries 集合（一次普通日志上传后这里就有数据）。
        if not logs:
            logger.info("log_analysis_results 无符合条件数据，回退读取 log_entries 集合")
            try:
                logs = self._extract_from_log_entries(
                    score_threshold=score_threshold,
                    tag_types=tag_types,
                    tag_level_mapping=tag_level_mapping,
                )
                logger.info(f"从 log_entries 兜底读取到 {len(logs)} 条日志")
            except Exception as exc:
                logger.error(f"从 log_entries 兜底读取失败: {exc}")

        return logs

    def _extract_from_log_entries(
        self,
        score_threshold: float,
        tag_types: List,
        tag_level_mapping: Dict[str, List[str]],
    ) -> List[Dict]:
        """
        兜底数据源：从 log_entries 集合读取日志，套用相同的
        标签级别 + 评分过滤。时间范围不在此兜底路径中应用
        （字段不确定时宁可不过滤，也不返回空）。
        """
        from app.database import get_mongo_db

        logs: List[Dict] = []
        mongo_db = get_mongo_db()
        docs = list(
            mongo_db["log_entries"].find(
                {},
                {"level": 1, "message": 1, "raw_line": 1, "_id": 0},
            ).limit(5000)
        )

        for document in docs:
            content = document.get("message") or document.get("raw_line")
            if not content:
                continue

            doc_for_score = {
                "level": document.get("level") or "",
                "content": str(content),
            }
            log_score = self._calculate_log_score(doc_for_score)

            if log_score < score_threshold:
                continue

            log_level = (document.get("level") or "").upper()
            if tag_types:
                matched = False
                for tag_type in tag_types:
                    if tag_type in tag_level_mapping and log_level in tag_level_mapping[tag_type]:
                        matched = True
                        break
                if not matched:
                    continue

            logs.append({
                "content": str(content),
                "level": document.get("level") or "",
                "score": log_score,
            })

        return logs

    def _store_access_record(
        self, report: Dict, config: Dict, source: str, aggregated_text: str = ""
    ) -> Optional[str]:
        """存储接入记录（=「接入报告」）到 MongoDB，返回 report_id。

        每个报告带稳定 report_id(=run_id) 与 aggregated_text（聚合多源数据，
        当前唯一源=接入日志）。后续「软件状态预测」与「故障诊断」都以该报告
        为单元：预测对 aggregated_text 整体分级，诊断按 run_id 取该文本建窗口。
        """
        report_id = str(uuid.uuid4())
        client = self._get_mongo_client()
        if not client:
            return report_id

        try:
            db = client[settings.MONGO_DB]
            collection = db['access_records']

            # 构建接入记录（report_id == run_id，作为报告的唯一标识）
            record = {
                "report_id": report_id,
                "run_id": report_id,
                "access_time": datetime.utcnow(),
                "source": source,
                "total_logs": report.get("total_logs", 0),
                "unique_logs": report.get("total_logs", 0),  # 去重后数量
                "tagged_logs": report.get("abnormal_logs", 0),  # 标记数量
                "status": "success",
                "report": report,
                "config": config,
                # 聚合文本（截断，供预测/诊断整体消费）
                "aggregated_text": (aggregated_text or "")[:20000],
            }

            # 插入记录
            collection.insert_one(record)
            logger.info(f"成功存储接入报告到 MongoDB，report_id={report_id}")
        except Exception as exc:
            logger.error(f"存储接入记录到 MongoDB 失败: {exc}")
        finally:
            if client:
                client.close()
        return report_id

    @staticmethod
    def _aggregate_entries_text(entries: List[Dict], max_chars: int = 20000) -> str:
        """把本次接入的日志条目拼成一份聚合文本（报告的整体数据）。

        每行带上 `[LEVEL]` 前缀（来自标注/来源级别），确保下游软件状态分级的
        规则评分(_LEVEL_RE)能扫到 ERROR/FATAL/WARN，避免「选了故障/异常标签的报告
        却因正文不含英文级别词而被判成一般」。大模型也能据此看清严重度。
        """
        parts: List[str] = []
        total = 0
        for e in entries:
            c = str(e.get("content") or e.get("message") or e.get("log") or "").strip()
            if not c:
                continue
            lvl = str(e.get("level") or "").upper()
            if lvl == "WARNING":
                lvl = "WARN"
            line = f"[{lvl}] {c}" if lvl in _VALID_LEVELS else c
            parts.append(line)
            total += len(line)
            if total >= max_chars:
                break
        return "\n".join(parts)[:max_chars]

    def _load_report(self, report_id: str) -> Optional[Dict]:
        """按 report_id 读取接入报告记录（含 aggregated_text）。"""
        client = self._get_mongo_client()
        if not client:
            return None
        try:
            db = client[settings.MONGO_DB]
            return db['access_records'].find_one({"report_id": report_id})
        except Exception as exc:
            logger.error(f"读取接入报告失败 report_id={report_id}: {exc}")
            return None
        finally:
            if client:
                client.close()

    def grade_report(self, report_id: str, db: Session) -> dict:
        """对「接入报告」整体做大模型软件状态分级（一般/严重/紧急）。

        取报告 aggregated_text 作为融合后的多源数据整体交给 predict()；
        报告缺失或无文本时回退到 grade_run（按 run_id 取窗口/条目），
        二者最终都经 predict() 的规则兜底，保证优雅降级。
        """
        rec = self._load_report(report_id)
        text = (rec or {}).get("aggregated_text") if rec else None

        # 报告级统计下限：接入时已算定 abnormal_logs（异常条数），是权威值。
        # 只要报告含异常 → 分级至少「严重」，杜绝散落异常被 LLM 漏看而误判「一般」。
        # 该下限取自报告自身统计，对【已接入的旧报告】也立即生效，无需重新接入。
        report = (rec or {}).get("report") or {}
        abnormal = 0
        try:
            abnormal = int(report.get("abnormal_logs") or 0)
        except (TypeError, ValueError):
            abnormal = 0
        floor_status = None
        floor_summary = None
        if abnormal > 0:
            floor_status = "yellow"  # 至少「严重」；红色由规则/LLM 视 ERROR/致命量级抬升
            dist = report.get("fault_distribution") or {}
            dist_str = "、".join(f"{k} {v} 条" for k, v in dist.items()) if isinstance(dist, dict) else ""
            floor_summary = (
                f"接入报告共 {report.get('total_logs', 0)} 条日志，检出异常 {abnormal} 条"
                + (f"（{dist_str}）" if dist_str else "")
                + "，存在影响运行的问题，需尽快处理。"
            )

        if text and text.strip():
            return self.predict(
                text, {}, db, run_id=report_id,
                floor_status=floor_status, floor_summary=floor_summary,
            )
        # 兜底：报告未携带聚合文本（旧报告）→ 退回按 run_id 取窗口
        return self.grade_run(report_id, db, floor_status=floor_status, floor_summary=floor_summary)

    def latest_prediction_out(self, run_id: str, db: Session) -> Optional[dict]:
        """返回某接入报告/日志(run_id)最近一次的分级记录（dict），无则 None。

        供「软件状态预测」列表回显已存分级、以及点行回放分级详情——分级结果本就
        落库（PredictionRecord.run_id == report_id），此前前端未读回导致离开页面即丢。
        """
        record = (
            db.query(PredictionRecord)
            .filter(PredictionRecord.run_id == run_id)
            .order_by(PredictionRecord.created_at.desc())
            .first()
        )
        if record is None:
            return None
        return {
            "id": record.id,
            "run_id": record.run_id,
            "health_status": record.health_status,
            "risk_summary": record.risk_summary,
            "risk_details": record.risk_details,
            "cpu_usage": record.cpu_usage,
            "memory_usage": record.memory_usage,
            "disk_usage": record.disk_usage,
            "temperature": record.temperature,
            "created_at": record.created_at,
        }

    def delete_report(self, report_id: str, db: Session) -> dict:
        """级联删除整份接入报告：access_records(report_id) + 其分级记录(PredictionRecord)
        + 其诊断记录(DiagnosisRecord)（均按 run_id == report_id）。

        任一子步骤失败不阻断其余删除，最终统一提交。删除后该报告从「多源接入 /
        软件状态预测 / 分级预警」三页同时消失（报告为一份）。
        """
        from app.models.diagnosis_record import DiagnosisRecord

        deleted_access = 0
        client = self._get_mongo_client()
        if client:
            try:
                coll = client[settings.MONGO_DB]['access_records']
                res = coll.delete_one({"report_id": report_id})
                deleted_access = getattr(res, "deleted_count", 0) or 0
            except Exception as exc:
                logger.error(f"删除接入报告失败 report_id={report_id}: {exc}")
            finally:
                client.close()

        deleted_pred = (
            db.query(PredictionRecord)
            .filter(PredictionRecord.run_id == report_id)
            .delete(synchronize_session=False)
        )
        deleted_diag = (
            db.query(DiagnosisRecord)
            .filter(DiagnosisRecord.run_id == report_id)
            .delete(synchronize_session=False)
        )
        db.commit()
        return {
            "ok": True,
            "deleted_access": int(deleted_access or 0),
            "deleted_predictions": int(deleted_pred or 0),
            "deleted_diagnoses": int(deleted_diag or 0),
        }

    def get_access_results(self, params: Dict, db: Session) -> Dict:
        """
        获取接入结果列表，支持分页和查询条件
        
        Args:
            params: 查询参数
            db: 数据库会话
            
        Returns:
            包含分页信息和接入结果的字典
        """
        client = self._get_mongo_client()
        if not client:
            return {"total": 0, "items": []}
        
        try:
            db_mongo = client[settings.MONGO_DB]
            collection = db_mongo['access_records']
            
            # 构建查询条件
            query = {}
            
            # 时间范围查询
            if params.get('startDate'):
                query['access_time'] = {"$gte": datetime.fromisoformat(params['startDate'])}
            if params.get('endDate'):
                if 'access_time' not in query:
                    query['access_time'] = {}
                query['access_time']["$lte"] = datetime.fromisoformat(params['endDate'])
            
            # 来源查询
            if params.get('source'):
                query['source'] = params['source']
            

            
            # 计算总数
            total = collection.count_documents(query)
            
            # 分页查询
            page = params.get('page', 1)
            pageSize = params.get('pageSize', 10)
            skip = (page - 1) * pageSize
            
            # 执行查询
            records = collection.find(query).sort('access_time', -1).skip(skip).limit(pageSize)
            
            # 转换结果
            items = []
            for record in records:
                item = {
                    "id": str(record.get('_id')),
                    "report_id": record.get('report_id'),
                    "run_id": record.get('run_id') or record.get('report_id'),
                    "accessTime": record.get('access_time').isoformat() if record.get('access_time') else None,
                    "source": record.get('source'),
                    "totalLogs": record.get('total_logs'),
                    "uniqueLogs": record.get('unique_logs'),
                    "taggedLogs": record.get('tagged_logs'),
                    "severityLevel": record.get('severity_level'),
                    "status": record.get('status'),
                    "report": record.get('report')
                }
                items.append(item)
            
            return {
                "total": total,
                "items": items
            }
        except Exception as exc:
            logger.error(f"获取接入结果列表失败: {exc}")
            return {"total": 0, "items": []}
        finally:
            if client:
                client.close()
