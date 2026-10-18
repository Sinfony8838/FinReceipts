# 容器重建与运行库持久化

原 Compose 只将 SEC 缓存挂到 `/app/.cache/sec`，任务库默认写入容器可写层的
`/app/.cache/runs.sqlite3`。同一容器的 stop/start 不会证明重建安全；删除并创建新容器后，
那份可写层数据不会自动出现在新实例中。

## 新部署的存储边界

| 内容 | 容器路径 | Compose 卷 |
| --- | --- | --- |
| 可重建的 SEC 缓存 | `/app/.cache/sec` | `finreceipts_cache` |
| 任务库与 SQLite sidecars | `/app/.cache/runtime/` | `finreceipts_runtime` |

镜像和 Compose 都明确将 `FINRECEIPTS_RUN_DB` 设为
`/app/.cache/runtime/runs.sqlite3`。挂载整个目录，主库、WAL、SHM 处于同一卷，
不只挂载一个 `.sqlite3` 文件。镜像构建时创建该目录并交给 UID 10001 的 `appuser`，
应用仍以非 root 用户运行，不在启动时对宿主目录递归改权限。

卷名由 Compose 项目前缀隔离，例如 `myproject_finreceipts_runtime`。
更改项目名会创建另一组卷，不会迁移原数据。普通 `docker compose down` 不删除命名卷；
`down --volumes`、显式删除卷、丢失 Docker 存储仍会丢数据。命名卷不代替备份。
这与 Docker 的[卷生命周期](https://docs.docker.com/engine/storage/volumes/)和
[Compose down 行为](https://docs.docker.com/reference/cli/docker/compose/down/)一致。

## 已有容器升级：先保留旧库

**新卷不会自动导入旧容器可写层的记录。已有部署不能直接用重建命令完成升级。**
本轮没有修改、停止或重建任何既有业务容器。以下为管理员操作步骤，容器名及路径均需
替换成实际值，备份输出目录必须是新目录。

1. 在旧容器仍存在时，仅查询数据库路径，不导出完整环境变量：

   ```powershell
   docker exec OLD_CONTAINER python -c "import os; print(os.path.abspath(os.getenv('FINRECEIPTS_RUN_DB','.cache/runs.sqlite3')))"
   ```

2. 停止旧容器，保留容器本身；将已停止数据库的**整个所在目录**复制到新目录，保留
   WAL/SHM。不要对仍在写入的数据库做独立文件复制。若路径是默认旧路径，可使用：

   ```powershell
   New-Item -ItemType Directory D:\recovery\finreceipts-old-001
   docker stop OLD_CONTAINER
   docker cp OLD_CONTAINER:/app/.cache/. D:\recovery\finreceipts-old-001
   ```

3. 用当前工具将这份停止后的副本转换为经过校验的独立备份。旧容器与复制品均保留：

   ```powershell
   finreceipts runtime-backup --database D:\recovery\finreceipts-old-001\runs.sqlite3 --out D:\recovery\verified-001
   finreceipts runtime-verify-backup --backup D:\recovery\verified-001
   ```

4. 使用新的 Compose 项目，构建镜像，以只读挂载的备份恢复到卷内**新的子目录**。
   以下命令在仓库根目录执行，不覆盖旧库或卷内已有文件：

   ```powershell
   docker compose -p finreceipts-restored build
   docker compose -p finreceipts-restored run --rm --no-deps -v D:/recovery/verified-001:/backup:ro api finreceipts runtime-restore --backup /backup --out /app/.cache/runtime/restored-001
   ```

5. 创建单独的 `recovery.override.yml`，显式选择恢复后的数据库，再启动新项目：

   ```yaml
   services:
     api:
       environment:
         FINRECEIPTS_RUN_DB: /app/.cache/runtime/restored-001/runs.sqlite3
   ```

   ```powershell
   docker compose -p finreceipts-restored -f docker-compose.yml -f recovery.override.yml up -d --no-build
   ```

   之后对此部署操作须继续使用相同项目名和 override 文件，否则会打开默认的另一份库。
   核对导出、预算及任务状态后再决定是否保留该部署，不自动删除旧容器或旧备份。
   不要同时启动两个库承接同一组任务。更换代码可能触发原有 `implementation_id` 续跑检查；
   能读历史记录不代表能用新实现继续执行，不可绕过检查来宣称迁移成功。

这些迁移命令是操作方案，本机尚未完成 Docker 执行验证；不要把它们当作已演练的生产切换。
在线备份、恢复检查和未知外部调用的边界见[备份恢复说明](BACKUP_RESTORE.md)。

## 重建验收门禁

```powershell
# 不启动容器：仅用真实 Compose CLI 解析配置并核查存储拓扑。
python scripts\container_persistence_smoke.py --out runs\container-config-new --config-only

# Docker 引擎可用时：构建并运行隔离的容器实验。
python scripts\container_persistence_smoke.py --out runs\container-drill-new
```

脚本只使用标准库，生成唯一项目名和镜像标签；显式使用空 env 文件，剔除继承的 Compose
目标和真实模型配置，固定 FakeLLM、离线夹具和回环随机端口。创建资源前检查项目容器、
卷及镜像没有碰撞，并拒绝外部卷、全局卷名和宿主目录挂载；失败不清理未认领资源。
结束时只 down 自己的项目，**不删除卷**；
成功后的卷名留在 summary 中供检查，不执行全局 prune。

完整 Docker 验收要求：

- 检查实际容器挂载为可写命名卷、进程 UID 10001、运行库路径和镜像/实现摘要。
- 创建待核验、已取消、预留预算后中断三种任务，保存完整导出。
- 分别执行 `--force-recreate` 和 `down` 后重新 `up`，确认容器 ID 确实变化。
- 两次重建后的导出均与原记录一致；继续核验不增加调度预算，显式 resume 保留原预留。

CI 增加独立 Ubuntu Docker job，保存每条命令日志和验收结果。
结果状态区分 `PASS`、`CONFIG_VALIDATED_ONLY`、`BLOCKED_UNRUN` 和 `FAILED`。
只有实际容器和 API 检查全部通过，才能得到 `PASS`；配置解析与模拟故障测试不能替代它。

## 本轮证据

2026-10-18，本机 Docker Compose **v5.1.4** 已成功解析修复后的真实配置，结果为
`CONFIG_VALIDATED_ONLY`。新增 19 项测试覆盖缺失库路径、单文件挂载、只读/未声明卷、
挂载覆盖、卷别名、外部共享卷、宿主目录、真实模型环境泄漏、资源碰撞及构建失败清理。
全仓离线回归 **813 passed / 1 skipped**，跳过仍为 Windows 符号链接权限；最终 19 项聚焦
回归与 Ruff 检查通过。这些检查不执行容器，不能证明 Linux 卷权限已通过。

真实容器实验停在引擎 preflight，结果为 **`BLOCKED_UNRUN`**：Docker Desktop 报无法启动，
WSL 状态提示尚未安装。没有构建测试镜像、创建实验容器或运行重建操作；没有修改 Windows
系统功能或要求重启。CI 配置已添加，但尚未远程运行，因此 Linux 卷初始化权限、容器
重建行为和上述迁移流程仍未取得实际平台证据。
本轮启动失败的 Docker Desktop 已关闭，未留下运行中的测试容器。

原始记录保存在 `runs/container-storage-20261018/`；最终 `evidence.json` 绑定配置、脚本、
离线回归和明确的未运行状态。该缺口保留为后续验收项，不包装成已实现企业容灾。
