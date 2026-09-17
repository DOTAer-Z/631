# 单个 Test 数据介绍

更新时间：2026-05-28

## 1. 这份文档说明什么

这份文档专门解释：

- 一个 `Test_<id>` 目录里都有哪些数据
- 每一部分数据分别是什么内容
- 每一部分数据在后续数据处理、训练、分析里有什么用

这里以真实样本：

- `/home/nanye/fault/data/dataset/NuttX/Test_1`

作为例子说明。


## 2. 一个 Test 的整体结构

当前一个 `Test` 的真实目录结构大致如下：

```text
Test_<id>/
├── fip_info.data
├── ground_truth.json
└── logs/
    ├── round_1/
    │   ├── faults/fault_events.log
    │   ├── foreground_wl/*.out.log.bzip2.out
    │   ├── monitor/system_metrics.log
    │   └── nuttx/qemu_console.log
    └── round_2/
        ├── faults/fault_events.log
        ├── foreground_wl/*.out.log.bzip2.out
        ├── monitor/system_metrics.log
        └── nuttx/qemu_console.log
```

可以把它理解成两层：

1. 标签层
   - `fip_info.data`
   - `ground_truth.json`

2. 日志层
   - `logs/round_1/...`
   - `logs/round_2/...`


## 3. round_1 和 round_2 分别是什么

### 3.1 `round_1`

`round_1` 表示故障轮。

也就是：

- 这一轮运行里故障场景被触发了
- NuttX 输出了对应的异常日志
- 外层也记录了故障事件和监控信息

### 3.2 `round_2`

`round_2` 表示正常/基线轮。

也就是：

- 这一轮不是故障注入运行
- 它作为对照样本存在
- 用来和故障轮做比较

这个设计的意义很大，因为一个 `Test` 不是只有异常数据，还同时带了“同类条件下的正常表现”。


## 4. `fip_info.data` 是什么

文件例子：

- [fip_info.data](/home/nanye/fault/data/dataset/NuttX/Test_1/fip_info.data)

示例内容：

```text
FAULT_TYPE: TASK_DEADLOCK_MUTEX_AB
TARGET_COMPONENT: nuttx/mission_task
TARGET_CLASS: none
TARGET_FUNCTION_DEF: src/mission/mission_task.c+212; 1
FAULT_POINT: nuttx_task_deadlock_mutex_ab_20260522t111503z_r001
```

### 4.1 它是什么

它是当前 `data_pipeline` 使用的轻量标签入口文件。

里面放的是最基础的监督信息：

- 这条样本是什么故障类型
- 主要责任模块是谁
- 主要责任函数定义是谁
- 这条故障样本对应的故障点标识是什么

### 4.2 它有什么用

它的作用主要是：

- 让 `data_pipeline` 能快速识别这条 `Test` 的标签
- 为 MySQL 标签表和任务表提供最基础的输入
- 给旧链路和轻量监督场景用

### 4.3 它的特点

它的信息比较少，属于：

- 最小标签入口
- 粗粒度监督信息

它不负责详细根因描述，也不负责完整证据链。


## 5. `ground_truth.json` 是什么

文件例子：

- [ground_truth.json](/home/nanye/fault/data/dataset/NuttX/Test_1/ground_truth.json)

### 5.1 它是什么

它是当前更完整、更适合训练的监督文件。

它不是给原始日志展示看的，而是给训练样本构建、监督学习、根因分析任务使用的。

### 5.2 当前主要字段

当前典型字段包括：

- `version`
- `sample_class`
- `fault_type`
- `domain`
- `source_session_id`
- `fault_point`
- `root_cause_text`
- `root_cause_mechanism`
- `root_cause_evidence`
- `injection`
- `propagation`
- `impact`
- `recovery`

### 5.3 每个字段大概是什么意思

#### `sample_class`

表示这条样本属于：

- `fault`
- `normal`

它是最直接的异常/正常标签。

#### `fault_type`

表示故障类型，例如：

- `TASK_DEADLOCK_MUTEX_AB`

它主要用于：

- 故障类型分类
- 故障检索
- 标签统计

#### `domain`

表示这条故障属于哪个大域，例如：

- `TASK`
- `MEM`
- `COMMS`
- `BOOT`
- `POWER`

它用于更粗粒度的任务归类。

#### `root_cause_text`

这是自然语言形式的根因描述。

例如当前样本里是：

- “任务 A 与任务 B 对共享互斥锁的获取顺序不一致，形成循环等待，最终导致死锁。”

它主要用于：

- 根因分析训练
- 生成式诊断任务
- SFT/RCA 数据构建

#### `root_cause_mechanism`

这是机制标签，强调“根因属于哪一类机制”。

例如：

- `lock_order_inversion_circular_wait`

它主要用于：

- 稳定的根因类别建模
- 机制级分类
- 减少只依赖自然语言文本带来的不稳定性

#### `root_cause_evidence`

这是证据链。

它不是一句总结，而是一组从日志里抽出来的关键证据点。

每个证据项一般包括：

- `stage`
- `timestamp`
- `module`
- `level`
- `message`
- `component`
- `function_def`

它主要用于：

- 训练模型学会“根据证据做根因判断”
- 做 RCA 可解释性监督
- 支持多跳诊断

#### `injection`

表示故障注入点。

本质上是在说：

- 故障最开始从哪里出现

#### `propagation`

表示传播链。

本质上是在说：

- 故障从注入点传播到了哪些模块

#### `impact`

表示主要影响点。

本质上是在说：

- 故障最终把哪个关键模块或关键功能打坏了

#### `recovery`

表示恢复点。

本质上是在说：

- 故障发生后，哪个模块承担了恢复/降级/兜底动作

### 5.4 它有什么用

`ground_truth.json` 是现在最重要的训练监督文件之一。

它的主要作用是：

- 支持根因分析
- 支持模块级责任定位
- 支持传播链定位
- 支持恢复链定位
- 支持异常检测标签
- 支持故障类型分类

如果说 `fip_info.data` 是“最小标签入口”，那么：

- `ground_truth.json` 就是“完整训练监督入口”


## 6. `logs/round_1/nuttx/qemu_console.log` 是什么

文件例子：

- [round_1/nuttx/qemu_console.log](/home/nanye/fault/data/dataset/NuttX/Test_1/logs/round_1/nuttx/qemu_console.log)

### 6.1 它是什么

这是最核心的日志文件。

它记录的是：

- QEMU 里运行的 NuttX 串口输出

里面既有：

- NSH 命令执行痕迹
- 系统基础输出
- `faultlog` 生成的故障阶段日志

### 6.2 里面通常包含什么

当前一般会看到：

1. NuttX 启动信息
2. `uname -a`
3. `uptime`
4. `ps`
5. `faultlog boot`
6. `faultlog sample`
7. `faultlog <具体故障模式>`
8. `faultlog recovery`

然后对应输出：

- baseline
- trigger
- propagation
- impact
- recovery

### 6.3 它有什么用

这是后续模型最主要的输入日志。

它主要用于：

- 日志窗口切分
- 向量化
- 检索
- 模型训练输入
- 异常检测
- 模块级诊断
- 根因分析

可以说，后面很多任务真正“吃进去”的文本，主要就来自这个文件。


## 7. `logs/round_1/faults/fault_events.log` 是什么

文件例子：

- [round_1/faults/fault_events.log](/home/nanye/fault/data/dataset/NuttX/Test_1/logs/round_1/faults/fault_events.log)

示例内容大概是：

- `run_start`
- `step_start`
- `step_end`
- `run_end`

### 7.1 它是什么

这是故障编排器输出的结构化事件日志。

它记录的不是 NuttX 内部业务日志，而是“外层这轮故障运行是怎么执行的”。

### 7.2 它有什么用

主要作用是：

- 给故障运行提供时间边界
- 记录故障步骤
- 方便调试联合运行链
- 辅助后续做故障-监控对齐

它不是训练主文本，但它对样本时序理解有帮助。


## 8. `logs/round_1/monitor/system_metrics.log` 是什么

文件例子：

- [round_1/monitor/system_metrics.log](/home/nanye/fault/data/dataset/NuttX/Test_1/logs/round_1/monitor/system_metrics.log)

### 8.1 它是什么

这是宿主机监控日志。

它监控的是：

- 运行 QEMU 的 Ubuntu 主机

而不是 NuttX 内部对象本身。

### 8.2 里面一般有什么

当前导出后的简化内容通常包括：

- `cpu_percent_total`
- `memory_used_percent`
- `host`
- `session_id`

原始监控链里信息比这个更多，但导出后保留的是当前数据集中需要的部分。

### 8.3 它有什么用

它主要用于：

- 联合异常检测
- 系统层辅助诊断
- 资源侧观测
- 为“故障事件 + 系统状态”联合建模提供输入

它不是主日志，但它是辅助证据。


## 9. `logs/round_1/foreground_wl/*.out.log.bzip2.out` 是什么

文件例子：

- [round_1/foreground_wl/nuttx_qemu_workload-nuttx_task_deadlock_mutex_ab_20260522t111503z_r001.out.log.bzip2.out](/home/nanye/fault/data/dataset/NuttX/Test_1/logs/round_1/foreground_wl/nuttx_qemu_workload-nuttx_task_deadlock_mutex_ab_20260522t111503z_r001.out.log.bzip2.out)

### 9.1 它是什么

这是 workload 导出标记文件。

当前这版数据里，它的内容比较轻，通常类似：

- `exported_from_joint_session`

### 9.2 它有什么用

当前主要作用是：

- 保持数据目录结构兼容
- 让下游知道这轮对应了一次 workload 输出

### 9.3 它的重要性

现阶段它的重要性不高。

对训练和主诊断任务来说，它不是核心输入。


## 10. `round_2` 里的这些文件是什么

`round_2` 和 `round_1` 的结构完全对应，但语义不同。

### 10.1 `round_2/nuttx/qemu_console.log`

它表示正常/基线轮下的 NuttX 串口日志。

它的作用是：

- 给故障轮提供对照
- 用于正常/异常区分
- 用于异常检测训练

### 10.2 `round_2/faults/fault_events.log`

在基线轮里，这个文件通常不会记录完整故障步骤，而是简单记录：

- `baseline_run`

它的作用是标识：

- 这一轮是正常对照轮，不是故障注入轮

### 10.3 `round_2/monitor/system_metrics.log`

它记录的是基线轮期间宿主机监控数据。

作用是：

- 与故障轮的监控表现做对照

### 10.4 `round_2/foreground_wl/*.out.log.bzip2.out`

它和故障轮那个 workload 文件一样，主要是结构兼容和导出标记。


## 11. 如果从“训练输入”和“监督标签”角度看，一个 Test 可以怎么理解

可以把一个 `Test` 分成两块：

### 11.1 模型输入侧

主要是这些：

- `logs/round_1/nuttx/qemu_console.log`
- `logs/round_2/nuttx/qemu_console.log`
- `logs/round_1/monitor/system_metrics.log`
- `logs/round_2/monitor/system_metrics.log`
- 少量用到：
- `fault_events.log`

其中最主要的，还是：

- `qemu_console.log`

### 11.2 监督标签侧

主要是这些：

- `fip_info.data`
- `ground_truth.json`

其中更完整、更适合训练的是：

- `ground_truth.json`


## 12. 每个部分分别更适合哪些任务

### 12.1 故障类型分类

主要用：

- 输入：`qemu_console.log`
- 标签：`fault_type`

### 12.2 模块级责任定位

主要用：

- 输入：`qemu_console.log`
- 标签：
- `TARGET_COMPONENT`
- `injection`
- `propagation`
- `impact`
- `recovery`

### 12.3 根因分析

主要用：

- 输入：`qemu_console.log`
- 标签：
- `root_cause_text`
- `root_cause_mechanism`
- `root_cause_evidence`

### 12.4 异常检测

主要用：

- 输入：
- `round_1`
- `round_2`
- 标签：
- `sample_class`

### 12.5 日志窗口 / 检索 / 向量化

主要用：

- 输入：`qemu_console.log`
- 后续切成窗口，进入 `vector_store`


## 13. 总结

如果用一句话概括一个 `Test`：

- `fip_info.data`：最小标签入口
- `ground_truth.json`：完整监督答案
- `round_1`：故障轮日志
- `round_2`：正常对照轮日志
- `qemu_console.log`：最主要的模型输入日志
- `fault_events.log`：故障运行时序记录
- `system_metrics.log`：宿主机资源观测
- `foreground_wl/*`：兼容性 workload 导出标记

如果后面只关心训练价值，优先关注：

1. `ground_truth.json`
2. `logs/round_1/nuttx/qemu_console.log`
3. `logs/round_2/nuttx/qemu_console.log`
4. `fip_info.data`

