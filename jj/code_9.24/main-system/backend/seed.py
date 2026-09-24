"""
seed.py — 预设故障类型和示例日志
幂等运行：如果数据已存在则跳过，不重复插入。
"""
import sys
import time
from sqlalchemy.orm import Session
from app.database import engine, SessionLocal
from app.models import FaultType, LogEntry, System, Case, Run, Ingestion

# ─── 预设故障类型 ────────────────────────────────────────────────────────────────
FAULT_TYPES = [
    {"name": "内存溢出(OOM)", "description": "Java 堆内存耗尽或系统 OOM Killer 触发", "color_tag": "red"},
    {"name": "CPU 过载", "description": "CPU 持续高负载，线程死锁或无限循环", "color_tag": "red"},
    {"name": "磁盘空间不足", "description": "磁盘写满，日志文件无法写入或数据库崩溃", "color_tag": "yellow"},
    {"name": "数据库连接池耗尽", "description": "连接池达到上限，新请求无法获取数据库连接", "color_tag": "red"},
    {"name": "网络连接超时", "description": "下游服务不可达，TCP 连接超时或拒绝连接", "color_tag": "yellow"},
]

# ─── 预设示例日志（每种故障 2 条）────────────────────────────────────────────────
SAMPLE_LOGS = {
    "内存溢出(OOM)": [
        (
            "oom_java_heap.log",
            """2024-03-15 14:22:31.456 ERROR [main] --- Exception in thread "main" java.lang.OutOfMemoryError: Java heap space
    at java.util.Arrays.copyOf(Arrays.java:3210)
    at java.util.ArrayList.grow(ArrayList.java:265)
    at com.example.service.DataProcessor.processLargeDataset(DataProcessor.java:142)
    at com.example.service.DataProcessor.run(DataProcessor.java:89)
Caused by: java.lang.RuntimeException: Allocation failed after 3 retries
    ... 25 more
2024-03-15 14:22:31.460 ERROR [main] --- JVM heap dump written to: /tmp/heap_dump_20240315.hprof
2024-03-15 14:22:31.461 FATAL [main] --- Application terminated due to OutOfMemoryError""",
            "Java 堆内存耗尽，ArrayList 扩容失败",
        ),
        (
            "oom_kernel.log",
            """Mar 15 09:14:27 prod-server kernel: Out of memory: Kill process 4821 (java) score 892 or sacrifice child
Mar 15 09:14:27 prod-server kernel: Killed process 4821 (java) total-vm:8192000kB, anon-rss:7654321kB, file-rss:1024kB, shmem-rss:0kB
Mar 15 09:14:27 prod-server kernel: oom_reaper: reaped process 4821 (java), now anon-rss:0kB, file-rss:0kB, shmem-rss:0kB
Mar 15 09:14:28 prod-server systemd[1]: app-service.service: Main process exited, code=killed, status=9/KILL
Mar 15 09:14:28 prod-server systemd[1]: app-service.service: Failed with result 'signal'.
Mar 15 09:14:29 prod-server systemd[1]: app-service.service: Scheduled restart job, restart counter is at 5.""",
            "Linux kernel OOM Killer 终止 JVM 进程",
        ),
    ],
    "CPU 过载": [
        (
            "cpu_overload_thread_dump.log",
            """2024-03-16 10:45:00.000 WARN  [monitoring] --- CPU usage: 98.7% (threshold: 80%)
2024-03-16 10:45:00.001 WARN  [monitoring] --- Load average: 15.34 (cores: 4)
2024-03-16 10:45:01.200 ERROR [thread-pool-1] --- Thread dump initiated due to high CPU
"pool-1-thread-42" #312 prio=5 os_prio=0 tid=0x00007f8abc RUNNABLE
    java.lang.Thread.State: RUNNABLE
    at com.example.util.HashUtil.computeHash(HashUtil.java:55)
    at com.example.service.CacheService.rebuildCache(CacheService.java:201)
    ... (repeated 38 times - possible infinite loop)
2024-03-16 10:45:05.000 ERROR [monitoring] --- Detected 38 threads stuck in CacheService.rebuildCache
2024-03-16 10:45:05.001 WARN  [monitoring] --- Response time degraded: p99=12340ms (normal: 200ms)""",
            "线程池中多线程陷入 rebuildCache 死循环",
        ),
        (
            "cpu_deadlock.log",
            """2024-03-17 08:30:14.123 ERROR [watchdog] --- DEADLOCK DETECTED
Thread 1: "http-nio-8080-exec-1" waiting for lock <0x00000006c2d12345> held by Thread 2
Thread 2: "http-nio-8080-exec-2" waiting for lock <0x00000006c2d67890> held by Thread 1
2024-03-17 08:30:14.124 ERROR [watchdog] --- Thread 1 stack:
    at com.example.db.ConnectionPool.acquire(ConnectionPool.java:88)
    at com.example.service.UserService.getUser(UserService.java:45)
2024-03-17 08:30:14.125 ERROR [watchdog] --- Thread 2 stack:
    at com.example.cache.RedisClient.get(RedisClient.java:112)
    at com.example.service.OrderService.getOrder(OrderService.java:67)
2024-03-17 08:30:20.000 FATAL [watchdog] --- System unresponsive, initiating restart""",
            "HTTP 工作线程死锁，系统无响应",
        ),
    ],
    "磁盘空间不足": [
        (
            "disk_full_applog.log",
            """2024-03-18 23:59:41.001 ERROR [log-writer] --- Failed to write log: /var/log/app/app.log
java.io.IOException: No space left on device
    at java.io.FileOutputStream.write(FileOutputStream.java:337)
    at ch.qos.logback.core.rolling.RollingFileAppender.writeOut(RollingFileAppender.java:292)
2024-03-18 23:59:41.050 FATAL [db-writer] --- Database write failed: disk quota exceeded
    org.postgresql.util.PSQLException: ERROR: could not extend file "base/16384/2619": No space left on device
2024-03-18 23:59:42.100 ERROR [monitoring] --- Disk usage: /dev/sda1 100% (available: 0 KB)
2024-03-18 23:59:42.200 WARN  [monitoring] --- Emergency: stopping non-critical services to free disk space""",
            "磁盘写满，日志和数据库写入均失败",
        ),
        (
            "disk_inode_exhausted.log",
            """Mar 18 22:10:05 app-server01 kernel: EXT4-fs error (device sdb1): ext4_find_entry: inode #5 (comm rsyslogd): reading directory lblock 0
Mar 18 22:10:06 app-server01 app: ERROR - Cannot create temp file: No space left on device (inode exhausted)
Mar 18 22:10:07 app-server01 app: ERROR - Session creation failed: unable to write session file /tmp/sess_abc123
df output: /dev/sdb1 500G 500G 0 100% /data
df -i output: /dev/sdb1 32000000 32000000 0 100% /data
Mar 18 22:10:10 app-server01 cron: (root) CMD (find /tmp -name 'sess_*' -mmin +60 -delete) failed: No space left on device""",
            "inode 耗尽，/tmp 下无法创建临时文件",
        ),
    ],
    "数据库连接池耗尽": [
        (
            "db_pool_exhausted.log",
            """2024-03-19 15:33:10.001 ERROR [http-exec-15] --- Unable to acquire JDBC Connection
org.hibernate.exception.JDBCConnectionException: Unable to acquire JDBC Connection
    at org.hibernate.engine.jdbc.spi.SqlExceptionHelper.convert(SqlExceptionHelper.java:147)
    at com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:213)
    at com.example.service.ReportService.generateReport(ReportService.java:78)
Caused by: com.zaxxer.hikari.pool.HikariPool$PoolInitializationException: Failed to initialize pool: FATAL: remaining connection slots are reserved
2024-03-19 15:33:10.002 WARN  [hikari-pool] --- HikariPool-1 - Connection is not available, request timed out after 30000ms
2024-03-19 15:33:10.003 WARN  [hikari-pool] --- HikariPool-1 - Pool stats (total=20, active=20, idle=0, waiting=47)
2024-03-19 15:33:12.000 ERROR [monitoring] --- Active DB connections: 20/20, waitQueue: 47, avgWaitTime: 28340ms""",
            "HikariCP 连接池耗尽，47个请求等待获取连接",
        ),
        (
            "db_connection_leak.log",
            """2024-03-20 11:20:00.001 WARN  [hikari-leak-detection] --- Connection leak detection triggered for com.zaxxer.hikari.pool.ProxyConnection@7f8a2b (wrapped by com.zaxxer.hikari.pool.HikariProxyConnection@6c3d9e)
    at com.example.dao.LegacyDao.executeQuery(LegacyDao.java:234)
    at com.example.batch.DataMigration.run(DataMigration.java:89)
stack trace omitted (leakDetectionThreshold=30000ms exceeded)
2024-03-20 11:35:00.000 ERROR [hikari-pool] --- Pool HikariPool-1 is dead and cannot be used: timed out waiting for an idle object
2024-03-20 11:35:00.001 FATAL [app] --- DataSource unavailable, all 20 connections leaked, application non-functional""",
            "连接泄漏导致连接池完全耗尽",
        ),
    ],
    "网络连接超时": [
        (
            "network_timeout_downstream.log",
            """2024-03-21 09:15:22.001 ERROR [http-exec-3] --- Failed to call downstream service: payment-service
java.net.SocketTimeoutException: Read timed out
    at java.net.SocketInputStream.socketRead0(Native Method)
    at com.example.client.PaymentClient.charge(PaymentClient.java:156)
    at com.example.service.OrderService.placeOrder(OrderService.java:234)
2024-03-21 09:15:22.002 WARN  [circuit-breaker] --- CircuitBreaker 'payment-service' OPEN: 15/20 calls failed in last 10s
2024-03-21 09:15:22.003 ERROR [http-exec-3] --- Fallback triggered: returning cached response
2024-03-21 09:15:30.000 WARN  [monitoring] --- payment-service unreachable for 8s, error rate: 75%
2024-03-21 09:15:30.001 ALERT [monitoring] --- SLA breach: order success rate dropped to 23% (threshold: 99%)""",
            "支付服务不可达，熔断器开启，订单成功率暴跌",
        ),
        (
            "network_dns_failure.log",
            """Mar 21 14:02:11 app-server02 app[12345]: ERROR: DNS resolution failed for 'db.internal.example.com'
Mar 21 14:02:11 app-server02 app[12345]: java.net.UnknownHostException: db.internal.example.com: Name or service not known
Mar 21 14:02:12 app-server02 app[12345]: Retry 1/3: DNS lookup for db.internal.example.com
Mar 21 14:02:17 app-server02 app[12345]: Retry 2/3: DNS lookup for db.internal.example.com
Mar 21 14:02:22 app-server02 app[12345]: Retry 3/3: DNS lookup for db.internal.example.com
Mar 21 14:02:22 app-server02 app[12345]: FATAL: Cannot resolve database hostname after 3 retries, giving up
Mar 21 14:02:23 app-server02 systemd[1]: app.service: Main process exited, code=exited, status=1/FAILURE
Mar 21 14:02:23 app-server02 systemd[1]: app.service: Failed with result 'exit-code'.""",
            "内部 DNS 解析失败，应用无法连接数据库主机",
        ),
    ],
}


def seed_database(db: Session) -> None:
    # 检查是否已有数据
    if db.query(FaultType).count() > 0:
        print("[seed] 数据已存在，跳过预设数据注入")
        return

    print("[seed] 开始注入预设故障类型和示例日志...")

    type_map = {}
    for ft_data in FAULT_TYPES:
        ft = FaultType(**ft_data)
        db.add(ft)
        db.flush()
        type_map[ft_data["name"]] = ft
        print(f"[seed] 创建故障类型: {ft_data['name']}")

    for fault_name, logs in SAMPLE_LOGS.items():
        ft = type_map[fault_name]
        for filename, content, summary in logs:
            le = LogEntry(
                fault_type_id=ft.id,
                filename=filename,
                raw_content=content,
                summary=summary,
                is_indexed=False,
            )
            db.add(le)
            print(f"[seed]   添加日志: {filename}")

    db.commit()

    # 向量化入库（如果 DashScope API Key 已配置）
    from app.config import settings
    if settings.DASHSCOPE_API_KEY and settings.DASHSCOPE_API_KEY != "sk-xxxxxxxxxxxxxxxx":
        print("[seed] 开始向量化日志入库（ChromaDB）...")
        from app.services.vector_store import VectorStoreService
        vs = VectorStoreService()
        logs_to_index = db.query(LogEntry).filter(LogEntry.is_indexed == False).all()
        for le in logs_to_index:
            try:
                ft = db.query(FaultType).filter(FaultType.id == le.fault_type_id).first()
                doc_id = f"log_entry_{le.id}"
                vs.add_document(
                    doc_id=doc_id,
                    text=le.raw_content,
                    metadata={
                        "log_entry_id": le.id,
                        "fault_type_name": ft.name if ft else "未知",
                        "fault_type_id": le.fault_type_id or 0,
                    },
                )
                le.chroma_doc_id = doc_id
                le.is_indexed = True
                print(f"[seed]   向量化完成: {le.filename}")
            except Exception as e:
                print(f"[seed]   向量化失败 {le.filename}: {e}")
        db.commit()
        print(f"[seed] 向量化完成")
    else:
        print("[seed] 未配置 DASHSCOPE_API_KEY，跳过向量化（可在知识库页面手动点击\"一键入库\"）")

    print("[seed] 预设数据注入完成！")


def wait_for_db(max_retries: int = 30) -> None:
    from sqlalchemy import text
    for i in range(max_retries):
        # try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
    #     except Exception:
    #         print(f"[seed] 等待数据库就绪... ({i+1}/{max_retries})")
    #         time.sleep(2)
    # raise RuntimeError("数据库连接超时，种子脚本退出")


if __name__ == "__main__":
    wait_for_db()
    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()
