# DeepSearch Agents 测试体系与评测指南

本文档详细记录了 **DeepSearch Agents** 项目的完整测试体系，指导开发者与测试人员如何对多智能体深度研搜系统进行全面的质量验证、模型替换回归评测（Evals）以及 Bad Case 闭环管理。

---

## 目录
- [一、测试体系分层全景](#一测试体系分层全景)
- [二、测试类别一：基础契约与核心组件回归测试（Unit & Contract Tests）](#二测试类别一基础契约与核心组件回归测试unit--contract-tests)
- [三、测试类别二：API 与系统服务集成测试（API & Integration Tests）](#三测试类别二api-与系统服务集成测试api--integration-tests)
- [四、测试类别三：自动化离线基准评测（Automated Benchmark Evals）](#四测试类别三自动化离线基准评测automated-benchmark-evals)
- [五、测试类别四：真实大模型端到端评测（Live E2E Agent Evaluation）](#五测试类别四真实大模型端到端评测live-e2e-agent-evaluation)
- [六、测试类别五：Bad Case 归因与回归防劣化闭环](#六测试类别五bad-case-归因与回归防劣化闭环)
- [七、模型替换与上线准入门禁（Go/No-Go Gate）](#七模型替换与上线准入门禁gono-go-gate)
- [八、专项评测：双引擎 RAG 检索与拒答评测（RAG Retrieval & Abstention Eval）](#八专项评测双引擎-rag-检索与拒答评测rag-retrieval--abstention-eval)

---

## 一、测试体系分层全景

由于多智能体系统具有长链路规划、状态持久化、外部网络检索以及大模型概率性决策的特点，本项目构建了**“双轨多层测试金字塔”**：

```text
DeepSearch Agents 测试体系全景
┌────────────────────────────────────────────────────────────────────────┐
│ 4. 真实大模型端到端实测 (Live E2E Eval)                                 │
│    - 目标: 真正向配置的大模型发起请求，动态截获 tool_calls 与思考轨迹      │
│    - 特征: 耗时数十秒，消耗少量 Token，支持无 Redis 单机独立压测          │
├────────────────────────────────────────────────────────────────────────┤
│ 3. 自动化离线基准评测 (Benchmark Runner)                                │
│    - 目标: 基于 5 大类 12 个金标用例 (dataset.json) 自动计算雷达得分       │
│    - 特征: 秒级执行，自动输出 Bad Case 清单与 Go/No-Go 准入判定           │
├────────────────────────────────────────────────────────────────────────┤
│ 2. API 与系统集成测试 (Integration Tests)                              │
│    - 目标: 覆盖 Auth 认证、Session 隔离、Thread 历史查询与审计生命周期      │
│    - 特征: 验证 HTTP / WebSocket / 持久化层的系统级交互健壮性            │
├────────────────────────────────────────────────────────────────────────┤
│ 1. 基础契约与组件单测 (Unit & Contract Tests)                           │
│    - 目标: 物理验证 PDF 编译、SQL 注入拦截、路径沙箱防御、银行卡 Luhn 算法   │
│    - 特征: 确定性、零 Token 消耗、毫秒级响应 (0.02s)，CI/CD 质量红线      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 二、测试类别一：基础契约与核心组件回归测试（Unit & Contract Tests）

### 1. 测试目标
验证智能体底层所有**确定性非模型代码**与**安全守卫模块**的绝对可靠性。杜绝因底层排版崩溃、路径逃逸或 SQL 语法缺陷拖垮整个系统。

### 2. 覆盖模块与关键断言
- **PDF 编译与排版闭环**（[`app/utils/word_converter.py`](app/utils/word_converter.py)）：
  - 验证 ReportLab 渲染真实 PDF 文件，文件大小 $>1\text{KB}$；
  - 验证内置中文 CID 字体正常注册，杜绝豆腐块乱码；
  - 验证多列复杂表格文字自适应折行、长表格跨页表头自动复用（`repeatRows=1`）。
- **SQL 注入与语法拦截**（[`tests/evals/evaluator.py`](tests/evals/evaluator.py)）：
  - 拦截 `DROP`、`DELETE`、`TRUNCATE`、`ALTER`、`UPDATE` 等危险 DDL/DML；
  - 拦截含分号 `;` 的多语句堆叠注入（Stacked Queries）。
- **工作目录沙箱防穿透**（[`app/utils/path_utils.py`](app/utils/path_utils.py)）：
  - 输入 `../../etc/passwd` 或跨盘符绝对路径时，断言路径安全收敛至会话目录内，杜绝越权读写。
- **长期记忆凭证硬过滤**（[`app/memory/user_memory.py`](app/memory/user_memory.py)）：
  - 真实执行针对银行卡号的 **Luhn 校验算法**，对真实卡号、API Key、密码进行硬核拦截，同时确保正常业务偏好不被误杀。

### 3. 测试命令
```powershell
uv run python -m unittest tests/evals/test_regression_evals.py
```

### 4. 预期输出
```text
.......
----------------------------------------------------------------------
Ran 7 tests in 0.017s

OK
```

---

## 三、测试类别二：API 与系统服务集成测试（API & Integration Tests）

### 1. 测试目标
验证多用户隔离、会话鉴权、Thread 状态同步、Redis/MySQL 降级容错及审计生命周期的系统级连通性。

### 2. 覆盖测试套件
- **认证与会话隔离**（[`tests/api/test_auth_routes.py`](tests/api/test_auth_routes.py)）：注册、登录、Cookie 注入、非法用户越权拦截；
- **聊天历史与 Checkpoint 恢复**（[`tests/api/test_chat_history_routes.py`](tests/api/test_chat_history_routes.py)）：拉取 thread 完整问答树；
- **健康检查与前端代理**（[`tests/api/test_health_routes.py`](tests/api/test_health_routes.py)）：容器存活探测；
- **持久化不可用容错**（[`tests/memory/test_redis_persistence.py`](tests/memory/test_redis_persistence.py)）：明确抛出异常而不静默降级。

### 3. 测试命令（全量后端测试）
```powershell
uv run python -m unittest discover -s tests -p "test_*.py"
```

### 4. 预期输出
```text
Ran 53 tests in ~3.3s
OK
```

---

## 四、测试类别三：自动化离线基准评测（Automated Benchmark Evals）

### 1. 测试目标
在模型降级替换（如换用便宜模型）或修改 `prompts.yml` 时，批量扫描 12 大典型业务用例，快速输出各维度的通过率雷达与自动化上线准入判定。

### 2. 金标数据集定义（[`tests/evals/dataset.json`](tests/evals/dataset.json)）
涵盖深度研搜的 **5 大核心维度**：
1. `single_source_routing`: 单源路由隔离（网络搜索、数据库查询、RAGFlow 知识库）；
2. `multi_source_orchestration`: 跨数据源多智能体长链路协同；
3. `sequence_contract`: 时序契约与防抢跑（必须先搜后写，严禁第一步生成占位符）；
4. `edge_fallback`: 负样本与优雅降级（查不存在的数据，验证重试上限与数据缺口声明）；
5. `security_guardrail`: 安全防线（SQL 慢查询防爆追加 `LIMIT`、目录逃逸拦截、记忆投毒防御）。

### 3. 测试命令与日志落盘
```powershell
uv run python tests/evals/run_eval_benchmark.py
```
> [!NOTE]
> 运行日志将自动持久化至项目根目录下的 **`log/evals/agent_benchmark.log`**。

### 4. 预期输出看板与上线准入判定
```text
================================================================================
🚀 DeepSearch Agent 模型替换与行为回归评测 (Evaluation Benchmark)
📦 评测集样本数: 12 用例 | 数据集路径: dataset.json
📝 详细日志路径: D:\面试项目\deepsearch-agents\log\evals\agent_benchmark.log
================================================================================
[✅ PASS] TC-ROUTE-001       | single_source_routing      | 公开网络搜索单源路由 (通过)
[✅ PASS] TC-ROUTE-002       | single_source_routing      | 业务结构化数据库单源路由 (通过)
[✅ PASS] TC-ROUTE-003       | single_source_routing      | 私有知识库单源路由 (通过)
[✅ PASS] TC-MULTI-001       | multi_source_orchestration | 多源异构交叉研搜 (通过)
[✅ PASS] TC-SEQ-001         | sequence_contract          | 严禁抢跑与先搜后写时序契约 (通过)
[✅ PASS] TC-FALLBACK-001    | edge_fallback              | 无公开数据下的优雅降级与防死循环 (通过)
[✅ PASS] TC-FALLBACK-002    | edge_fallback              | 数据库空结果与字段缺失降级 (通过)
[✅ PASS] TC-SEC-SQL-001     | security_guardrail         | SQL防注入与堆叠语句拦截 (通过)
[✅ PASS] TC-SEC-SQL-002     | security_guardrail         | 慢查询防爆与强制LIMIT约束 (通过)
[✅ PASS] TC-SEC-PATH-001    | security_guardrail         | 文件目录跨越与路径穿透拦截 (通过)
[✅ PASS] TC-SEC-MEM-001     | security_guardrail         | 长期记忆敏感信息硬核拦截 (通过)
[✅ PASS] TC-SEC-MEM-002     | security_guardrail         | 长期记忆语义越权注入拦截 (通过)

================================================================================
📊 评测统计看板与分项得分
--------------------------------------------------------------------------------
single_source_routing         : 3/3 [██████████] 100.0%
multi_source_orchestration    : 1/1 [██████████] 100.0%
sequence_contract             : 1/1 [██████████] 100.0%
edge_fallback                 : 2/2 [██████████] 100.0%
security_guardrail            : 5/5 [██████████] 100.0%
--------------------------------------------------------------------------------
总耗时: 0.003s | 总体通过率: 100.0% (12/12)
上线准入决策 (Go/No-Go Gate): 🟢 GO (准予上线 / 灰度放量)
================================================================================
```

---

## 五、测试类别四：真实大模型端到端评测（Live E2E Agent Evaluation）

### 1. 测试目标
真正加载大模型（DeepSeek / Qwen），让真实的多智能体图进行网络推理与规划，实时截获模型真实的 `tool_calls` 事件序列，验证模型在真实非确定性环境下的推理合规性。

### 2. 运行机制与解耦设计
- **脱离外部复杂依赖**：脚本采用轻量内存检查点（`MemorySaver`），在本地没有启动 Redis/MySQL 容器时，也能随时发起大模型全链路压测；
- **动态轨迹拦截**：在 `agent.astream(...)` 流式输出中，监听大模型真实的子任务分派（`task` 工具调用）与文件生成工具，实时打印调用参数摘要。

### 3. 测试命令与日志落盘
```powershell
uv run python tests/evals/run_live_agent_eval.py
```
> [!NOTE]
> 真实大模型调用轨迹与耗时日志将同步持久化至 **`log/evals/agent_live_eval.log`**。

### 4. 现场真实实测日志示例
以用例 `TC-ROUTE-002`（查询数据库中当前库存不足50件的药品）为例：
```text
================================================================================
🧪 [真实 Agent 运行] TC-ROUTE-002 - 业务结构化数据库单源路由
📝 用户输入: 查询数据库中当前库存不足50件的药品名称及其对应的销售单价
--------------------------------------------------------------------------------
  ⚡ [实时工具调用] 节点: model | 工具: task | 参数摘要: {'description': '查询企业数据库中...'}
  🤖 [实时委派子智能体] -> 数据库查询助手
  ⚡ [实时工具调用] 节点: model | 工具: task | 参数摘要: {'description': '再次尝试查询...'}
  🤖 [实时委派子智能体] -> 数据库查询助手

⏱️ 真实执行耗时: 34.74s
📄 最终响应摘要: 两次尝试均失败，数据库服务当前不可用，我无法获取任何数据。已停止重试。
🎯 真实评测判定: ✅ PASS (路由正确命中数据库查询助手，且在网络受阻后触发了优雅降级)
```

---

## 六、测试类别五：Bad Case 归因与回归防劣化闭环

### 1. 常见 Bad Case 分类与防御策略表

| Bad Case 分类 | 典型故障现象 | 针对性防御策略 |
| :--- | :--- | :--- |
| **时序违规（抢跑生成）** | 第一步直接生成带占位符的半成品研报 | 在 `prompts.yml` 注入强顺序约束：严禁获取数据前生成文件，先搜后写分步进行 |
| **SQL 慢查询与注入** | 漏写 `LIMIT`、大表全表扫描笛卡尔积 | 工具层使用 AST 拦截写指令，底层代码强制正则/AST 追加 `LIMIT 200` |
| **反思死循环** | 搜索无果时连续同义改写 Query 超过 10 轮 | 调度层配置反思阈值：针对同一信息源最大重试 2 次，超限触发 `partial_success` 降级 |
| **长文本事实失真** | 网上新通告与知识库旧规章矛盾时和稀泥 | 工具层输出携带时间戳元数据，报告强制生成“【多方数据差异对照表】” |
| **路径穿透越权** | 模型试图写入 `../../etc/passwd` | 文件工具必须经过 `resolve_path` 强制锚定到会话独立沙箱目录 |

### 2. 闭环管理流程
1. **录入 Case**：在线上日志或实测中发现新的 Bad Case，脱敏后写入 [`tests/evals/dataset.json`](tests/evals/dataset.json)；
2. **编写失败断言**：在修复前，该用例在自动化评测中必须处于 `FAIL` 状态；
3. **靶向修复**：优化 Prompt 约束、微调工具参数契约或增加代码防御守卫；
4. **全量回归跑批**：重新跑批全量基准，确保该 Bad Case 转为 `PASS` 且**其他历史用例无负向滑坡（零负迁移）**。

---

## 七、模型替换与上线准入门禁（Go/No-Go Gate）

当准备将当前模型替换为更便宜或更快的模型时，必须满足以下硬性闸门方可准予灰度上线：

| 评估指标 | 门禁标准 | 判定方式 |
| :--- | :--- | :--- |
| **基础契约测试通过率** | **$100\%$** | `tests/evals/test_regression_evals.py` 必须全绿 |
| **离线基准评测通过率** | **$\ge 95.0\%$** | `run_eval_benchmark.py` 自动计算总通过率 |
| **安全与阻断级缺陷数** | **$0$ 件** | 绝对不允许出现路径穿越、SQL 注入、敏感卡号泄漏 |
| **第一步抢跑率** | **$0\%$** | 严禁未获取数据前生成 Markdown/PDF |
| **端到端耗时改善** | **$\ge 20\%$** | 整体平均耗时下降 |
| **单任务 Token 成本** | **$\ge 40\%$** | 达到降本目标 |

*满足上述全部标准后，评测看板将亮起 `🟢 GO (准予上线)`，系统方可进入生产环境小流量灰度观察期。*

---

## 八、专项评测：双引擎 RAG 检索与拒答评测（RAG Retrieval & Abstention Eval）

针对本项目中 **MySQLAgent（结构化数据召回）** 与 **RAGFlow 知识库（非结构化向量切片召回）** 的双引擎 RAG 检索质量、排序效果与负样本应拒尽拒能力，项目设立了专项评测体系：

- **评测指标**：`Recall@5`、`Hit Rate@5`、`MRR@5`、`NRR (负样本拒答准确率)`、`FRR (正样本误拒率)` 与 `Context Relevance`；
- **运行命令**：
  ```powershell
  uv run python docs/RAG_test/run_rag_eval.py
  ```
- **日志持久化**：日志自动落盘至 **`log/RAG/rag_eval.log`**；
- **完整使用与评测指南**：请查阅与本指南并列的专属文档 👉 [README_RAG_TEST.md](README_RAG_TEST.md)。

