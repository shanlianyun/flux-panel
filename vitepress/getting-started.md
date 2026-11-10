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

## 使用 fork 自有镜像部署（推荐）

新部署请使用本 fork 的 GHCR 镜像和专用 Compose 文件，不要使用下方旧版交互式安装器。部署前在 GitHub Actions 确认最近一次 `Validate and publish fork images` 的验证、前后端镜像发布任务均成功，并将 `IMAGE_TAG` 设置为该次提交的完整 `sha-<提交号>` 标签。当前前后端镜像可匿名拉取。

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

IPv6 环境下载 `docker-compose-fork-v6.yml` 替代 v4 文件，并在下面命令中相应替换 Compose 文件名。`gost.sql` 只会在全新数据库卷初始化时执行；不要把这些新部署步骤直接用于已有数据库。原作者版本迁移请先阅读[迁移指南](https://github.com/shanlianyun/flux-panel/blob/main/docs/migrate-from-original.md)，在隔离测试环境用备份副本验证后再安排生产维护窗口。

首次登录后立即修改默认管理员密码，并配置防火墙，仅开放所需的前端及转发端口。fork Compose 不会将后端 API 或数据库端口映射到宿主机。

## 旧版交互式安装（仅兼容已有部署）

以下 `panel_install.sh` 和默认 Compose 文件仍引用原作者 Docker Hub 镜像，仅供已有原作者部署延续原有安装、更新和卸载操作。**不要将它用于新的 fork 部署，也不要把其更新选项当作迁移步骤。**

```bash
curl -L https://raw.githubusercontent.com/shanlianyun/flux-panel/refs/heads/main/panel_install.sh -o panel_install.sh && chmod +x panel_install.sh && ./panel_install.sh
```

### 旧版安装流程说明

1. **运行脚本后选择操作**：
   ```
   ===============================================
             面板管理脚本
   ===============================================
   请选择操作：
   1. 安装面板      ← 选择这个
   2. 更新面板
   3. 卸载面板
   4. 退出
   ===============================================
   ```

2. **输入配置参数**：
   - **面板服务器地址**: 需要节点服务器能正常访问的地址
   - **前端端口**: 默认 6366
   - **后端端口**: 默认 6365

3. **自动化处理**：
   - 检测 Docker 环境
   - 自动检测 IPv6 支持并选择合适的配置
   - 生成随机的数据库配置
   - 下载并启动服务

### 配置示例

```bash
当前面板服务器地址: 192.168.1.100  # 或您的公网IP
前端端口（默认 6366）: 6366         # 直接回车使用默认值
后端端口（默认 6365）: 6365         # 直接回车使用默认值
```

## 验证旧版交互式安装

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
- `vue-frontend` - 前端 Web 界面

### 2. 访问 Web 界面

打开浏览器访问：`http://your-server-ip:6366`

### 3. 登录系统

使用默认管理员账号登录：
- **用户名**: `admin_user`
- **密码**: `admin_user`

⚠️ **重要**: 首次登录后请立即修改默认密码！

## IPv6 支持

脚本会自动检测系统的 IPv6 支持：

- ✅ **支持 IPv6**: 自动使用 IPv6 配置文件并配置 Docker IPv6 支持
- ⚠️ **不支持 IPv6**: 使用 IPv4 配置文件

### Docker IPv6 配置

如果检测到 IPv6 支持，脚本会自动配置：

```json
{
  "ipv6": true,
  "fixed-cidr-v6": "fd00::/80"
}
```

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
