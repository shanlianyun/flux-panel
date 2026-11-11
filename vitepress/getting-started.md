# 快速开始

本指南将帮助您快速安装和配置flux-panel，让您在几分钟内开始使用流量转发功能。

## 系统要求

在开始之前，请确保您的系统满足以下要求：

- **操作系统**: Linux，**推荐纯净系统安装**
- **Docker**: 20.10+ 
- **Docker Compose**: Docker Compose 插件 v2+
- **内存**: 至少 1GB RAM
- **存储**: 至少 1GB 可用空间
- **网络**: 确保相关端口未被占用

## 一键安装或迁移（推荐）

在服务器执行这一条命令，打开 Flux Panel 管理菜单：

```bash
curl --fail --location --retry 3 https://raw.githubusercontent.com/shanlianyun/flux-panel/main/panel_install.sh -o panel_install.sh && bash panel_install.sh
```

需要 Bash、curl、Python 3.8+、Docker 和支持 `config --format json` 的 Docker Compose v2。建议从部署服务器本机运行；不支持远程 Docker endpoint 或旧版 `docker-compose` v1。

### 全新安装

选择菜单 **`1. 安装本 fork`**，并使用专用空目录。脚本会生成随机数据库密码和 JWT 密钥，使用对应提交的不可变镜像标签。若发现现有 Compose 文件、容器、数据卷或项目，会停止而不会覆盖旧部署。

### 从原作者版本迁移

选择菜单 **`7. 旧版迁移到本 fork`**。脚本会尝试从后端容器的 Compose 标签找到原部署目录；核对路径无误后，在目录提示处按回车，或输入原路径。原部署目录通常包含 `docker-compose.yml`、`.env` 和 `gost.sql`。例如可用以下命令查看 Compose 记录的路径：

```bash
docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' springboot-backend
```

建议先保护含数据库凭据的 `.env` 文件：

```bash
chmod 600 /原部署目录/.env
```

迁移前会检查支持范围、数据库结构及可用磁盘空间，备份配置和数据库，并先拉取前后端镜像；确认后才替换前后端容器。会保留原端口、网络、数据卷、凭据及其他服务，不自动修改数据库结构、重启 MySQL/Docker 或更新节点。若检查不通过会停止，不要改选“卸载”或“全新安装”来绕过。

### 后续更新与恢复

- 已迁移的系统选择 **`2. 更新本 fork`**。普通更新不会自动把原作者版本切换到 fork。
- **`4. 导出数据库与配置备份`**可单独备份；**`8. 恢复中断的迁移/更新`**会尝试恢复旧容器和配置，不会自动回灌数据库。
- 菜单会保留在服务器上，下次可运行 `bash panel_install.sh` 再次打开。

镜像默认使用 `main` 当前提交对应的完整 `sha-<40位提交号>` 标签。也可通过 `PANEL_IMAGE_TAG=sha-<完整提交号>` 指定已发布版本。迁移只支持常见的单文件 Compose v2、MySQL 5.7/InnoDB 及标准服务；这不是任意旧数据库的自动升级器。建议先在隔离副本测试，并安排维护时间。

失败时会尝试回退容器与配置，不会自动覆盖数据库。基础健康检查不能替代登录、WebSocket、节点心跳、隧道及流量验证。备份包含敏感数据库信息，应另存到服务器外。完整支持范围和恢复说明见[原作者版本迁移指南](https://github.com/shanlianyun/flux-panel/blob/main/docs/migrate-from-original.md)。

管理脚本默认使用数字菜单，直接运行即可：

```bash
bash panel_install.sh
```

按菜单选择：已迁移系统选 `2` 更新，原作者系统选 `7` 迁移，单独备份选 `4`，中断恢复选 `8`。维护脚本会保留自身；更新或迁移中断时，先选 `8` 恢复容器和配置，再处理业务验证。

## 节点安装与滚动更新

全新机器先在面板创建节点，再从“节点管理”复制一条安装命令，以 root 身份在节点服务器粘贴执行。短命令通过 `node.sh` 引导脚本处理私有临时目录、下载检查及清理，保留节点程序的 SHA-256 和可执行版本检查，不覆盖当前目录文件。命令包含该节点密钥，请勿公开分享或在不同节点间复用。HTTPS/WSS 入口验证可用后，先在同一个终端执行 `export GOST_PANEL_SCHEME=https`，再粘贴安装命令。

已有原作者节点可以保留配置滚动升级，不要重复执行安装命令。先确认包含旧节点兼容修复的面板镜像和目标节点 Release 都已发布，再下载**本 fork** 的脚本打开菜单（旧脚本仍然下载原作者程序）：

```bash
curl --fail --location --retry 3 https://raw.githubusercontent.com/shanlianyun/flux-panel/main/install.sh -o flux-node.sh && bash flux-node.sh
```

选择菜单 `2` 更新。更新只替换通过 SHA-256 和版本检查的 GOST 二进制，保留 `/etc/gost/config.json`、`/etc/gost/gost.json`，并短暂重启 `gost` 服务；新二进制启动失败时会恢复旧二进制。建议先灰度一台节点，确认转发和节点心跳正常后，再逐台滚动更新。公网面板先配置 TLS 反向代理，再运行 `GOST_PANEL_SCHEME=https bash flux-node.sh` 打开同一个菜单选 `2`。

面板维护期间，节点本地已运行的转发规则不会因为控制通道断开而停止；新增、编辑、暂停和删除操作需等面板恢复。节点会自动重连并重新同步有效规则。

旧节点仍可使用原有 HTTP/WS 通道；在全部节点完成迁移前，不要直接关闭它或强制仅允许 HTTPS/WSS。旧节点不能获得新版的暂停持久化和安全加固；当存在暂停规则时，面板会拒绝不安全的限速配置修改并提示升级，而不是偷偷启动监听。详见[常见问题](./faq#原作者旧节点能否继续使用)。

节点检查命令：

```bash
/etc/gost/gost -V
systemctl is-active gost
systemctl status gost --no-pager
journalctl -u gost -n 50 --no-pager
```

## 高级：手动使用 fork 镜像部署

若需要自定义 Compose 部署，可以手动使用 fork 的 GHCR 镜像和专用 Compose 文件。部署前确认 GitHub Actions 最近一次 `Validate and publish fork images` 的验证、前后端镜像发布均成功，并将 `IMAGE_TAG` 设置为对应完整提交号的 `sha-<40位提交号>` 标签。当前镜像可匿名拉取。

在新目录下载初始化 SQL 和 IPv4 Compose 文件：

```bash
mkdir flux-panel && cd flux-panel
curl --fail --location --retry 3 --retry-delay 2 \
  https://raw.githubusercontent.com/shanlianyun/flux-panel/main/gost.sql -o gost.sql
curl --fail --location --retry 3 --retry-delay 2 \
  https://raw.githubusercontent.com/shanlianyun/flux-panel/main/docker-compose-fork-v4.yml -o docker-compose-fork-v4.yml
```

创建环境文件并替换所有示例密码、密钥和镜像标签。数据库密码应为高强度随机值，`JWT_SECRET` 至少 32 字节；不要复用示例值：

```bash
umask 077
cat > compose-fork.env <<'EOF'
DB_NAME=flux
DB_USER=flux_user
DB_PASSWORD=替换为高强度随机密码
JWT_SECRET=替换为至少32字节的随机密钥
FRONTEND_PORT=6366
IMAGE_TAG=sha-替换为成功发布的完整提交号
EOF
chmod 600 compose-fork.env
```

核对 Compose 配置并启动：

```bash
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml config
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml pull
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml up -d
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml ps
```

IPv6 环境下载 `docker-compose-fork-v6.yml` 替代 v4 文件，并在下面命令中相应替换 Compose 文件名。`gost.sql` 只会在全新数据库卷初始化时执行；不要把这些新部署步骤直接用于已有数据库。已有原作者部署请使用上方菜单显式迁移，或先阅读[迁移指南](https://github.com/shanlianyun/flux-panel/blob/main/docs/migrate-from-original.md)。

首次登录后立即修改默认管理员密码，并配置防火墙，仅开放所需的前端及转发端口。fork Compose 不会将后端 API 或数据库端口映射到宿主机。

## 验证部署

### 1. 检查服务状态

```bash
# 使用 docker-compose
docker-compose ps

# 或使用 docker compose
docker compose ps
```

您应该看到以下服务都处于 `Up` 状态：
- `gost-mysql` - 数据库服务
- `springboot-backend` - 后端 API 服务
- `vite-frontend` - 前端 Web 界面

### 2. 访问 Web 界面

打开浏览器访问：`http://your-server-ip:6366`

### 3. 登录系统

使用默认管理员账号登录：
- **用户名**: `admin_user`
- **密码**: `admin_user`

⚠️ **重要**: 首次登录后请立即修改默认密码！

## IPv6 支持

新建双栈部署时，请按实际网络状况手动选择 `docker-compose-fork-v4.yml` 或 `docker-compose-fork-v6.yml`。管理菜单不会自动修改 Docker daemon 的 IPv6 设置。迁移现有部署时会保留原 Compose 网络配置；若网络状态与配置不符，则停止迁移。

## 端口说明

| 端口 | 服务 | 说明 | 可配置 |
|------|------|------|--------|
| 6366 | Web 界面 | 前端管理界面 | ✅ |
| 6365 | API 服务 | 旧版安装器映射；fork Compose 仅在 Docker 内网可访问 | 旧版可配置 |
| 3306 | MySQL | 数据库服务（容器内部） | ❌ |
| 动态分配 | 转发端口 | 用户创建的转发规则 | ✅ |

## 获取帮助

如果在安装过程中遇到问题：


1. **🔍 故障排除**
   - 查看安装日志和错误信息
   - 检查系统要求是否满足
   - 验证 Docker 和网络配置


2. **📖 文档资源**
   - [使用指南](/guide) - 详细的操作教程
   - [常见问题](/faq) - 快速解决方案和管理操作指南 
