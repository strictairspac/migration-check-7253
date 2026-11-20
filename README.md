# 见山 · 检验结果迁移核对站

见山质量档案组13人用源批次、字段映射、后台对账运行完成旧字段检验结果的有证据交接。

## 场景与业务
上游实验室交付旧字段JSON检验记录；过去人工改字段名、复制到新表并凭总行数判断成功。痛点：非法数值默默丢失、重复委托号覆盖、备份无法证明能恢复。本站登记原始行，字段映射规定类型和必填，Python逐块转换并保留拒绝原行，真实PG目标表一次提交。下游质量复核人下载对账凭据，管理员用JSON逻辑备份恢复本次运行目标并核验。

四业务页：源批次、字段映射、迁移执行（可取消）、差异核对（拒绝区/目标行/备份/分版补正）；管理员另有审计和回传恢复。三核心实体batches/mappings/runs，run_versions保存不可变版本（入库行/拒绝行/替换原因/对账/该版备份），target_rows保存当前目标行，version_rows保存各版目标行。全部实体按模型/schema/service/controller/routes/前端api/store/page贯穿。

## 启动与账号
```sh
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```
前端 http://localhost:18248 ，后端 http://localhost:19248/docs ，PostgreSQL localhost:17748（migrate / migratepass / migrationcheck）。结束docker compose down -v --remove-orphans会清除本地数据。

| 角色 | 账号 | 权限 / 页面 | 典型动作 |
|---|---|---|---|
| admin | admin@migration.local | 全部、审计、恢复 | 回传备份恢复目标并校验 |
| operator | operator@migration.local | 创建/编辑/执行/取消/导出 | 配置映射并迁移 |
| viewer | viewer@migration.local | 只读、下载凭据/备份 | 复核拒绝区和真实目标表 |

密码统一Migrate2026!；PBKDF2密码与8小时JWT。本地演示凭据与JWT密钥，部署前更换。

## 一天的交接剧本
1. 09:00 operator签入，查看见山日班检验结果12条正常记录。
2. 09:05 登记lab-results-20261117.json，委托号JS-20261117-001结果420.1MPa。
3. 09:10 新建shift-20261117批次，复制旧字段。
4. 09:15 字段映射：委托号→sample_code、检测项→test_name、结果→result_value、单位→unit、时间→measured_at，每块4行。
5. 09:20 提交shift-20261117对账，或运行旧字段异常样本查看重复/非法数字/空值/缺列拒绝。
6. 09:25 查看接受/拒绝与分块证据，读取真实PG目标表。
7. 09:30 下载reconciliation-<id>.json与backup-<id>-v1.json，viewer复核。
8. 09:35 复核发现第3行结果值录入错，在结果页补正该行；同键更早的合法行若因此成立会替掉原入库行，替换原因写入第2版。
9. 10:00 admin回传该版本备份，只恢复对应版本并核对摘要，查看审计完成事件。

seed含三账号、三批次（三种正常/异常/空输入）、三映射。固定数值、自然键upsert式补缺，不覆盖用户编辑；连续执行不新增。

## 迁移、校验、取消与恢复
字段类型为text/decimal/datetime。decimal使用Decimal精确转换为6位小数字符串，范围±1e9；无穷/NaN拒绝。空值/空串按required隔离（只有unit可非必填）；缺源字段隔离。时间统一UTC，无时区按Asia/Shanghai(+08:00)解释。自然键sample_code+test_name按规范JSON SHA-256计算；同次运行重复键只接受首条合法记录。每行与全部接受行用排序key/紧凑JSON/UTF-8算SHA-256。拒绝行保存原行和原因。

## 拒收行按行号补正与分版
差异核对页把每次运行分成版本：第1版为后台迁移的初版入库，之后每次接受的补正各追加一版，版本标签可逐版查看入库行、拒绝区、替换原因、对账摘要与该版备份。

- 补正按1-based源行号替换该行完整原行，只影响被指到的那一行；其余源行保持冻结快照不变。补正行仍要过字段映射、必填、类型、自然键与列数校验，非法则整单拒绝。
- 同键仍取**最早合法行**：若补正后某键首次合法，即使更早版本已把同键的后面一行入库，也会用补正行替掉原入库行（替换原因记为 displaced/replaced_by），而不是让旧行留住自然键。
- 补正发布在一个事务里同时写入新版本目标行、对账记录（接受/拒绝/替换原因）与该版逻辑备份；任一步失败整体回滚，旧版仍是当前版。
- 两单并发：运行行锁串行化检查+计算+写入。后单基于旧版本时，若它影响的委托号+检测项与已发布版本不重叠，则合并生效为新版本；若争到同一检测项或同一源行，返回409要求按最新版本重新核对。同一补正单号（request_id）重试只返回已发布版本，不重复发布。
- 恢复只影响备份对应版本：恢复旧版只重写该版目标行；只有恢复当前版才同步运行当前目标表。备份按run_version绑定，跨版本或篡改均拒绝。旧数据库里没有版本记录的历史完成运行会在首次查看时以现有目标行补登为第1版。

最多2000源行、每行20列、单元格500字符；每块1-500行（补正重算按500行分块，仅用于证据，不影响结果）。逐块更新进度；取消在块间检查，最终提交前锁定run再次检查，成功目标一次提交；取消/异常没有部分目标数据。后台任务使用简单Redis单消费者；进程崩溃可能留下running状态，需人工复核并新建任务，不自动重试。

下载备份为本工具某版本目标行的逻辑JSON快照(version2)，含run_id/run_version/rows/origins/row_hashes/checksum（仍接受旧版version1备份），不是pg_dump或物理备份，不含WAL/索引/权限。管理员恢复只允许该运行该版本已保存checksum完全匹配的备份；先核验逐行/总体摘要、schema、自然键和版本，再事务性替换该版本目标行，回读重新计算摘要并保存recovery记录。篡改、错运行、错版本、未完成运行拒绝，恢复事务回滚；不能用任意新数据覆盖目标。

受控场景演示旧字段到新目标结构的逻辑迁移，数据库真实保存源/映射/目标/对账。不能连接远端数据库，不宣称生产异构迁移或灾备能力。

## 工程与开发
Python3.11/FastAPI/SQLAlchemy/psycopg/Redis/PostgreSQL16，原生HTML/CSS/JS，nginx1.27。exact五服务frontend/backend/worker/db/redis均healthcheck，数据库/Redis/共享产物命名卷；依赖健康后启动。请求追踪、全局错误、JWT授权和业务审计横切。

backend/src按models/schemas/services/controllers/routes/middlewares/config/constants/utils；backend/scripts/seed_demo.py；backend/migrations记录元数据版本。frontend/src按api/stores/pages/components/common/hooks/router/types/constants/utils；共享table/status/feedback；forms/poll hooks。角色/状态/类型枚举在backend/src/constants/enums.py与frontend/src/constants/enums.js。墨绿邮政凭单视觉，Noto Sans SC/Space Mono双字体来自Google Fonts，离线回退；桌面/平板/手机响应。

.env.example定义COMPOSE_PROJECT_NAME/三端口/JWT_SECRET/SEED_DEMO；DATABASE_URL/REDIS_URL由Compose注入。启动SEED_DEMO失败只记日志。本地开发pip install -r backend/requirements.txt，设数据库/Redis/JWT环境变量，在backend运行uvicorn src.main:app --reload；前端需/api代理，不能file://。测试/截图/日志放批次test-artifacts/gb-4109，源码不混入。补正/分版回归测试在backend/tests：`cd backend && DATABASE_URL=sqlite:////tmp/x.db JWT_SECRET=test python -m pytest tests -q`（使用桩队列，无需Docker）。MIT License。
