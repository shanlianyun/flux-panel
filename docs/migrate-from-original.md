# 从原作者版本迁移到 shanlianyun/flux-panel

本文面向目前正在运行原作者镜像的服务器。迁移不会由 CI 或普通更新自动触发；需要你在管理菜单中明确选择迁移并确认。不要通过卸载、删除卷或重新安装来迁移旧系统。

## 推荐：一行命令打开管理菜单

```bash
curl --fail --location --retry 3 https://raw.githubusercontent.com/shanlianyun/flux-panel/main/panel_install.sh -o panel_install.sh && bash panel_install.sh
```

1. 已有原作者系统选 **`7. 旧版迁移到本 fork`**。
2. 核对建议的原部署目录。脚本优先使用当前目录；当前目录没有配置时，会尝试从旧后端的 Compose 标签找到目录。可以手动输入路径。
3. 核对目标版本和维护提示，输入 `yes`。失败、取消或检查不通过时，不会先停止服务。
4. 完成后验证登录、验证码、WebSocket、节点心跳、隧道转发和流量统计。之后用同一个脚本的 **`2`** 更新；脚本会保留，不必每次重新下载。

菜单 `4` 导出备份；`8` 恢复中断的迁移/更新（只回退容器和配置，不恢复 SQL）。新安装选 `1`，只能在没有旧部署的专用空目录中进行，目录中可以有管理脚本。已有 `compose.yaml` 等配置、旧数据卷或同名项目容器时都会拒绝安装。

### 支持范围与安全边界

- 在部署服务器本机运行；需要 Bash、curl、Python 3.8+ 和支持 `config --format json` 的 Docker Compose v2（建议使用维护中的新版，已用 v2.34 验证配置行为）。不自动安装依赖或重启 Docker，不支持远程 Docker endpoint 或旧版 `docker-compose` v1。
- 支持包含普通 `docker-compose.yml` 与 `.env` 的单文件部署，以及标准 `mysql` / `backend` / `frontend` 服务和容器名 `gost-mysql` / `springboot-backend` / `vite-frontend`。不支持 profiles/build、多文件覆盖、外部数据库或与配置不一致的运行状态。备份菜单也需要这些容器运行中。
- 保留原 Compose 中的端口、网络、挂载、其他服务（包括 phpMyAdmin）和配置；不会把旧部署直接换成 fork 模板。若手动连接过反代网络或额外挂载，必须先把它们写入原 Compose，否则检查会拒绝迁移。前后端 Docker 日志改为 `10m × 3`，缺失的后端归档参数补为默认值，后端增加已知健康检查。
- 原 MySQL 必须是 5.7 且数据表使用 InnoDB；检查当前代码所需的表、字段类型、自增 ID 和额外必填字段。旧后端须有健康检查且处于健康状态，确保回退可验证。**检查不等于证明所有 schema/业务兼容**，不检查任意自定义触发器、索引和应用语义；先在隔离副本验证。结构过旧时会停止，不会改表、导入初始化 SQL 或删除旧字段。
- 默认查询 `main` 的完整提交号，预先拉取两个对应的 `sha-<40位提交号>` 镜像；未发布或无法拉取就停止，不会退回 `latest`。可用 `PANEL_IMAGE_TAG=sha-<完整提交号> bash panel_install.sh` 选择已验证的已发布版本。私有 GHCR 包需要预先配置只读登录凭据。
- 备份目录为原部署目录下的 `panel-backups/<UTC时间-随机后缀>/`，权限 `700`，文件 `600`。保存原配置、`.env`、有效配置、旧镜像 ID、回退配置及非空 SQL 导出；有本地 `gost.sql` 时也一并保存。数据库导出含账号和节点密钥，务必另存到服务器外；非空不等于已验证可恢复。
- 检查部署目录和 Docker 存储目录的空间。部署目录至少预留 `max(1GiB, 数据表大小 × 3 + 256MiB)`，Docker 存储至少预留 `2GiB`；这只是保守预检，不是硬配额或镜像展开大小保证。备份不自动删除，失败导出的 `.partial` 也会留在该次私有备份目录，后续应自行核对、异地保存和归档。
- 切换时只以 `--no-deps --pull never` 重建前后端，不停止或升级 MySQL、其他服务及节点，不修改 `/etc/docker/daemon.json`。MySQL/phpMyAdmin 的已有 Docker 日志限制不会因此生效；历史日志也不会被删除，另见[日志说明](log-retention.md)。

### 失败与恢复

普通失败或 SIGINT/SIGTERM 会尝试用备份中的旧镜像 ID 回退前后端，并恢复原 Compose 文件；**绝不自动覆盖数据库**。回退失败、断电或进程被强制结束时，保留 `.panel-transaction.json`，再次运行菜单选 `8`。不要卸载、删除卷或运行安装覆盖。

基础检查只验证后端容器健康和前端本机 HTTP，不证明公网反代、登录或转发规则正常。新版本启动期间可能写入数据；即使容器成功回退，也不能保证数据库已恢复到迁移前时点。SQL 恢复必须另行停服、核对目标并人工确认。首次安装失败会保留新配置/数据卷供排查，不会自动删库重装。

新安装默认使用 fork 的 IPv4 Compose；已有 IPv6 部署迁移时保留其网络，不自动开关 IPv6。需要新建双栈部署的高级用户可使用下述手动方式与 `docker-compose-fork-v6.yml`。

## 高级：手动切换

不使用管理菜单时，可以参照以下步骤自行安排备份和维护窗口。此方式替换整套 Compose，和菜单的“只重建前后端、保留其他服务”不同；必须先核对端口、反代和网络差异。

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
ORIGINAL_PROJECT=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' springboot-backend)
test -n "$ORIGINAL_PROJECT"
printf '%s\n' "$ORIGINAL_PROJECT" > backup-before-fork-migration/project-name
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
ORIGINAL_PROJECT=$(cat backup-before-fork-migration/project-name)
docker compose --project-directory "$PWD" --project-name "$ORIGINAL_PROJECT" --env-file compose-fork.env -f docker-compose-fork-v4.yml up -d
docker compose --project-directory "$PWD" --project-name "$ORIGINAL_PROJECT" --env-file compose-fork.env -f docker-compose-fork-v4.yml ps
```

新 Compose 保留 `mysql_data` 和 `backend_logs` 的显式卷名，并沿用原容器名；后端不再映射到宿主机端口，前端 Nginx 通过 Docker 内网访问后端。不要同时启动两套 Compose；也不要运行 `panel_install.sh` 的卸载选项。phpMyAdmin 不会在新 Compose 中启动；数据库管理请通过本机隧道或受限的临时维护方案进行。

## 验证与回滚

检查前端页面、登录、验证码、API、WebSocket、节点心跳、隧道和流量统计，并确认数据库仍挂载原卷。旧安装器曾在更新时执行内嵌数据库结构迁移；新管理菜单已移除这条可能删除字段的路径，手动方式也不自动执行改表。先在备份副本验证结构差异和升级结果，再将经验证的迁移步骤纳入正式维护流程。观察日志：

```bash
ORIGINAL_PROJECT=$(cat backup-before-fork-migration/project-name)
docker compose --project-directory "$PWD" --project-name "$ORIGINAL_PROJECT" --env-file compose-fork.env -f docker-compose-fork-v4.yml logs --tail=200
```

若有问题，先保留数据卷和备份，停止 fork 容器，再用保存的原 Compose 文件启动旧系统。不要加删除卷或删除镜像的参数：

```bash
ORIGINAL_PROJECT=$(cat backup-before-fork-migration/project-name)
docker compose --project-directory "$PWD" --project-name "$ORIGINAL_PROJECT" --env-file compose-fork.env -f docker-compose-fork-v4.yml down
docker compose --project-directory "$PWD" --project-name "$ORIGINAL_PROJECT" --env-file backup-before-fork-migration/.env -f backup-before-fork-migration/docker-compose.yml up -d --pull never
```

`--pull never` 依赖原作者镜像仍缓存在本机，因此切换前务必保存 `docker image ls` / `docker image inspect` 信息，并确认原镜像仍在本机。若 fork 版本已写入不兼容数据库结构，单纯回滚容器并不够，必须恢复迁移前 SQL 备份；先停止服务，再人工确认恢复目标，切勿直接覆盖生产库。

完成稳定观察并确认可恢复后，再另行安排 MySQL 大版本升级及长期备份策略。本次 fork Compose 继续使用 MySQL 5.7，以降低迁移期间的变量。

## gost 节点二进制

fork 的 [`gost-latest` Release](https://github.com/shanlianyun/flux-panel/releases/tag/gost-latest) 已发布 Linux AMD64/ARM64 二进制及对应 SHA-256 文件，并保留兼容旧脚本的 `gost`/`gost.sha256` 资产；已下载核对当前三个二进制的校验和。修改 `go-gost` 源码后，在 GitHub Actions 手动运行 `Build gost release assets` 更新这些资产。新安装脚本会严格要求匹配的校验和；在测试节点验证安装、`gost -V` 和节点心跳前，不要用新 `install.sh` 更新正在运行的节点，再分批迁移节点。

安装器默认直连 GitHub，不再根据 IP 地理定位自动将二进制和配置下载请求转交给第三方代理。如网络环境必须使用镜像，请显式设置 `GITHUB_MIRROR_PREFIX`，只使用你信任的服务。

安全部署节点时，先为面板配置有效 TLS 证书和反向代理，再使用 `GOST_PANEL_SCHEME=https ./install.sh`（或更新前导出该变量）。旧的 HTTP/WS 模式仅用于受控内网兼容，不能作为公网安全配置。

### 面板维护时的节点可用性

节点上的 GOST 转发服务由节点本地进程独立监听和转发。面板停机维护只会暂时中断控制通道和状态上报，不会停止已经运行的转发规则；节点会每 5 秒重试连接，面板恢复后自动重连并同步数据库中的有效规则。维护期间请不要依赖面板执行规则变更，恢复后再检查节点在线状态和转发连通性。

### 节点脚本与滚动更新

节点安装命令应从面板“节点管理”页面复制。脚本现在支持以下明确命令：

```bash
./install.sh install -a '<面板地址:6365>' -s '<节点密钥>'
./install.sh update
./install.sh uninstall
./install.sh block-protocol
./install.sh help
```

已有旧节点请使用 `update`，不要重新执行 `install`；更新会保留 `/etc/gost/config.json` 和 `/etc/gost/gost.json`，只在下载文件通过 SHA-256 与版本检查后替换二进制。若新二进制无法启动，脚本会恢复旧二进制。更新期间 GOST 会短暂重启，因此应先灰度一台节点，再按批次滚动更新，并在每台节点执行 `systemctl is-active gost`、`gost -V` 和实际转发检查。公网面板请在 TLS 反向代理就绪后设置 `GOST_PANEL_SCHEME=https`。
