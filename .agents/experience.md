# Experience Log

> 记录每次踩坑的完整链路：Problem -> Root Cause -> Solution -> Rule。
> 格式：`## N. 标题（一句话说清问题）`，然后记录 **Problem** / **Root Cause** / **Solution** / **Rule**。

## 1. 沙箱账户触发 Git dubious ownership

**Problem**：在仓库内执行 `git status` 被 Git 拒绝。

**Root Cause**：仓库属于桌面用户，执行命令的是受限沙箱账户。

**Solution**：对只读 Git 命令使用单次 `git -c safe.directory='<repo>' ...`。

**Rule**：不得为了消除该提示修改用户全局 Git 配置；只在必要命令内局部放行。
## 2. 用户 npm registry 与执行网络均不可达

**Problem**：`npm install` 长时间无输出，前端依赖无法安装。

**Root Cause**：用户 npm 配置指向 `registry.npm.taobao.org` 且连接被拒；单次切换官方 `registry.npmjs.org` 后 `npm ping` 同样 `ECONNREFUSED`，确认是当前执行环境网络而非依赖声明。

**Solution**：不修改全局 npm 配置；终止有限重试，保留 JSON 校验，将前端 lint/test/build 标记为 `SKIP_ENVIRONMENT`。

**Rule**：依赖安装无输出时用显式 registry 和 `npm ping --loglevel verbose` 区分用户配置、网络与项目依赖问题；重试必须有限。
## 3. mypy strict 拒绝 SQLAlchemy 2.0 async 的 rowcount

**Problem**：`session.execute(update(...))` 后访问 `.rowcount` 报 `Result[Any] has no attribute "rowcount"`。

**Root Cause**：SQLAlchemy 2.0 async 的 `execute` 对 DML 语句类型推断为 `Result[Any]`，而 `rowcount` 只在 `CursorResult` 上，strict 模式因此拒绝。

**Solution**：乐观锁 UPDATE 改用 `.returning(Job.id)` + `scalar_one_or_none()` 判断是否命中，类型干净且能区分「未命中」与「命中」；SQLite 3.35+ 支持 UPDATE…RETURNING。

**Rule**：用乐观锁 UPDATE 判断抢占是否成功时，优先 `.returning(主键)` + `scalar_one_or_none()`，不要依赖 `rowcount`。
## 4. 租约推进与恢复必须原子 CAS，不能先读后写

**Problem**：审查发现 `advance_job` 未校验租约、`recover_stale_jobs` 先 SELECT 后无条件 UPDATE，导致旧 Worker 能推进状态、恢复器覆盖新 Worker 的租约。

**Root Cause**：把「状态/租约」当成可先读后改的普通字段，忽略了 SELECT 与 UPDATE 之间的并发窗口；ORM 的 dirty-check 提交按主键无条件覆盖。

**Solution**：`advance_job` 用单条 `UPDATE ... WHERE lease_owner=:owner AND lease_expires_at > :now AND status=:old` 做 CAS，行数 0 即拒绝；`recover_stale_jobs` 用带 `lease_expires_at <= :now` 条件的原子 UPDATE 并返回受影响 id。

**Rule**：凡涉及所有权（租约、锁、抢占）的状态推进与清理，必须单条带条件的 UPDATE/CAS 完成，禁止「先 SELECT 后按主键写」。
