# 第二阶段：LLM 语义编译器与"无状态瞬时生灭"高并发看板开发 (Week 3-4)

## 一、阶段概述（v3.0 语义编译版）

本阶段的核心目标：建立人机交互的核心逻辑，让系统能够"看懂"业务并"自动思考"，同时落地无状态容器生灭机制。

**与 v2 的本质区别**：

| 维度 | v2（蓝图蹦出版） | v3（语义编译版） |
|------|-----------------|-----------------|
| 大模型角色 | 蓝图生成器（静态规则） | 业务逻辑编译器（动态 DAG） |
| 输出格式 | 静态 AuditBlueprint JSON | 动态 DAG JSON（含算子拓扑） |
| 架构理念 | 有状态（内存缓存蓝图） | 去状态化（Run Snapshot + 瞬时生灭） |
| 执行模型 | 单一执行 | 无锁并发 + 时序解耦 |
| 并发处理 | 无 | JIT Ephemeral Container（即用即建、完工即毁） |
| 版本管理 | 无 | Run_ID 版本树（v1/v2/v3 平行展现） |
| 输入处理 | 单张 Excel | 混沌输入（ZIP/嵌套文件夹/散落多源） |
| 前端 | Streamlit 基础 | Streamlit + Run 版本树面板 + 质控按钮 |

---

## 二、代码文件清单

```
phase2_v3/
├── core/                               # 核心引擎
│   ├── run_snapshot.py                 # Run Snapshot 管理器（Run_ID + 版本树 + SQLite）
│   ├── chaos_input.py                  # 混沌输入处理（ZIP解压 + 路径扁平化 + Data Catalog）
│   └── dag_compiler.py                 # DAG 语义编译器（算子拓扑 + Schema校验 + 预设按钮）
├── engine/                             # 执行引擎
│   ├── sandbox_v3.py                   # 瞬时生灭沙箱（JIT Ephemeral + 生命周期钩子）
│   └── delivery_engine.py              # 成果物交付引擎（Named Ranges + 三校验 + docxtpl）
├── config/                             # 配置
│   └── fallback_prompts.py             # 降级模式固化 Prompt 模版库
├── api/                                # API 网关
│   └── routes.py                       # 异步生灭路由网关（Run_ID 分发 + 生命周期钩子）
├── frontend/                           # 前端 (Vue 3 SPA)
│   └── src/                            # Vue 组件、路由、API 封装
├── dify/                               # Dify 配置
│   ├── Dify配置指南_语义编译版.md       # 语义编译器工作流配置（DAG JSON 输出）
│   └── preset_prompts.md               # 预设按钮模式 System Prompt（医保/银行/大额三场景）
└── README.md                           # 本文档
```

---

## 三、核心链路（严格遵循白皮书 §2 拓扑图）

```
审计师终端 (Streamlit localhost:8501)
    │ 交互A: 自由大白话指令框 / 交互B: 质控专家预设按钮
    ▼
[ 平台后端网关 (FastAPI localhost:8000) ] —— 毫秒级分发唯一 Run_ID
    │
    ▼ (1. 接收混沌组合: ZIP/嵌套文件夹/散落Excel)
[ 混沌输入解压与路径扁平化模块 ] —— 全局唯一哈希 → 只读锁定
    │
    ▼ (2. 发送"符号化表头元数据" + "人类审计意图")
================================================================================
★ 【核心大模型启动中枢 —— LLM 语义逻辑编译与动态 DAG 构建阶段】
[ Dify 工作流编排中枢 (本地 localhost:5001) ]
    │
    ├─► 【自由指令】→ 拒绝关键字匹配！通读全量上下文，现场推演审计数理逻辑
    ├─► 【预设按钮】→ 强行注入事务所固化的标准 Prompt 模版与中注协风险红线
    │
    ▼ (3. 组合高维上下文，通过 SSH 隧道调用私有 API)
[ AutoDL 弹性 GPU 算力云 ] → 运行 vLLM 私有大模型 → 输出"动态执行蓝图 DAG JSON"
================================================================================
    │
    ▼ (4. 网页端主动"蹦出"DAG 蓝图，人类阅卷并点击"确认执行")
[ 审计师终端 (Web UI) ]
    │
    ▼ (5. 异步投递至无状态线程队列 —— 触发宿主机 Docker 沙箱集群)
[ Linux Kernel 级别物理隔离沙箱集群 ]
    ├─► 容器 A (Run_v1) → 挂载只读资产 → 运行 a 指令 → 爆算结束 → 【立即物理销毁】
    │                                                                      │ (中间休息/时序解耦)
    │                                                                      ▼
    └─► 容器 B (Run_v2) → 挂载只读 A 文件 → 运行 b 指令 → 独立干净内存 → 【立即物理销毁】
    │
    ▼ (6. 数据同源分拣与底稿生成引擎)
[ 平台成果物构建与持久化中心 ]
    ├─► Excel 引擎 (xlwings + Named Ranges) —— 动态扩展填充标准工作底稿
    ├─► Word 引擎 (docxtpl) —— 动态渲染财务报表附注与审计说明
    └─► 中间件桥接 (.csv/.xml) —— 对接鼎信诺/审计大师
    │
    ▼
================================================================================
★ 【终态交付网关 —— 成果物管理与下载引擎】
[ 成果物版本控制中心 (Run Snapshot Manager) ]
    ├─► 依托 Run_ID 实现多阶段作业树状并存，彻底防覆盖、防污染
    └─► 提供全套打包下载 (Excel底稿 + Word说明 + 异常明细CSV + 审计溯源日志)
================================================================================
```

---

## 四、三大核心引擎详解

### 4.1 混沌输入处理引擎（chaos_input.py）

```
任意输入（ZIP/RAR/7z/文件夹/单文件）
    ↓
递归解压 → 临时工作区
    ↓
路径扁平化（多级目录 → 单层，冲突消解）
    ↓
静默嗅探（前1000行 → 表头Schema提取）
    ↓
全局唯一哈希（SHA256(文件列表 + MD5 + 时间戳)）
    ↓
只读锁定（chmod -R 555）
    ↓
Data Catalog JSON（文件名 + 列名 + 类型 + 样本值，**不含原始明细**）
```

### 4.2 DAG 语义编译器（dag_compiler.py）

```
审计师大白话 + Data Catalog
    ↓
Dify 语义编译（深度理解，非关键字匹配）
    ↓
DAG JSON 蓝图：
{
  "objective": "编译后的审计目标",
  "operators": [
    {"id": "op_1", "name": "Load", "params": {...}},
    {"id": "op_2", "name": "RegexFilter", "params": {...}},
    {"id": "op_3", "name": "GroupBy", "params": {...}},
    {"id": "op_4", "name": "ConditionCheck", "params": {...}},
    {"id": "op_5", "name": "Extract", "params": {...}}
  ],
  "context": {"tolerance": ..., "noise_rules": ...},
  "risk_alerts": [...],
  "human_review_points": [...]
}
    ↓
拓扑排序验证 → Schema 校验 → 返回前端
```

**15 种标准算子**：Load, RegexFilter, ColumnFilter, GroupBy, Merge, Sort, ConditionCheck, Extract, Transform, NoiseFilter, Aggregate, Diff, Export, Reconcile, AuditAdjustment

### 4.3 瞬时生灭沙箱引擎（sandbox_v3.py）

```
Run 触发
    ↓
【诞生】docker-py 创建全新容器（Python:Alpine，readonly 根目录，--network=none）
    ↓
【执行】代码爆算 → stdout/stderr 通过 Docker 日志 API 回传
    ↓
【完成】成果物回传 + 持久化落盘（原子提交）
    ↓
【消亡】docker rm -f（强制物理销毁，零残留）
```

**四层安全枷锁**：
1. 只读根文件系统（readonly）
2. 物理断网（--network=none）
3. 资源熔断（CPU 50%，内存 2GB，120秒超时）
4. 最小化权限（cap_drop ALL + no-new-privileges）

---

## 五、与前后阶段衔接

### 第一阶段 (W1-2) 交付物 → 本阶段使用

| 交付物 | 使用方式 |
|--------|---------|
| SSH 隧道（localhost:18000 → AutoDL vLLM） | api/routes.py 通过 Dify 间接调用 |
| Dify 本地部署（localhost:5001） | 语义编译器工作流编排 |
| 文件存储规范（/data/） | run_snapshot.py RUNS_BASE |
| Docker daemon | sandbox_v3.py docker-py |

### 本阶段交付物 → 第三阶段 (W5-6) 使用

| 交付物 | 使用方式 |
|--------|---------|
| Data Catalog JSON 格式 | 脱敏网关 Middleware 输入 |
| Run Snapshot 元数据库 | 记录脱敏状态 |
| DAG JSON Schema | 联网搜索触发条件判断 |

### 本阶段交付物 → 第四阶段 (W7+) 使用

| 交付物 | 使用方式 |
|--------|---------|
| DAG 算子拓扑 | OpenClaw 编译为 Python/Pandas 代码 |
| 生命周期钩子接口 | OpenClaw 自纠错循环集成 |
| Named Ranges 映射引擎 | 底稿精准写入 |
| 三校验强制关卡 | 阻断交付 → AI 修正 |

---

## 六、快速启动

```bash
# 1. 安装依赖
pip install fastapi uvicorn httpx aiosqlite pandas openpyxl xlrd
pip install docker RestrictedPython
pip install streamlit requests

# 2. 建立 SSH 隧道
ssh -N -L 0.0.0.0:30000:localhost:8000 root@connect.westb.seetacloud.com -p 25922 -o ServerAliveInterval=60 -o ServerAliveCountMax=3
# 说明：本地端口 30000（0.0.0.0 绑定使 Dify 容器可经 host.docker.internal 访问；有局域网暴露风险，建议防火墙限制）
# 切换端口后需同步环境变量：
#   VLLM_TUNNEL_URL=http://localhost:30000/v1/chat/completions（后端/core 模块）
#   VLLM_API_BASE=http://localhost:30000/v1（评测/验证脚本）
# 以及 Dify 模型供应商的 API Base URL（host.docker.internal:30000/v1）

# 3. 配置 Dify 模型供应商（OpenAI-API-compatible → http://host.docker.internal:30000/v1）
# 按照 dify/Dify配置指南_语义编译版.md 配置

# 4. 启动后端
cd phase2_v3
python -c "from api.routes import create_app; import uvicorn; uvicorn.run(create_app(), host='0.0.0.0', port=8000)"

# 5. 启动前端 (Vue 3)
cd phase2_v3\frontend
npm run dev          # 开发模式，localhost:3000 → proxy → localhost:8000
```

---

## 七、降级模式

当 Dify 不可用时，系统自动激活降级模式：

| 能力 | 正常模式（Dify 可用） | 降级模式（Dify 不可用） |
|------|---------------------|----------------------|
| 语义编译 | 自由编译 DAG | 固定算子模板（场景预设） |
| 历史库检索 | ✅ Dify 知识库 | ❌ 不可用 |
| 联网搜索 | ✅ Tavily/SearxNG | ❌ 不可用 |
| 复杂策略推演 | ✅ 多步关联推理 | ❌ 自动挂起 |
| 简单清洗/筛选 | ✅ | ✅ 确定性操作 |
| 审计合规底线 | ✅ | ✅ 强制保留 |

降级检测：`config/fallback_prompts.py` 根据 `user_intent` 自动推断场景，加载对应的固化 Prompt。

---

## 八、全免疫铁律

| 免疫维度 | 实现机制 | 代码位置 |
|---------|---------|---------|
| **空间免疫**（混沌输入） | ZIP递归解压 + 路径扁平化 + 冲突消解 | `chaos_input.py` |
| **并发免疫**（并发连击） | 同源/异源文件 → 毫秒级分流至各自的容器平行宇宙 | `run_snapshot.py` + `sandbox_v3.py` |
| **时序免疫**（先后操作） | 完工即物理销毁，每次都是全新无菌环境 | `sandbox_v3.py` + `run_snapshot.py` |

---

## 九、API 端点

| 方法 | 端点 | 功能 |
|------|------|------|
| POST | `/api/v3/runs` | 创建新 Run（混沌输入 → DAG 编译） |
| GET | `/api/v3/runs/{run_id}` | 查询 Run 详情 |
| GET | `/api/v3/runs/{run_id}/dag` | 获取 DAG 蓝图 |
| POST | `/api/v3/runs/{run_id}/execute` | 确认 DAG → 触发沙箱执行 |
| GET | `/api/v3/runs/{run_id}/status` | 查询执行状态 |
| GET | `/api/v3/projects/{code}/tree` | 获取版本树 |
| GET | `/api/v3/runs/{run_id}/download` | 异步打包下载 |
| GET | `/api/v3/download/status/{task_id}` | 查询下载状态 |


---

## 总结

### 可以连 LLM 了

代码层面全部就绪——11 个文件语法通过，所有确定性模块通过回归。

### 不需要 LLM 就能测的 6 个场景

| # | 场景 | 测试内容 |
|---|------|---------|
| 1 | **银行对账引擎** | 文件类型识别、列映射、五层匹配、红旗检测 |
| 2 | **表格归一化** | 7 种形态探测、表头自动定位、空格清洗、多级表头拍平、合计剥离 |
| 3 | **关键词词典** | 精确命中、模糊回退(环保税→税费)、命中率预览、11条目/113pattern |
| 4 | **场景路由+预设** | 对账/数据加工/大额筛查路由、预设别名归一、11场景交叉验证 |
| 5 | **反向校验+自纠错** | 未匹配摘要聚类、Layer1 缺import/输出目录自动修复 |
| 6 | **语法完整性** | 11 个修改过的文件全部 ast.parse 通过 |

### 需要 LLM 才能测的

- DAG 编译（Dify/vLLM 语义编译）
- 代码生成+沙箱执行
- 报告措辞生成
- LLM 关键词提案
- 知识问答（RAG）

这些场景的代码逻辑已经接好，只差 vLLM/Dify 服务在线即可验证。
