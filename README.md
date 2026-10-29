# 售后支持 Agent · After‑Sales Support Agent

> 一条**有状态、可审计**的售后处理流程：混合意图路由 + 带引用的政策 RAG + 确定性退款规则 + 人工审批中断恢复。
> 后端 LangGraph + FastAPI，前端 Vue 3，接 DeepSeek 与千问 embedding，**默认 mock 模式离线可跑**。

```
111 个测试 · 语句覆盖 67.5% · 检索 recall@1 0.964 · 答案有依据率 1.000
订单链路 20 并发 100 请求 0 错误 · 政策链路 p95 3.1s
```

---

## 1. 它能做什么

| 场景 | 输入 | 输出（实测） |
| --- | --- | --- |
| 政策咨询 | `退款一般多久到账？` | 分支付渠道的到账时效 + `[1][2]` 引用，引用校验不过就转人工 |
| 查订单 | `我的订单 SO202610180001 到哪了` | 商品/金额/运单/签收时间（客户只能查自己的） |
| 自助退款 | `订单 SO202610240003 不要了` | 规则判定 → 退款单 + 工单（≤¥300 自动通过） |
| 高额退款 | `订单 SO202610180001 我要退款，耳机有杂音` | 图挂起 → 审核台审批 → `Command(resume)` 恢复执行 |
| 越界问题 | `帮我写一首春天的诗` | 说明服务范围，不硬答 |
| 越权访问 | 客户拿别人的订单号 | `没有查到这笔订单`，不产生任何退款 |

**演示账号**（首次启动自动创建，密码 `demo1234`）：`alice` / `bob`（客户）、`agent01`（审核员，单笔 ≤¥5000）、`admin`（管理员）。

---

## 2. 30 秒跑起来

```bash
make install          # 建 venv + 装后端依赖 + 前端依赖
make index            # 建知识库索引（默认 hashing 向量，零外网）
make dev-backend      # :8000
make dev-frontend     # :5173  → 打开即登录页

# 可选：接真实模型
# backend/.env 里填 LLM_PROVIDER=deepseek / EMBEDDING_PROVIDER=openai（模板见 .env.example）
```

全栈容器：`docker compose up --build`（前端 :8080 / API :8000 / PostgreSQL）。

---

## 3. 架构

```
Vue 3（登录 / 对话工作台 / 审核台 / 知识库调试台）
   │  HTTP + SSE（Bearer JWT）
FastAPI  ──  鉴权依赖 · request_id · 指标 · 登录限流
   │
SupportService  ──  会话语义：chat / stream / resume / history
   │
LangGraph 主图（13 节点 / 6 条意图分支 / 2 处 interrupt）
   ├── knowledge 子图：主题闸门 → 混合检索 → 相关性闸门 → 生成 → 引用校验
   ├── order / invoice 子图：查单、发票保修问答
   ├── 退款规则引擎：R1–R7 + R9 → auto / approval / reject
   └── human_support：interrupt 等人工
   │
FAISS 索引 · SQLite（订单/退款/工单/审批/用户/会话归属）· Checkpointer（SQLite/PG）
```

> 完整图、节点职责、状态字段见 **[docs/项目讲解.md](docs/项目讲解.md)**。

---

## 4. 质量证据（不是"我觉得还行"）

| 维度 | 数字 | 怎么来的 |
| --- | --- | --- |
| 单元/接口测试 | **111 passed** | `make test`（mock 模式，不联网） |
| 语句覆盖 | **67.5%** | `make cov`（stdlib trace 自实现；policy/repository/security/classifier/service 均 100%） |
| 检索评估 | recall@1 **0.964** · recall@3 **1.000** · MRR **0.976** | `make eval`（36 条标注集，含 8 条无关问题） |
| 答案评估 | 引用命中 **1.000** · 事实覆盖 **1.000** · 有依据回答 **1.000** · 拒答准确 **1.000** · 硬答率 **0** | `make eval-answer`（26 条，跑整条 `/api/chat`） |
| 压测 | 订单链路 20 并发 × 100 请求：**0 错误**，RPS 11.8，p50 1.53s / p95 2.39s<br>政策链路 5 并发：p50 4.22s / p95 6.93s | `make loadtest` |
| CI | pytest + smoke + `vue-tsc --noEmit` + build | `.github/workflows/ci.yml` |
| 安全 | 3 个 P0 越权漏洞已复现并修复，6 条 IDOR 回归测试 | 见讲解书 §14 |

---

## 5. 六个设计决策

1. **混合意图路由**：词表打分 + 规则纠正 + LLM 兜底。执行类（"我要退款"）与咨询类（"能退货吗"）必须分开——判错会误执行。
2. **RAG 多阶段校验**：主题闸门 → 混合召回（0.6 向量 + 0.4 BM25，时效问题给表格加成）→ 阈值闸门 → 带引用生成 → 引用合法性/覆盖率校验，置信度 <0.55 直接转人工。
3. **退款判断交给代码**：8 条规则全量求值，输出结构化 `PolicyDecision`（含 `checks` / `breakdown` / `failed_rules`），模型只负责翻译成人话。
4. **中断恢复四道防线**：幂等键 `(thread_id, order_no)`、审批 CAS（`UPDATE … WHERE status='pending'`）、单事务 + 部分唯一索引、执行前 TOCTOU 复核。
5. **认证 ≠ 授权**：JWT(HS256) + RBAC（3 角色）+ ABAC（审核员单笔上限 ¥5000）+ 数据归属（订单 / 会话）。
6. **降级优先**：LLM 挂了 → 抽取式回答；embedding 挂了 → 纯 BM25；faiss 缺失 → numpy 精确检索；全程不 500。

---

## 6. 接口

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/health` | 公开 | LLM / 索引 / checkpointer / 认证方案 |
| POST | `/api/auth/login` | 公开 | 换 JWT（10 次/5 分钟限流） |
| GET | `/api/auth/me` | 登录 | 当前身份与权限点 |
| POST | `/api/chat`、`/chat/stream` | 登录 | 对话；客户只能用自己身份 |
| GET | `/api/threads/{id}` | 登录 | 历史；客户仅限自己的会话 |
| POST | `/api/threads/{id}/resume` | `reviews:decide` | 审批/人工回复；超 ¥5000 需 admin |
| GET | `/api/reviews` · `/api/tickets` | `reviews:read` / `tickets:read` | 审核队列 / 工单 |
| GET | `/api/knowledge` · `/search` · `/ingest` | `knowledge:read/search/write` | 统计 / 调试检索 / 重建（仅 admin） |
| GET | `/api/metrics` | `metrics:read`（admin） | Prometheus 文本：请求量、延迟 p50/p95/p99、转人工率 |
| GET | `/api/demo/orders` | 登录 | 客户只能看自己的订单 |

---

## 7. 目录

```
backend/
├── app/
│   ├── main.py                应用工厂 / lifespan / request_id 中间件 / 指标
│   ├── core/                  config · logging · metrics · ratelimit · context · errors
│   ├── domain/                security(JWT/RBAC) · policy(8 条规则) · repository · models
│   ├── api/                   routes · schemas · deps_auth
│   ├── graph/                 state · classifier · builder · service · checkpointer
│   ├── agents/                knowledge · order · refund · human · fallback · guard · wrappers
│   └── rag/                   chunking · embeddings · bm25 · store · retriever · ingest
├── knowledge_base/            6 篇售后政策（换成你的文档即可）
├── eval/                      36 条检索标注 + 26 条答案标注 + 基线报告
├── scripts/                   smoke · eval_retrieval · eval_answer · loadtest · coverage
├── tests/                     111 个用例
└── Dockerfile · requirements.txt

frontend/src/                  api.ts · stores(auth/chat) · views(Login/Chat/Review/Knowledge) · components
docs/                          项目讲解.md · 面试拷打题库.md（均有同名 .html）
```

---

## 8. 已知限制

- 订单/支付是 SQLite 演示数据，接真实 OMS 只需替换 `domain/repository.py`；
- 单实例演示：SQLite checkpointer 共用连接，压测显示 20 并发下 p50 从 0.53s 升到 1.53s（写锁竞争），生产应换 PostgreSQL；
- 无 refresh token / 服务端吊销（旧 token 8 小时内仍有效）；token 存 localStorage；
- 不支持多意图（一句话两件事）、知识库无文档级权限、只有阶段流式（token 级流式待做）；
- 政策文档为自建示例，规则按真实电商售后流程抽象。

---

## 9. 文档

- **[docs/项目讲解.md](docs/项目讲解.md)** — 架构、代码走读、RAG/规则/审批/认证细节、质量工程、3 个 P0 审计复盘、面试讲法
- 本地可 `make docs` 生成带目录与内联架构图的 HTML（可直接打印 PDF）

---

## 10. 参考

底座流程参考 `Amankhan1009/customer-support-agent`，RAG 校验思路参考 `amine-akrout/customer-support-agentic-rag`；
两个仓库只作对照放在 `vendor/`（已 gitignore），运行代码按本项目业务模型重写。
