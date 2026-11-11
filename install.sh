#!/bin/bash
# 下载地址及平台选择
RELEASE_BASE_URL="https://github.com/shanlianyun/flux-panel/releases/download/gost-latest"
DOWNLOAD_URL=""
CHECKSUM_URL=""
INSTALL_DIR="/etc/gost"
GITHUB_MIRROR_PREFIX="${GITHUB_MIRROR_PREFIX:-}"
if [[ -n "$GITHUB_MIRROR_PREFIX" ]]; then
    RELEASE_BASE_URL="${GITHUB_MIRROR_PREFIX%/}/${RELEASE_BASE_URL}"
fi

select_gost_asset() {
  case "$(uname -s)/$(uname -m)" in
    Linux/x86_64|Linux/amd64) GOST_ASSET="gost" ;;
    Linux/aarch64|Linux/arm64) GOST_ASSET="gost-linux-arm64" ;;
    *)
      echo "❌ 暂不支持的平台：$(uname -s)/$(uname -m)"
      return 1
      ;;
  esac
  DOWNLOAD_URL="${RELEASE_BASE_URL}/${GOST_ASSET}"
  CHECKSUM_URL="${DOWNLOAD_URL}.sha256"
}

download_and_verify_gost() {
  local destination="$1"
  local checksum_file="${destination}.sha256"
  local expected actual

  if ! curl --fail --location --retry 3 --retry-delay 2 "$DOWNLOAD_URL" -o "$destination"; then
    echo "❌ 下载 gost 失败。"
    rm -f "$destination" "$checksum_file"
    return 1
  fi
  if ! curl --fail --location --retry 3 --retry-delay 2 "$CHECKSUM_URL" -o "$checksum_file"; then
    echo "❌ 下载 SHA-256 校验文件失败，拒绝安装未验证的二进制。"
    rm -f "$destination" "$checksum_file"
    return 1
  fi

  expected=$(awk 'NR == 1 {print $1}' "$checksum_file")
  actual=$(sha256sum "$destination" | awk '{print $1}')
  rm -f "$checksum_file"
  if [[ ! "$expected" =~ ^[[:xdigit:]]{64}$ || "$actual" != "$expected" ]]; then
    echo "❌ gost SHA-256 校验失败，文件未安装。"
    rm -f "$destination"
    return 1
  fi
  chmod +x "$destination"
  if ! "$destination" -V >/dev/null 2>&1; then
    echo "❌ 下载文件无法运行，拒绝替换现有 gost。"
    rm -f "$destination"
    return 1
  fi
}




# 显示菜单。数字菜单是节点日常管理的推荐入口。
show_menu() {
  echo "==============================================="
  echo "              管理脚本"
  echo "==============================================="
  echo "请选择操作："
  echo "1. 安装"
  echo "2. 更新"
  echo "3. 卸载"
  echo "4. 屏蔽 http/tls/socks 协议"
  echo "5. 退出"
  echo "6. 修改面板连接地址 / 迁移 HTTPS"
  echo "==============================================="
}

# 获取用户输入的配置参数
get_config_params() {
  if [[ -z "$SERVER_ADDR" || -z "$SECRET" ]]; then
    echo "请输入配置参数："
    [[ -n "$SERVER_ADDR" ]] || read -r -p "服务器地址: " SERVER_ADDR
    [[ -n "$SECRET" ]] || read -r -p "密钥: " SECRET
  fi
  if [[ -z "$SERVER_ADDR" || -z "$SECRET" ]]; then
    echo "❌ 参数不完整，操作取消。"
    return 1
  fi
}

# 拒绝会改变 JSON 结构的配置值；节点地址和密钥本身不需要这些字符。
validate_config_value() {
  local value="$1"
  case "$value" in
    *'"'*|*\\*|*$'\n'*|*$'\r'*)
      echo "❌ 配置参数包含不支持的引号、反斜杠或换行。"
      return 1
      ;;
  esac
}

validate_panel_scheme() {
  case "${GOST_PANEL_SCHEME:-http}" in
    http|https) ;;
    *) echo "❌ GOST_PANEL_SCHEME 只能是 http 或 https。"; return 1 ;;
  esac
}

normalize_panel_endpoint() {
  case "$SERVER_ADDR" in
    https://*|http://*)
      GOST_PANEL_SCHEME="${SERVER_ADDR%%://*}"
      SERVER_ADDR="${SERVER_ADDR#*://}"
      SERVER_ADDR="${SERVER_ADDR%/}"
      export GOST_PANEL_SCHEME
      ;;
    *://*) echo "❌ 节点入口只支持 http:// 或 https://。"; return 1 ;;
  esac
  case "$SERVER_ADDR" in
    ""|*/*|*\?*|*\#*|*@*|*[[:space:]]*)
      echo "❌ 节点入口必须是主机和端口，不能包含路径、认证信息或查询参数。"
      return 1 ;;
  esac
  validate_panel_scheme
}

# Remove only exact scheme overrides owned/documented by this fork.
remove_legacy_panel_override() {
  local override content
  for override in /etc/systemd/system/gost.service.d/{99-panel-https,99-flux-panel-endpoint}.conf; do
    [[ -f "$override" && ! -L "$override" ]] || continue
    content=$(cat "$override") || return 1
    if [[ "$content" == $'[Service]\nEnvironment=GOST_PANEL_SCHEME=https' ||
          "$content" == $'[Service]\nEnvironment=GOST_PANEL_SCHEME=http' ]]; then
      rm -f -- "$override" || return 1
    fi
  done
}

# Observe this service invocation, never create a second authenticated node connection.
report_panel_connection() {
  local invocation event attempt
  invocation=$(systemctl show gost -p InvocationID --value 2>/dev/null) || invocation=""
  if [[ ! "$invocation" =~ ^[[:xdigit:]]{32}$ ]]; then
    echo "⚠️ 服务已启动，但尚未确认面板连接；请在节点监控查看状态或检查 journalctl -u gost。"
    return 0
  fi
  for attempt in {1..8}; do
    if ! systemctl is-active --quiet gost; then
      echo "❌ GOST 启动后退出，请查看 journalctl -u gost -n 50 --no-pager。"
      return 1
    fi
    event=$(journalctl "_SYSTEMD_INVOCATION_ID=$invocation" -n 40 --no-pager -o cat 2>/dev/null |
      grep -E 'WebSocket连接建立成功|WebSocket连接失败|WebSocket读取消息错误|WebSocket连接已关闭' | tail -n 1)
    case "$event" in
      *WebSocket连接建立成功*) echo "✅ 已连接面板，请在节点监控确认在线。"; return 0 ;;
      *WebSocket连接失败*)
        echo "⚠️ GOST 已启动，但尚未连接面板：$event"
        echo "请检查节点入口协议、证书、WebSocket 反代和节点认证；服务保持运行，不停止本地转发。"
        return 0 ;;
    esac
    [[ "$attempt" == 8 ]] || sleep 2
  done
  echo "⚠️ 服务已启动，但尚未确认面板连接；服务保持运行，请检查节点监控及 journalctl -u gost。"
}


# 解析显式子命令的选项。
parse_options() {
  OPTIND=1
  while getopts "a:s:y" opt; do
    case $opt in
      a) SERVER_ADDR="$OPTARG" ;;
      s) SECRET="$OPTARG" ;;
      y) ASSUME_YES=1 ;;
      *) echo "❌ 无效参数"; return 2 ;;
    esac
  done
  shift $((OPTIND - 1))
  [[ $# -eq 0 ]] || { echo "❌ 未识别参数: $1"; return 2; }
}

show_help() {
  cat <<'HELP'
推荐：直接运行 ./install.sh，按数字菜单选择操作。

用法：
  ./install.sh install -a <http://或https://面板地址> -s <节点密钥>
  ./install.sh update
  ./install.sh uninstall [-y]
  ./install.sh block-protocol
  ./install.sh help

说明：
  install    首次安装并创建 /etc/gost/config.json
  update     只更新已校验的 gost 二进制，不覆盖现有配置
  uninstall  停止服务并删除 /etc/gost（需要确认，-y 可跳过确认）
  block-protocol  在入口节点屏蔽 http/tls/socks 协议
  安装命令中的 https:// 自动启用 HTTPS/WSS，无需另设环境变量
  已有节点迁移地址或 HTTPS：打开数字菜单选 6，需要 Python 3、curl、systemd
  迁移会备份并短暂重启，连接失败会恢复原配置；不要卸载旧节点
  旧的 host:port 参数和 GOST_PANEL_SCHEME 仅保留兼容已有维护流程

直接运行 ./install.sh 会打开数字菜单；脚本不会自动删除自身。
HELP
}

# 安装功能
install_gost() {
  echo "🚀 开始安装 GOST..."
  get_config_params || return $?
  normalize_panel_endpoint || return $?
  validate_config_value "$SERVER_ADDR" || return 1
  validate_config_value "$SECRET" || return 1
  validate_panel_scheme || return 1
  select_gost_asset || return 1

  mkdir -p "$INSTALL_DIR"
  local new_binary
  new_binary=$(mktemp "${INSTALL_DIR}/gost.new.XXXXXX") || return 1
  if ! download_and_verify_gost "$new_binary"; then
    return 1
  fi

  if systemctl list-units --full -all | grep -Fq "gost.service"; then
    echo "🔍 检测到已存在的 gost 服务"
    systemctl stop gost 2>/dev/null && echo "🛑 停止服务"
    systemctl disable gost 2>/dev/null && echo "🚫 禁用自启"
  fi

  mv -f "$new_binary" "$INSTALL_DIR/gost"
  echo "✅ 下载并校验完成"
  echo "🔎 gost 版本：$($INSTALL_DIR/gost -V)"

  CONFIG_FILE="$INSTALL_DIR/config.json"
  cat > "$CONFIG_FILE" <<CONFIG
{
  "addr": "$SERVER_ADDR",
  "secret": "$SECRET"
}
CONFIG

  GOST_CONFIG="$INSTALL_DIR/gost.json"
  if [[ -f "$GOST_CONFIG" ]]; then
    echo "⏭️ 跳过配置文件: gost.json (已存在)"
  else
    printf '{}\n' > "$GOST_CONFIG"
  fi
  chmod 600 "$INSTALL_DIR"/*.json

  SERVICE_FILE="/etc/systemd/system/gost.service"
  cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Gost Proxy Service
After=network.target

[Service]
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/gost
Environment=GOST_PANEL_SCHEME=${GOST_PANEL_SCHEME:-http}
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
SERVICE

  remove_legacy_panel_override || return $?
  systemctl daemon-reload || return $?
  local effective_scheme
  effective_scheme=$(systemctl show gost -p Environment --value 2>/dev/null |
    grep -oE 'GOST_PANEL_SCHEME=[^ ]+' | tail -n 1)
  if [[ -n "$effective_scheme" && "$effective_scheme" != "GOST_PANEL_SCHEME=${GOST_PANEL_SCHEME:-http}" ]]; then
    echo "❌ systemd 的其他配置覆盖了节点连接协议，请检查 gost.service.d；未启动新服务。"
    return 1
  fi
  systemctl enable gost || return $?
  systemctl start gost || return $?
  echo "🔄 检查服务状态..."
  if systemctl is-active --quiet gost; then
    echo "✅ GOST 服务已启动并设置为开机启动；配置目录: $INSTALL_DIR"
    report_panel_connection
  else
    echo "❌ gost 服务启动失败，请执行：journalctl -u gost -n 50 --no-pager"
    return 1
  fi
}

# 更新只替换二进制；不会重写 config.json、gost.json 或 systemd 安全选项。
update_gost() {
  echo "🔄 开始更新 GOST..."
  [[ -d "$INSTALL_DIR" ]] || { echo "❌ GOST 未安装，请先执行 install。"; return 1; }
  [[ -x "$INSTALL_DIR/gost" ]] || { echo "❌ 未找到现有 gost 二进制，请先执行 install。"; return 1; }
  validate_panel_scheme || return 1
  select_gost_asset || return 1
  echo "⬇️ 下载最新版本..."
  local new_binary
  new_binary=$(mktemp "${INSTALL_DIR}/gost.new.XXXXXX") || return 1
  if ! download_and_verify_gost "$new_binary"; then
    return 1
  fi

  local previous_binary
  previous_binary=$(mktemp "${INSTALL_DIR}/gost.rollback.XXXXXX") || return 1
  if ! cp -p "$INSTALL_DIR/gost" "$previous_binary"; then
    rm -f "$previous_binary"
    echo "❌ 无法保存旧 gost 二进制，取消更新。"
    return 1
  fi
  if systemctl list-units --full -all | grep -Fq "gost.service"; then
    local stop_status
    if systemctl stop gost; then
      :
    else
      stop_status=$?
      rm -f "$previous_binary"
      return "$stop_status"
    fi
  fi
  mv -f "$new_binary" "$INSTALL_DIR/gost"
  echo "🔎 新版本：$($INSTALL_DIR/gost -V)"
  if [[ -n "${GOST_PANEL_SCHEME+x}" && -f /etc/systemd/system/gost.service ]]; then
    remove_legacy_panel_override || return $?
    if grep -q '^Environment=GOST_PANEL_SCHEME=' /etc/systemd/system/gost.service; then
      sed -i "s#^Environment=GOST_PANEL_SCHEME=.*#Environment=GOST_PANEL_SCHEME=$GOST_PANEL_SCHEME#" /etc/systemd/system/gost.service
    else
      sed -i "/^ExecStart=/a Environment=GOST_PANEL_SCHEME=$GOST_PANEL_SCHEME" /etc/systemd/system/gost.service
    fi
    systemctl daemon-reload || return $?
  fi
  local start_status
  if systemctl start gost; then
    :
  else
    start_status=$?
    mv -f "$previous_binary" "$INSTALL_DIR/gost"
    systemctl start gost >/dev/null 2>&1 || true
    echo "❌ 新 gost 无法启动，已恢复旧二进制。"
    return "$start_status"
  fi
  if ! systemctl is-active --quiet gost; then
    start_status=1
    mv -f "$previous_binary" "$INSTALL_DIR/gost"
    systemctl start gost >/dev/null 2>&1 || true
    echo "❌ gost 服务启动失败，请执行：journalctl -u gost -n 50 --no-pager"
    echo "ℹ️ 已恢复旧二进制，请检查服务日志。"
    return "$start_status"
  fi
  rm -f "$previous_binary"
  echo "✅ 更新完成，服务已重新启动；现有节点配置未修改。"
}

# 在入口节点屏蔽协议，不影响其他配置字段。
block_protocol() {
  local config_file="$INSTALL_DIR/config.json" temporary
  [[ -f "$config_file" ]] || { echo "❌ 未找到 $config_file，请先安装 GOST。"; return 1; }
  command -v python3 >/dev/null 2>&1 || { echo "❌ block-protocol 需要 Python 3。"; return 1; }
  temporary=$(mktemp "${config_file}.new.XXXXXX") || return 1
  if ! python3 - "$config_file" "$temporary" <<'PY'
import json
import os
import sys

source, destination = sys.argv[1:]
with open(source, encoding="utf-8") as handle:
    config = json.load(handle)
for key in ("http", "tls", "socks"):
    config[key] = 1
with open(destination, "w", encoding="utf-8") as handle:
    json.dump(config, handle, ensure_ascii=False, separators=(",", ":"))
    handle.write("\n")
os.chmod(destination, 0o600)
PY
  then
    rm -f "$temporary"
    echo "❌ config.json 不是有效 JSON，未修改配置。"
    return 1
  fi
  mv -f "$temporary" "$config_file"
  systemctl restart gost || return $?
  echo "✅ 已屏蔽 http/tls/socks 协议并重启 gost。"
}

# Read only the relevant effective setting; never print the service's other secrets.
panel_service_scheme() {
  local environment
  environment=$(systemctl show gost -p Environment --value) || return 1
  python3 - "$environment" <<'PY'
import shlex
import sys
scheme = "http"
for value in shlex.split(sys.argv[1]):
    if value.startswith("GOST_PANEL_SCHEME="):
        scheme = value.split("=", 1)[1]
if scheme not in ("http", "https"):
    sys.exit(1)
print(scheme)
PY
}

# This explicit maintenance transaction is separate from ordinary offline startup.
migrate_panel_endpoint() (
  umask 077
  local config="$INSTALL_DIR/config.json"
  local dropdir="/etc/systemd/system/gost.service.d"
  local override="$dropdir/99-flux-panel-endpoint.conf"
  local lock="$INSTALL_DIR/.endpoint-migration.lock"
  local backup="" config_new="" override_new="" changed=0 committed=0 restarted=0 made_dropdir=0
  local endpoint parsed scheme address old_scheme old_address confirm envfiles response old_invocation

  command -v python3 >/dev/null && command -v curl >/dev/null || {
    echo "❌ 此操作需要 Python 3 和 curl；未修改节点。"; return 1;
  }
  [[ -d "$INSTALL_DIR" && ! -L "$INSTALL_DIR" && -f "$config" && ! -L "$config" ]] || {
    echo "❌ 需要已安装节点的常规 config.json；未修改节点。"; return 1;
  }
  mkdir "$lock" 2>/dev/null || { echo "❌ 另一个迁移正在进行，或存在未处理的迁移锁：$lock"; return 1; }

  finish_endpoint_migration() {
    local status=$? restored=1
    trap - EXIT INT TERM HUP
    if [[ "$changed" == 1 && "$committed" == 0 ]]; then
      # Restore via same-filesystem renames, including the original file's absence.
      cp -p "$backup/config.json" "$config_new" && mv -f "$config_new" "$config" || restored=0
      if [[ -f "$backup/endpoint.conf" ]]; then
        cp -p "$backup/endpoint.conf" "$override_new" && mv -f "$override_new" "$override" || restored=0
      else
        rm -f -- "$override" || restored=0
      fi
      if [[ "$restored" == 1 ]] && systemctl daemon-reload; then
        if [[ "$restarted" == 0 ]]; then
          echo "ℹ️ 已恢复原配置，原服务未重启。"
        elif systemctl restart gost && systemctl is-active --quiet gost; then
          echo "ℹ️ 已恢复原配置并重启原服务；请验证原有转发。"
        else
          echo "❌ 原配置已恢复，但恢复服务失败；请检查 journalctl -u gost 和备份：$backup"
        fi
      else
        echo "❌ 配置回退或 daemon-reload 失败；请使用备份人工恢复：$backup"
      fi
      status=1
    fi
    [[ -z "$config_new" ]] || rm -f -- "$config_new"
    [[ -z "$override_new" ]] || rm -f -- "$override_new"
    [[ "$made_dropdir" == 0 || "$committed" == 1 ]] || rmdir "$dropdir" 2>/dev/null
    rmdir "$lock" 2>/dev/null
    return "$status"
  }
  trap finish_endpoint_migration EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP

  if [[ -L "$dropdir" || -L "$override" || ( -e "$override" && ! -f "$override" ) ]]; then
    echo "❌ 连接设置路径不是常规文件/目录，拒绝覆盖。"; return 1
  fi
  if [[ -f "$override" ]]; then
    local content
    content=$(cat "$override") || return 1
    if [[ "$content" != $'[Service]\nEnvironment=GOST_PANEL_SCHEME=https' &&
          "$content" != $'[Service]\nEnvironment=GOST_PANEL_SCHEME=http' ]]; then
      echo "❌ 连接设置文件含自定义内容，拒绝覆盖：$override"; return 1
    fi
  fi
  envfiles=$(systemctl show gost -p EnvironmentFiles --value) || return 1
  [[ -z "$envfiles" ]] || { echo "❌ 服务使用自定义 EnvironmentFile，请先人工核对协议覆盖；未修改节点。"; return 1; }
  old_scheme=$(panel_service_scheme) || { echo "❌ 无法读取原服务协议。"; return 1; }
  old_address=$(python3 - "$config" <<'PY'
import json
import sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        config = json.load(handle)
    addr = config["addr"]
    if not isinstance(config.get("secret"), str) or not config["secret"]:
        raise ValueError()
    if not isinstance(addr, str) or not addr or any(c.isspace() for c in addr) or any(c in addr for c in "/@?#\\\""):
        raise ValueError()
    print(addr)
except (ValueError, KeyError, TypeError, OSError):
    sys.exit(1)
PY
  ) || { echo "❌ 原 config.json 缺少有效地址/密钥或不是有效 JSON；未修改节点。"; return 1; }

  echo "当前入口：$old_scheme://$old_address"
  read -r -p "新的完整入口（例如 https://node.example.com:443）: " endpoint || return 1
  parsed=$(python3 - "$endpoint" <<'PY'
import ipaddress
import re
import sys
from urllib.parse import urlsplit
try:
    raw = sys.argv[1]
    url = urlsplit(raw)
    if (url.scheme not in ("http", "https") or not raw.startswith(url.scheme + "://")
            or any(c.isspace() or ord(c) < 32 for c in raw)
            or any(c in raw for c in "?#\\") or url.path or url.username is not None
            or url.password is not None or not url.hostname or not url.port or not 1 <= url.port <= 65535):
        raise ValueError()
    host = url.hostname
    if ":" in host:
        ipaddress.IPv6Address(host)
        if "%" in host:
            raise ValueError()
        host = "[" + host + "]"
    elif not (len(host) <= 253 and all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label) for label in host.split("."))):
        raise ValueError()
    print(url.scheme)
    print(host + ":" + str(url.port))
except ValueError:
    sys.exit(1)
PY
  ) || { echo "❌ 请输入明确的 http(s)://主机:端口，不含路径、认证、查询或片段；未修改节点。"; return 1; }
  scheme=${parsed%%$'\n'*}
  address=${parsed#*$'\n'}
  endpoint="$scheme://$address"
  echo "新入口：$endpoint"
  echo "密钥、本地转发配置及二进制保持不变；将重启 gost，现有连接可能短暂中断。"
  if [[ "$scheme" == http ]]; then
    echo "⚠️ HTTP/WS 不加密，只适合受控网络。"
    read -r -p "确认使用明文入口？输入 HTTP 继续: " confirm || return 1
    [[ "$confirm" == HTTP ]] || { echo "已取消，未修改节点。"; return 0; }
  else
    read -r -p "确认迁移？(y/N): " confirm || return 1
    [[ "$confirm" == y || "$confirm" == Y ]] || { echo "已取消，未修改节点。"; return 0; }
  fi
  echo "🔎 检查证书和后端入口（不跳过 TLS 校验）..."
  response=$(curl --disable --fail --silent --show-error --proto '=http,https' --connect-timeout 8 --max-time 15 \
    --write-out $'\n%{http_code}' "$endpoint/flow/test") || {
    echo "❌ 新入口网络或证书检查失败；原服务未重启。"; return 1;
  }
  [[ "$response" == $'test\n200' ]] || { echo "❌ 新入口未返回预期后端响应；请检查反代，原服务未重启。"; return 1; }

  backup=$(mktemp -d "$INSTALL_DIR/endpoint-backup.XXXXXX") || return 1
  chmod 700 "$backup" || return 1
  cp -p "$config" "$backup/config.json" || return 1
  if [[ -f "$override" ]]; then cp -p "$override" "$backup/endpoint.conf" || return 1; fi
  echo "备份目录：${backup}（含节点密钥，请勿公开）"
  config_new=$(mktemp "$INSTALL_DIR/config.endpoint.XXXXXX") || return 1
  cp -p "$backup/config.json" "$config_new" || return 1
  python3 - "$backup/config.json" "$config_new" "$address" <<'PY'
import json
import os
import sys
source, target, address = sys.argv[1:]
with open(source, encoding="utf-8") as handle:
    config = json.load(handle)
config["addr"] = address
with open(target, "w", encoding="utf-8") as handle:
    json.dump(config, handle, ensure_ascii=False)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
  [[ $? == 0 ]] || { echo "❌ 无法准备新配置；原服务未重启。"; return 1; }
  if [[ ! -d "$dropdir" ]]; then mkdir "$dropdir" || return 1; made_dropdir=1; fi
  override_new=$(mktemp "$dropdir/.endpoint.XXXXXX") || return 1
  printf '[Service]\nEnvironment=GOST_PANEL_SCHEME=%s\n' "$scheme" > "$override_new" || return 1
  old_invocation=$(systemctl show gost -p InvocationID --value) || return 1
  changed=1
  mv -f "$config_new" "$config" && mv -f "$override_new" "$override" || return 1
  systemctl daemon-reload || return 1
  [[ "$(panel_service_scheme)" == "$scheme" ]] || {
    echo "❌ 自定义 systemd 设置覆盖了新协议；取消迁移。"; return 1;
  }
  restarted=1
  systemctl restart gost || { echo "❌ 新配置启动失败，正在回退。"; return 1; }
  local invocation event events attempt current websocket_scheme=ws
  [[ "$scheme" != https ]] || websocket_scheme=wss
  for attempt in {1..15}; do
    systemctl is-active --quiet gost || { echo "❌ 新服务未保持运行，正在回退。"; return 1; }
    invocation=$(systemctl show gost -p InvocationID --value) || return 1
    if [[ "$invocation" =~ ^[[:xdigit:]]{32}$ && "$invocation" != "$old_invocation" ]]; then
      events=$(journalctl "_SYSTEMD_INVOCATION_ID=$invocation" --no-pager -o cat 2>/dev/null |
        grep -E 'WebSocket连接地址:|WebSocket连接建立成功|WebSocket连接失败|WebSocket读取消息错误|WebSocket连接已关闭')
      event=$(printf '%s\n' "$events" | tail -n 1)
      case "$event" in
        *WebSocket连接建立成功*)
          current=$(systemctl show gost -p InvocationID --value) || return 1
          if [[ "$current" == "$invocation" ]] && systemctl is-active --quiet gost &&
            printf '%s\n' "$events" | grep -Fq "WebSocket连接地址: $websocket_scheme://$address/system-info?type=1&version="; then
            committed=1
            echo "✅ 迁移完成，节点已通过新入口连接面板。请再验证节点在线、实际转发和流量上报。"
            return 0
          fi ;;
        *WebSocket连接失败*) echo "❌ 新入口 WebSocket 握手失败，正在回退；请检查证书、反代与节点认证。"; return 1 ;;
      esac
    fi
    [[ "$attempt" == 15 ]] || sleep 2
  done
  echo "❌ 未确认新服务连接面板，正在回退。"
  return 1
)

# 卸载功能
uninstall_gost() {
  echo "🗑️ 开始卸载 GOST..."
  local confirm
  if [[ "${ASSUME_YES:-0}" == "1" ]]; then
    confirm="y"
  else
    read -r -p "确认卸载 GOST 吗？此操作将删除所有相关文件 (y/N): " confirm
  fi
  if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "❌ 取消卸载"
    return 0
  fi

  if systemctl list-units --full -all | grep -Fq "gost.service"; then
    systemctl stop gost 2>/dev/null
    systemctl disable gost 2>/dev/null
  fi
  rm -f "/etc/systemd/system/gost.service"
  remove_legacy_panel_override || return $?
  rm -rf "$INSTALL_DIR"
  systemctl daemon-reload
  echo "✅ 卸载完成"
}

# 主逻辑
main() {
  local command="${1:-menu}"
  if [[ "$command" != "menu" ]]; then
    case "$command" in
      install|update|uninstall|block-protocol|help) shift ;;
      -h|--help) show_help; return 0 ;;
      -a|-s) command="install" ;;
      *) echo "❌ 未知命令: $command"; show_help; return 2 ;;
    esac
  fi

  case "$command" in
    help) show_help ;;
    install) parse_options "$@" || return $?; install_gost ;;
    update) parse_options "$@" || return $?; update_gost ;;
    uninstall) parse_options "$@" || return $?; uninstall_gost ;;
    block-protocol) parse_options "$@" || return $?; block_protocol ;;
    menu)
      while true; do
        show_menu
        read -r -p "请输入选项 (1-6): " choice || return 1
        case "$choice" in
          1) install_gost; return $? ;;
          2) update_gost; return $? ;;
          3) uninstall_gost; return $? ;;
          4) block_protocol; return $? ;;
          5) echo "👋 退出脚本"; return 0 ;;
          6) migrate_panel_endpoint; return $? ;;
          *) echo "❌ 无效选项，请输入 1-6" ;;
        esac
      done
      ;;
  esac
}

# 执行主函数
main "$@"
