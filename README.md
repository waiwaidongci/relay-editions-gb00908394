# 海外器乐巡演场馆场务晨报补发

面向本地演练的 Python API，使用 IANA 时区、夏令时歧义消解与缺报补发。业务场景、字段、算法边界和演练剧本见 [BUSINESS.md](BUSINESS.md)。

## 启动

```sh
cp -n .env.example .env
docker compose up --build -d --wait
curl --fail http://127.0.0.1:19339/health
```

API 使用本机端口 `19339`，MySQL 8.0 使用本机端口 `17839`。`/openapi.json` 提供接口描述。所有演示密码为 `Local-demo-2026!`，设置 `SEED_DEMO=false` 后可关闭演示初始化。演示配置用于本地环境。

```sh
curl --fail -X POST http://127.0.0.1:19339/api/auth/login \
  -H 'Content-Type: application/json' \
  --data '{"email":"analyst@demo.local","password":"Local-demo-2026!"}'
```

后续请求携带 `Authorization: Bearer <access_token>`。

| 角色 | 可执行动作 |
|---|---|
| `viewer@demo.local` | 查看版本与分析、读取批准报告及本人收件箱 |
| `analyst@demo.local` | 查看数据与报告、导入快照、发起运算 |
| `reviewer@demo.local` | 查看数据与报告、给出复核、安排投递、查看审计 |

## 业务 API

| 操作 | 路径与输入 |
|---|---|
| 获取领域字段和配置 | `GET /api/method` 返回数据行和选项的 JSON Schema |
| 导入及核对 | `POST /api/datasets` 接受 `{"name":"…","rows":[…]}`，或用 `Content-Type: text/csv` 和 `?name=…` 传原始 CSV。`GET /api/datasets` 列版本，`GET /api/datasets/{id}/rows` 分页读原始行 |
| 运算 | `POST /api/datasets/{id}/runs` 提交 `{"options":{}}` 使用默认值，`GET /api/runs/{run_id}` 轮询结果 |
| 回执台账 | `POST /api/ledger/batches` 提交 `{"posted_at":"…(可选)","entries":[{"edition_key":"…","kind":"receipt|withdrawal","received_at":"…","source_ref":"…"}]}`；同入账时刻内容冲突整批拒绝（409）并留档。`GET /api/ledger/replay?as_of=…` 按入账时间重建当时知识库，`GET /api/ledger/editions/{edition_key}/history` 查单场轨迹 |
| 复核 | `POST /api/runs/{run_id}/review` 提交 `{"decision":"approved","note":"已核对来源与口径"}`，决策也可为 `rejected` |
| 报告和分发 | `GET /api/runs/{run_id}/report` 获取带版本校验值的报告，`GET /api/runs/{run_id}/report/csv?table=…` 导出业务表。审批后 `POST /api/runs/{run_id}/deliveries` 提交 `recipient` 与带偏移的 `due_at`，由 worker 到点写入 `GET /api/inbox`；`GET /api/audit` 查询审计 |

数据集按同名及规范化内容校验值幂等导入，新增内容形成新版本。任务和定时投递存于数据库，worker 用 Redis 唤醒并定期轮询。收件箱属于本地账号，所有授权登录账号共享分析台账，审批后报告可读取。时刻保存为 UTC，API 以 ISO 时间返回。初始表结构随 API 启动创建，数据保留在命名卷；升级已有结构需要独立迁移。

```sh
docker compose exec -T backend python -m scripts.seed_demo
docker compose down
```

验收脚本与实测证据见 [本项目验收记录](../../test-artifacts/gb-0090/TESTING.md)。

该项目按场次发布时使用 `POST /api/runs/{run_id}/editions`，请求为 `{"edition_key":"NY-HALL/2026-11-01/matinee"}`。回放时间通过 `options.as_of` 指定。实时投递同时检查当前时钟和补发截止。历史 seed 用于复算夏令时及缺报场景。实时录入和补发验证见业务剧本。

## 领域规则测试

双时态台账的核心规则不依赖 Web/数据库栈，可直接用标准库运行：

```sh
cd backend
PYTHONPATH=. python3 -m unittest discover -s tests
```

覆盖：倒填收到时间与按入账时间重放、撤回按缺报、同入账时刻冲突整批不入账、更正拦截排队旧晨报并补发、投递先于更正时保留证据且重跑幂等。
