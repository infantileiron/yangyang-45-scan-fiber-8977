# 墨桥动画·扫描纸纹返工关联

核对原画飞白和返扫负担的有序关系，分台比较同向与反向比例。

## 模拟场景

[业务剧本](yangyang-business.md)记录团队、上游资料、计算口径和交接去向。业务模块包括动画帧扫描记录、纸纹等级与返扫等级、并列尺度诊断。

观测以 CSV 导入或逐行登记，按交接单号和更正版本接续。后台按提交时的观测快照运行 Python 算法；负责人签核后，使用方可查看结果、下载 CSV 或按计划接收报告。

## 更正版本与分析工作台

- 每条观测带「更正版本」，首次登记为 1。同一交接单号按版本号接续，只有填补了前一版的最高连续版本计入统计；缺前一版时先挂起。
- 重复导入相同内容不新增观测；同一交接单号同一版本内容冲突时，保留该单已生效的版本，本批其他单照常入账。
- 导入新更正后，后台按原台和新台样本重算同向单对、反向单对和等级并列单对，分析工作台一次性切换两台结果。
- 重算失败时保留旧版工作台并标记待重建，下一次成功导入时整台重建。
- 已完成的历史分析仍按提交时固化的观测快照可查，不受后续更正影响。
- 旧演示卷启动时会补加 `ticket`、`revision`、`state` 三列，历史观测作为各自单据的版本 1。

## 启动

```sh
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```

浏览器打开 `http://127.0.0.1:18424`。API 健康检查位于 `http://127.0.0.1:19424/api/health`，数据库本地端口 `17924`。停机用 `docker compose down`。只有要重做演示数据时才加 `-v` 清除命名卷。

## 演示账号

| 角色 | 邮箱 | 演示密码 | 可执行动作 |
|---|---|---|---|
| 业务分析员 | `analyst@demo.local` | `Analyst-2026!` | 登记或导入样本、提交分析、下载内部结果 |
| 复核负责人 | `supervisor@demo.local` | `Supervisor-2026!` | 导入资料、签核或退回、安排或停止分发、查审计 |
| 只读查看 | `viewer@demo.local` | `Viewer-2026!` | 查看台账、已签核结果和本地收件箱 |

`.env` 以 `.env.example` 为模板。`FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT` 可做本机端口调整。Docker 私有子网可通过 `NETWORK_SUBNET` 覆盖。`JWT_SECRET` 和 `DB_PASSWORD` 为本地演示配置。`SEED_DEMO=false` 可跳过自动种子。所有公开端口绑定在回环地址。首次执行种子后，后续种子只填缺失观测，不覆盖已录入数据。

## 模块和本地开发

| 服务 | 技术 | 用途 |
|---|---|---|
| frontend | HTML、CSS、ES Modules、Nginx | 工作台、录入与报告 |
| backend | Python 3.12、FastAPI、SQLAlchemy | 校验、快照、业务 API 和 JWT |
| worker | Python 3.12、Redis | 计算排队、心跳和到时导出 |
| db | PostgreSQL 16 | 观测、快照、签核、分发计划和审计 |
| redis | Redis 7 | 唤醒、心跳和登录限流 |

在 `backend` 中可用 `pip install -r requirements.txt` 安装本地依赖，设置 `DATABASE_URL`、`REDIS_URL` 和可写 `ARTIFACT_ROOT` 后运行 `uvicorn src.main:app --port 8000`，计算进程运行 `python -m src.worker`。前端修改在 `frontend/src`，用 `docker compose up -d --build frontend` 通过 Nginx 检查同源代理。业务输入定义在 `backend/src/domain/spec.json`，算法在同目录的 `analysis.py`。角色、任务状态和签核状态在后端 `backend/src/constants/enums.py` 和前端 `frontend/src/constants/enums.js` 共同定义。数据库表在 `backend/src/models/workflow.py`。

分发计划锁定特定已签核快照，按北京时间显示。收件箱可下载真实 CSV，当前收件组是本地交接标签。停机跨过多个周期时补送最早未投递的一期，然后追到下一个将来的周期。新快照需重新签核并建新计划。

许可证为 [MIT](LICENSE)。
