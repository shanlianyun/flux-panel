# 日志容量与保留策略

## 两层日志分别限额

后端同时写入应用文件日志和控制台，Docker 会另外收集控制台输出。因此需要分别限制两层日志，不能只处理其中一处。

| 日志 | 默认策略 | 生效范围 |
| --- | --- | --- |
| 后端文件 | 活动文件 `backend.log`，约 20MB 切分，跨日也切分 | 包含本修复的新后端镜像或源码构建 |
| 后端归档 | `backend.YYYY-MM-DD.N.log.gz`，滚动保留策略配置为 7 天、200MB | 同上；长期停机的旧文件例外见下文 |
| Docker 控制台 | `json-file`，`max-size: "10m"`，`max-file: "3"`，每个容器约 30MB | 使用更新后 Compose 创建或重建的容器 |

四份 Compose 的所有服务均设置 Docker 轮转，包括旧版 Compose 中的 phpMyAdmin。三个服务的 fork 部署，其 Docker 日志预算约 90MB；四个服务的旧版部署约 120MB。这不是整个 Docker 数据目录的配额，数据库文件、镜像、其他容器等不在其中。

Logback 的 200MB 限制其滚动历史窗口内、匹配新命名规则的**归档**，不包含活动文件。切分时大小检查、单条大日志、压缩临时文件和异步清理会带来短暂超出，不能把它当作文件系统硬配额。持续正常运行时，滚动清理按七天和容量限制保留日志，不保证高流量时能保留完整七天。每个后端实例应独占自己的日志目录，不支持多个 JVM 同时写同一 `backend.log`。

**长期停机例外：** 当前 Logback 1.2 的启动清理只向保留边界之外回看有限时段（约 32 天），不会遍历所有历史文件。例如停机两个月后，60 天前的 `backend.YYYY-MM-DD.N.log.gz` 可能仍然存在，且不计入后续 `totalSizeCap` 的清理窗口。这些文件可能一直残留，不会因后续日常轮转自动补清理，并非暂时超额。因此即使命名符合新规则，也不能保证启动后目录内所有文件都在七天或 200MB 以内。长期停机恢复或复制旧日志目录后，需要先清点、备份并单独审核清理超出窗口的旧归档。本修复不额外加入全目录删除逻辑。

## 调整文件日志限额

可在 Compose 使用的 `.env` 中配置，省略时使用默认值：

```dotenv
LOG_FILE_MAX_SIZE=20MB
LOG_FILE_MAX_HISTORY=7
LOG_FILE_TOTAL_SIZE_CAP=200MB
```

`LOG_FILE_MAX_HISTORY` 使用正整数；归档总容量应不小于单文件切分大小。不要设为零来试图禁用日志，那可能禁用清理或造成配置错误。

流量上报、配置接收、健康检查的成功请求日志，以及常规 WebSocket 消息正文，改为 DEBUG。配置检查不再直接把整份节点配置打印到 stdout。管理操作仍保留 INFO，异常仍保留 WARN/ERROR。临时开启 DEBUG 会增大日志量，且请求详情可能包含敏感参数，请限制排查范围和持续时间，不要公开上传原始日志。

## 已运行的服务器不会自动生效

本页仅提供操作说明，不会自动执行部署或清理。

1. 先备份数据库、`.env`、Compose 和需要保留的历史日志，确认可接受的维护窗口。
2. 确认包含本修复的后端镜像已经通过 CI 并发布，使用该次提交的固定 `sha-<完整提交号>` 标签；仅提交代码或合并 PR 并不等于服务器升级。
3. 比较服务器上的 Compose 与仓库对应版本，合入 `logging` 配置与必要环境变量。不要直接覆盖自定义端口、网络、镜像和其他配置。
4. 后端文件轮转和降低打印量需要运行**新后端镜像**。原作者的 `brunuhville/springboot-backend` 镜像不会因为更新本仓库的 Compose 而获得新的应用配置；需要按[迁移指南](migrate-from-original.md)评估迁移，或自行构建本修复。
5. Docker 日志选项只对新建或重建的容器生效，单纯 `docker restart` 不会应用。应另行授权并安排对应服务的重建；MySQL 重建会造成数据库短暂不可用，不要为了日志问题随意重启整套服务。
6. 部署后检查容器实际日志选项和新归档文件。保留回滚所需的镜像标签和配置。

不要运行 `docker compose down -v`，它可能删除数据库和日志卷；也不需要为此重启 Docker 守护进程。

## 只读检查当前占用

以下命令在服务器上执行，只用于观察，不删除文件：

```bash
sudo du -sh /var/lib/docker/volumes/backend_logs/_data /var/lib/docker/containers

sudo find /var/lib/docker/volumes/backend_logs/_data \
  -maxdepth 1 -type f -printf '%s %p\n' | sort -nr | head -20

docker inspect --format '{{.Name}} {{json .HostConfig.LogConfig}} {{.LogPath}}' \
  $(docker ps -aq)
```

目录路径取决于 Docker data-root、存储方式和卷名称；如果与部署不符，先用 `docker info`、`docker volume inspect backend_logs` 和 `docker inspect` 核实。日志可能包含节点密钥或用户信息，不要直接公开日志正文。

## 已有大日志需要单独处理

- 旧版日期文件（如 `2026-11-01.log`）不匹配新归档名，不会被新策略自动删除。先确认文件清单、保留需求和备份，再单独安排清理；不要删除仍被旧后端写入的当天日志。
- 长期停机后，超出 Logback 启动回看范围的新格式旧归档也需要单独清点与审核清理，不能仅凭 `totalSizeCap` 判断整个日志目录的占用。
- 已有 Docker 大日志不会因为修改 YAML 而立即缩小。优先在维护窗口内通过受控的容器重建应用轮转策略；旧容器日志应先按需导出保存。
- 不要批量 `rm` 或直接 `truncate` Docker 管理的 `*-json.log`，Docker 仍可能持有文件或处于写入状态。删除打开的文件也不一定立即释放空间。
- 如果磁盘已经满，先反馈占用与日志选项的检查结果，再制定针对性的应急处理，不要清理数据库卷、执行 `docker system prune --volumes` 或盲目重建服务。
