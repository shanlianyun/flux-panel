- 特别说明：

- 原作者已删库，仅为fork纪念版。

- 方便后人使用。少有的开源面板，不该湮灭断绝。
   
---

## flux-panel转发面板

本项目基于 [go-gost/gost](https://github.com/go-gost/gost) 和 [go-gost/x](https://github.com/go-gost/x) 两个开源库，实现了转发面板。

---
## 文档地址
- [文档地址](https://shanlianyun.github.io/flux-panel/)
- [从原作者版本迁移到本 fork](docs/migrate-from-original.md)
- [日志容量、轮转与历史日志处理](docs/log-retention.md)
---
## 特性

- 支持按 **隧道账号级别** 管理流量转发数量，可用于用户/隧道配额控制
- 支持 **TCP** 和 **UDP** 协议的转发
- 支持两种转发模式：**端口转发** 与 **隧道转发**
- 可针对 **指定用户的指定隧道进行限速** 设置
- 支持配置 **单向或双向流量计费方式**，灵活适配不同计费模型
- 提供灵活的转发策略配置，适用于多种网络代理场景


---
# 流量统计算法

## 流量流向定义

```
用户端 ←→ 转发服务器 ←→ 目标服务器/出口节点
```

### 数据流向说明

| 流向 | 描述 | 计费类型 |
|-----|-----|----------|
| 用户 → 转发服务器 | 用户发送到服务器的数据包 | 入站流量 |
| 转发服务器 → 目标服务器 | 服务器转发到目标的数据包 | 出站流量 |
| 目标服务器 → 转发服务器 | 目标服务器返回的数据包 | 入站流量 |
| 转发服务器 → 用户 | 服务器返回给用户的数据包 | 出站流量 |

## 计费模式

### 🔄 双向计费
统计所有方向的流量，包括：
- ✅ 用户 → 转发服务器
- ✅ 转发服务器 → 目标服务器  
- ✅ 目标服务器 → 转发服务器
- ✅ 转发服务器 → 用户

**计费公式**：`总流量 = 入站流量 + 出站流量`

### ➡️ 单向计费
仅统计有效的转发流量，包括：
- ❌ 用户 → 转发服务器 （不计费）
- ✅ 转发服务器 → 目标服务器 （计费）
- ❌ 目标服务器 → 转发服务器 （不计费）
- ✅ 转发服务器 → 用户 （计费）

**计费公式**：`总流量 = 转发出站流量`

## 使用场景

- **双向计费**：适用于需要精确计量所有网络资源消耗的场景
- **单向计费**：适用于用户友好的计费模式，只对实际的数据转发服务收费

> 💡 **提示**：可以在创建隧道时选择不同的流量计算方式，灵活适配不同的业务需求
---

## 部署流程

### 源码编译部署

```bash

暂时没时间写，能源码部署的都是大佬，不差我这点文档
```
---
### Docker Compose部署

当前已部署的原作者版本不会自动切换，原有 `docker-compose-v4.yml` / `docker-compose-v6.yml` 仍保留。新 fork 镜像发布到 GHCR 后，如需迁移请按[迁移指南](docs/migrate-from-original.md)操作；迁移前先备份数据库与配置，不要运行卸载选项。

#### 快速部署

**新部署请使用本 fork 的 GHCR 镜像和 fork Compose 文件**，不要使用下面的旧版 `panel_install.sh` 安装入口：该安装器和默认 Compose 文件沿用原作者镜像。部署前确认最近一次 `Validate and publish fork images` 工作流的验证与发布任务均成功，并使用该次提交对应的 `sha-<完整提交号>` 镜像标签。本仓库当前前后端 GHCR 镜像已验证可匿名拉取；若之后修改包可见性，则设为 Public，或在服务器配置只读 GHCR 凭据。

在空的部署目录下载初始化 SQL，并创建仅当前用户可读的环境文件。将 `IMAGE_TAG` 设为已发布的完整 `sha-...` 标签；修改所有示例密码/密钥，不要把以下示例值用于生产：

```bash
curl --fail --location --retry 3 --retry-delay 2 \
  https://raw.githubusercontent.com/shanlianyun/flux-panel/main/gost.sql -o gost.sql
umask 077
cat > compose-fork.env <<'EOF'
DB_NAME=flux
DB_USER=flux_user
DB_PASSWORD=替换为高强度随机密码
JWT_SECRET=替换为至少32字节的随机密钥
FRONTEND_PORT=6366
IMAGE_TAG=sha-替换为已发布的完整提交号
EOF
chmod 600 compose-fork.env
```

IPv4 部署使用 `docker-compose-fork-v4.yml`；确认配置并启动：

```bash
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml config
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml pull
docker compose --env-file compose-fork.env -f docker-compose-fork-v4.yml up -d
```

IPv6 环境将上面文件名替换为 `docker-compose-fork-v6.yml`。首次 GHCR 镜像发布及现有部署迁移的详细步骤见[迁移指南](docs/migrate-from-original.md)。

旧版安装入口（仅供已有原作者部署延续原有操作使用）：

```bash
curl -L https://raw.githubusercontent.com/shanlianyun/flux-panel/refs/heads/main/panel_install.sh -o panel_install.sh && chmod +x panel_install.sh && ./panel_install.sh
```

该脚本会继续使用原作者镜像配置；**不要在生产机上将它的“更新”选项当作切换到 fork 的迁移步骤**。现有系统迁移必须按迁移指南显式备份、切换并验证。

节点端：
```bash
curl -L https://raw.githubusercontent.com/shanlianyun/flux-panel/refs/heads/main/install.sh -o install.sh && chmod +x install.sh && ./install.sh

```

默认情况下安装脚本直接从 GitHub 下载，不会通过第三方代理，也不会向 IP 定位服务查询所在地。若你的网络必须经由可信镜像，可在运行前显式设置 `GITHUB_MIRROR_PREFIX`；请自行确认镜像服务的可信度。




#### 默认管理员账号

- **账号**: admin_user
- **密码**: admin_user

> ⚠️ 首次登录后请立即修改默认密码！


## 免责声明

本项目仅供个人学习与研究使用，基于开源项目进行二次开发。

使用本项目所带来的任何风险由使用者自行承担，包括但不限于：

- 配置不当或使用错误导致的服务异常或不可用；
- 使用本项目引发的网络攻击、封禁、滥用等行为；
- 服务器因使用本项目被入侵、渗透、滥用导致的数据泄露、资源消耗或损失；
- 因违反当地法律法规所产生的任何法律责任。

本项目为开源流量转发工具，仅供合法用途。  
使用者应确保其使用行为符合所在国家或地区的法律法规。

作者不对因使用本项目导致的任何法律责任、经济损失或其他后果承担责任。  
禁止将本项目用于任何违法或未经授权的行为，包括但不限于网络攻击、数据窃取、非法访问等。

如不同意上述条款，请勿使用本项目。

作者对因使用本项目所造成的任何直接或间接损失概不负责，也不提供任何形式的担保或技术支持。

请确保在合法、合规、安全的前提下使用本项目。
