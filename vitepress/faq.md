# 常见问题


## 面板安装、迁移与更新

安装、旧版迁移和后续更新使用同一个管理菜单。服务器上执行：

```bash
curl --fail --location --retry 3 https://raw.githubusercontent.com/shanlianyun/flux-panel/main/panel_install.sh -o panel_install.sh && bash panel_install.sh
```

- 新安装选 `1`，必须使用专用空目录。
- 原作者版本迁移选 `7`，并核对旧部署目录；不要先卸载或删除数据卷。
- fork 后续更新选 `2`。该选项不会自动迁移旧版。
- 单独备份选 `4`；恢复中断的迁移/更新选 `8`。

需要 Python 3.8+ 和 Docker Compose v2。迁移指南说明[支持范围、备份及失败恢复](https://github.com/shanlianyun/flux-panel/blob/main/docs/migrate-from-original.md)。如果系统结构或运行配置不受支持，菜单会停止，不要通过重新安装绕过检查。

管理脚本也支持非交互命令，适合维护窗口和自动化：

```bash
bash panel_install.sh install --directory /root/flux-panel
bash panel_install.sh update --directory /root/flux-panel
bash panel_install.sh backup --directory /root/flux-panel
bash panel_install.sh recover --directory /root/flux-panel
bash panel_install.sh help
```

`update` 和 `migrate` 会在替换前备份并执行健康检查；出现中断时使用 `recover`，不要直接卸载或删除数据卷。`--tag` 可指定已经发布且验证过的 `sha-<40位提交号>` 镜像，脚本拒绝 `latest`。

## 节点管理脚本

在面板的“节点管理”页面复制安装命令。新脚本支持明确的子命令，执行完成后不会删除脚本文件：

```bash
# 已有节点升级（不会覆盖 config.json 或 gost.json）
./install.sh update

# 卸载（默认会再次确认）
./install.sh uninstall
./install.sh uninstall -y

# 入口节点屏蔽 http/tls/socks
./install.sh block-protocol

# 查看帮助
./install.sh help
```

旧版数字菜单仍可直接运行 `./install.sh`，但不要用“安装”代替已有节点的 `update`。更新会短暂停止并重启 GOST；建议先选择一台灰度节点验证，再分批更新其他节点。更新失败时不会替换未通过 SHA-256 和版本检查的旧二进制。

公网面板必须先配置 TLS 反向代理，再在安装或更新命令前设置：

```bash
GOST_PANEL_SCHEME=https ./install.sh update
```

面板维护或重启期间，节点本地已经运行的转发不会停止；控制通道和状态上报会暂时中断，面板恢复后节点自动重连并同步有效规则。维护期间不能通过面板执行新的规则变更。

节点升级后可使用以下命令检查：

```bash
/etc/gost/gost -V
systemctl is-active gost
systemctl status gost --no-pager
journalctl -u gost -n 50 --no-pager
```

## 日志查看

### 查看后端服务日志

当后端服务出现问题时，可以通过以下命令查看实时日志：

```bash
docker logs -f springboot-backend
```

按 `Ctrl+C` 退出日志查看

### 查看节点日志

当转发功能出现问题时，可以通过以下命令查看gost服务日志：

```bash
journalctl -u gost -f
```

按 `Ctrl+C` 退出日志查看

---

### 屏蔽 http/tls/socks 协议

⚠️注意：目前已知bug，开启屏蔽后，转发udp会失效，只能转发tcp

屏蔽指定协议的方法，只需要在入口执行。出口无需执行

1️⃣ 编辑配置文件

打开 /etc/gost/config.json，添加以下字段：
```
"http": 1,
"tls": 1,
"socks": 1
```

2️⃣ 重启服务
```
sudo systemctl restart gost
```

3️⃣ 检查状态（可选）
```
sudo systemctl status gost
```

- 说明：
  - 0 或不添加 → 不屏蔽对应协议
  - 1 → 屏蔽对应协议

完整的json文件
```
{
  "addr":"127.0.0.1：6365",
  "secret":"doraemon",
  "http":1,
  "tls":1,
  "socks":1
}
```
