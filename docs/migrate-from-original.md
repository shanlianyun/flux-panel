# 从原作者版本迁移到 shanlianyun/flux-panel

本文面向目前正在运行原作者镜像的服务器。**迁移是手动维护操作，不会由本仓库的 CI、安装脚本或普通更新自动触发。** 在完成备份、镜像发布和测试前，继续使用当前系统即可。

## 迁移前准备

1. 查看 GitHub Actions 中最近一次 `Validate and publish fork images` 运行，确认验证及前后端发布任务都成功。正常推送到 `main` 会自动触发该工作流；只有需要重跑时才手动 dispatch。
2. 本文更新时，`flux-panel-backend` 和 `flux-panel-frontend` 均已通过匿名拉取检查。若之后更改了 GHCR 包可见性，服务器无登录时必须把两个包设为 Public；如保持 Private，则先在服务器配置仅有读取权限的 GitHub 凭据并使用 `docker login ghcr.io`。不要把有写权限的令牌放到服务器。
3. 选择与成功发布的工作流提交号对应的完整镜像标签 `sha-<完整提交号>`；不要仅依赖可变的 `latest`。部署前可在测试机执行 `docker compose ... pull`，确认目标标签可拉取。
4. 确认当前部署目录、Compose 文件、`.env`、Docker 卷名和数据库版本。当前 fork 代码基于仓库中的数据库结构；如果线上系统版本更旧或有本地改动，不能直接假设 schema 兼容。先在隔离测试机恢复一份备份副本，验证启动和数据读写；不要在生产库手动套用 `gost.sql`（它是新库初始化脚本，不是迁移脚本）。
5. 预留维护窗口。停服时间至少覆盖数据库导出、容器替换及健康检查。

## 备份

在当前 Compose 项目目录执行，并确认备份文件存放在服务器以外的安全位置。下面的命令从后端容器读取数据库连接信息并导出数据库；按实际容器/Compose 名称调整命令：

```bash
set -euo pipefail
umask 077
mkdir backup-before-fork-migration
cp .env backup-before-fork-migration/
cp docker-compose.yml backup-before-fork-migration/
cp gost.sql backup-before-fork-migration/
docker exec gost-mysql sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers --databases "$MYSQL_DATABASE"' \
  > backup-before-fork-migration/database.sql
test -s backup-before-fork-migration/database.sql
```

另行保存 gost 节点配置（通常位于节点的 `/etc/gost/config.json` 和 `gost.json`）、当前镜像 ID 和 `docker compose ps` 输出。数据库导出可能含有账号、节点密钥和流量记录，应按敏感数据保护。

## 执行切换

先把仓库中的 `docker-compose-fork-v4.yml` 或 `docker-compose-fork-v6.yml` 与 `gost.sql` 放到原部署目录。创建独立文件 `compose-fork.env`，填入原 `.env` 中的数据库名、用户、密码、JWT 密钥和端口，并额外设置已选镜像标签：

```dotenv
DB_NAME=沿用原值
DB_USER=沿用原值
DB_PASSWORD=沿用原值
JWT_SECRET=沿用原值
FRONTEND_PORT=沿用原前端端口
IMAGE_TAG=sha-替换为完整提交号
```

创建后执行 `chmod 600 compose-fork.env`，并将备份目录及其中的凭据/SQL 保护好。

执行前核对配置并拉取镜像：

```bash
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml config
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml pull
```

IPv6 服务器使用 `docker-compose-fork-v6.yml`。确认镜像地址/tag 正确、配置无空值后，在原 Compose 项目目录先只停止并删除容器及网络，**不要加 `-v`、`--volumes` 或 `--rmi`**：

```bash
docker compose -f docker-compose.yml down
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml up -d
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml ps
```

新 Compose 保留 `mysql_data` 和 `backend_logs` 的显式卷名，并沿用原容器名；后端不再映射到宿主机端口，前端 Nginx 通过 Docker 内网访问后端。不要同时启动两套 Compose；也不要运行 `panel_install.sh` 的卸载选项。phpMyAdmin 不会在新 Compose 中启动；数据库管理请通过本机隧道或受限的临时维护方案进行。

## 验证与回滚

检查前端页面、登录、验证码、API、WebSocket、节点心跳、隧道和流量统计，并确认数据库仍挂载原卷。原安装器更新流程会执行内嵌数据库结构迁移；这里不自动运行该迁移，因为它可能删除旧字段。先在备份副本验证结构差异和升级结果，再将经验证的迁移步骤纳入正式维护流程。观察日志：

```bash
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml logs --tail=200
```

若有问题，先保留数据卷和备份，停止 fork 容器，再用保存的原 Compose 文件启动旧系统。不要加删除卷或删除镜像的参数：

```bash
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml down
docker compose -f backup-before-fork-migration/docker-compose.yml up -d --pull never
```

`--pull never` 依赖原作者镜像仍缓存在本机，因此切换前务必保存 `docker image ls` / `docker image inspect` 信息，并确认原镜像仍在本机。若 fork 版本已写入不兼容数据库结构，单纯回滚容器并不够，必须恢复迁移前 SQL 备份；先停止服务，再人工确认恢复目标，切勿直接覆盖生产库。

完成稳定观察并确认可恢复后，再另行安排 MySQL 大版本升级及长期备份策略。本次 fork Compose 继续使用 MySQL 5.7，以降低迁移期间的变量。

## gost 节点二进制

fork 的 [`gost-latest` Release](https://github.com/shanlianyun/flux-panel/releases/tag/gost-latest) 已发布 Linux AMD64/ARM64 二进制及对应 SHA-256 文件，并保留兼容旧脚本的 `gost`/`gost.sha256` 资产；已下载核对当前三个二进制的校验和。修改 `go-gost` 源码后，在 GitHub Actions 手动运行 `Build gost release assets` 更新这些资产。新安装脚本会严格要求匹配的校验和；在测试节点验证安装、`gost -V` 和节点心跳前，不要用新 `install.sh` 更新正在运行的节点，再分批迁移节点。

安装器默认直连 GitHub，不再根据 IP 地理定位自动将二进制和配置下载请求转交给第三方代理。如网络环境必须使用镜像，请显式设置 `GITHUB_MIRROR_PREFIX`，只使用你信任的服务。
