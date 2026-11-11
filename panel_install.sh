#!/bin/bash
set -euo pipefail
export LC_ALL=C
umask 077

REPOSITORY="shanlianyun/flux-panel"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
GITHUB_MIRROR_PREFIX="${GITHUB_MIRROR_PREFIX:-}"
MANAGER_TEMP=""

cleanup() {
  if [[ -n "$MANAGER_TEMP" ]]; then
    rm -rf -- "$MANAGER_TEMP"
  fi
}
trap cleanup EXIT

download() {
  local url="$1" output="$2"
  if [[ -n "$GITHUB_MIRROR_PREFIX" ]]; then
    url="${GITHUB_MIRROR_PREFIX%/}/$url"
  fi
  curl --fail --silent --show-error --location --retry 3 --max-time 120 -o "$output" "$url"
}

prepare_manager() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "错误：需要 Python 3.8+。请先安装 python3 后重试。" >&2
    return 1
  fi
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || {
    echo "错误：需要 Python 3.8+。" >&2
    return 1
  }
  # Repository development uses the matching local helper; downloaded menus
  # fetch their helper at an exact commit, never an unversioned partial file.
  if [[ -e "$SCRIPT_DIR/.git" && -f "$SCRIPT_DIR/panel_manager.py" ]]; then
    MANAGER_PATH="$SCRIPT_DIR/panel_manager.py"
    return
  fi
  MANAGER_TEMP="$(mktemp -d)"
  download "https://api.github.com/repos/$REPOSITORY/commits/main" "$MANAGER_TEMP/revision.json"
  local revision
  revision="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha"])' "$MANAGER_TEMP/revision.json")"
  if [[ ! "$revision" =~ ^[0-9a-f]{40}$ ]]; then
    echo "错误：无法解析管理脚本版本。" >&2
    return 1
  fi
  download "https://raw.githubusercontent.com/$REPOSITORY/$revision/panel_manager.py" "$MANAGER_TEMP/panel_manager.py"
  [[ -s "$MANAGER_TEMP/panel_manager.py" ]] || { echo "错误：下载的管理脚本为空。" >&2; return 1; }
  MANAGER_PATH="$MANAGER_TEMP/panel_manager.py"
}

choose_directory() {
  local suggested="$PWD" entered detected=""
  if [[ "$1" != "install" && ! -f docker-compose.yml ]] && command -v docker >/dev/null 2>&1; then
    detected="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' springboot-backend 2>/dev/null || true)"
    if [[ -n "$detected" && -f "$detected/docker-compose.yml" ]]; then
      suggested="$detected"
    fi
  fi
  read -r -p "部署目录（默认 $suggested）: " entered || return 1
  cd -- "${entered:-$suggested}" || { echo "错误：部署目录不存在。" >&2; return 1; }
}

manage() {
  choose_directory "$1"
  prepare_manager
  python3 "$MANAGER_PATH" "$1" --directory "$PWD"
}

install_reverse_proxy() {
  echo "将运行独立的 Caddy 配置脚本；不会自动迁移面板。"
  MANAGER_TEMP="$(mktemp -d)"
  download "https://raw.githubusercontent.com/$REPOSITORY/main/proxy.sh" "$MANAGER_TEMP/proxy.sh"
  bash "$MANAGER_TEMP/proxy.sh"
}

uninstall_panel() {
  if [[ -n "${1:-}" ]]; then
    cd -- "$1" || { echo "错误：部署目录不存在：$1" >&2; return 1; }
  else
    choose_directory uninstall
  fi
  if [[ ! -f docker-compose.yml || -f .panel-transaction.json ]]; then
    echo "错误：缺少原配置或有未恢复的事务；禁止猜测配置卸载。" >&2
    return 1
  fi
  local confirm
  read -r -p "确认删除面板容器和数据卷？备份不会删除。(输入 DELETE 确认): " confirm || return 1
  if [[ "$confirm" != "DELETE" ]]; then
    echo "已取消卸载。"
    return
  fi
  docker compose --project-directory "$PWD" -f docker-compose.yml down --rmi all --volumes --remove-orphans
  rm -f -- docker-compose.yml gost.sql .env
  echo "卸载完成；panel-backups 和本管理脚本已保留。"
}

show_help() {
  cat <<'HELP'
Flux Panel 管理脚本

推荐：直接运行 `bash panel_install.sh`，按数字菜单选择操作。

用法：
  bash panel_install.sh install [--directory DIR] [--tag sha-<40位提交号>]
  bash panel_install.sh migrate [--directory DIR] [--tag sha-<40位提交号>]
  bash panel_install.sh update [--directory DIR] [--tag sha-<40位提交号>]
  bash panel_install.sh backup [--directory DIR]
  bash panel_install.sh recover [--directory DIR]
  bash panel_install.sh uninstall [--directory DIR]
  bash panel_install.sh proxy

不带参数时打开交互菜单。更新和迁移会先备份，失败时保留恢复记录；不会自动删除本脚本。
HELP
}

show_menu() {
  echo "==============================================="
  echo "       Flux Panel 管理（shanlianyun fork）"
  echo "==============================================="
  echo "1. 安装本 fork（仅空目录/无旧数据卷）"
  echo "2. 更新本 fork（先备份，不修改数据库结构）"
  echo "3. 卸载面板（危险：删除数据卷）"
  echo "4. 导出数据库与配置备份"
  echo "5. 安装并配置反向代理（Caddy）"
  echo "6. 退出"
  echo "7. 旧版迁移到本 fork（保留数据/端口/网络）"
  echo "8. 恢复中断的迁移/更新（不回灌数据库）"
  echo "==============================================="
}

run_operation() {
  local operation="$1" directory="${2:-$PWD}" tag="${3:-${PANEL_IMAGE_TAG:-}}"
  cd -- "$directory" || { echo "错误：部署目录不存在：$directory" >&2; return 1; }
  prepare_manager
  local args=("$MANAGER_PATH" "$operation" --directory "$PWD")
  [[ -n "$tag" ]] && args+=(--tag "$tag")
  python3 "${args[@]}"
}

run_uninstall() {
  local directory="${1:-$PWD}"
  uninstall_panel "$directory"
}

main() {
  local command="${1:-menu}" directory="" tag=""
  if [[ "$command" != "menu" ]]; then
    shift
    case "$command" in
      help|-h|--help)
        show_help
        return 0
        ;;
      proxy)
        [[ $# -eq 0 ]] || { echo "错误：proxy 不接受额外参数。" >&2; return 2; }
        install_reverse_proxy
        return
        ;;
      install|migrate|update|backup|recover|uninstall)
        while [[ $# -gt 0 ]]; do
          case "$1" in
            --directory)
              [[ $# -ge 2 ]] || { echo "错误：--directory 缺少参数。" >&2; return 2; }
              directory="$2"; shift 2
              ;;
            --tag)
              [[ $# -ge 2 ]] || { echo "错误：--tag 缺少参数。" >&2; return 2; }
              tag="$2"; shift 2
              ;;
            *) echo "错误：未知参数：$1" >&2; return 2 ;;
          esac
        done
        directory="${directory:-$PWD}"
        if [[ "$command" == "uninstall" ]]; then
          [[ -z "$tag" ]] || { echo "错误：uninstall 不接受 --tag。" >&2; return 2; }
          run_uninstall "$directory"
        else
          run_operation "$command" "$directory" "$tag"
        fi
        return
        ;;
      *)
        echo "错误：未知命令：$command" >&2
        show_help
        return 2
        ;;
    esac
  fi

  local choice
  while true; do
    show_menu
    read -r -p "请输入选项 (1-8): " choice || return 0
    case "$choice" in
      1) manage install; return ;;
      2) manage update; return ;;
      3) uninstall_panel; return ;;
      4) manage backup; return ;;
      5) install_reverse_proxy; return ;;
      6) echo "已退出；脚本已保留，下次可直接运行 bash $0"; return ;;
      7) manage migrate; return ;;
      8) manage recover; return ;;
      *) echo "无效选项，请输入 1-8。" ;;
    esac
  done
}

main "$@"
