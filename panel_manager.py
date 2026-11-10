#!/usr/bin/env python3
"""Conservative, local-only Compose deployment transactions (Python stdlib only)."""

import argparse
import copy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time


REPO = "shanlianyun/flux-panel"
IMAGES = {s: "ghcr.io/" + REPO + "-" + s for s in ("backend", "frontend")}
CONTAINERS = {"mysql": "gost-mysql", "backend": "springboot-backend", "frontend": "vite-frontend"}
BOUNDED_LOGGING = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
# Required write contract of gost.sql. Older schemas need a separately reviewed
# migration; an installer must not guess how to drop or transform their data.
SCHEMA = {
    "forward": "id:int user_id:int user_name:varchar name:varchar tunnel_id:int in_port:int out_port:int remote_addr:longtext strategy:varchar interface_name:varchar in_flow:bigint out_flow:bigint created_time:bigint updated_time:bigint status:int inx:int",
    "node": "id:int name:varchar secret:varchar ip:longtext server_ip:varchar port_sta:int port_end:int version:varchar created_time:bigint updated_time:bigint status:int",
    "speed_limit": "id:int name:varchar speed:int tunnel_id:int tunnel_name:varchar created_time:bigint updated_time:bigint status:int",
    "statistics_flow": "id:int user_id:int flow:bigint total_flow:bigint time:varchar created_time:bigint",
    "tunnel": "id:int name:varchar traffic_ratio:decimal in_node_id:int in_ip:varchar out_node_id:int out_ip:varchar type:int protocol:varchar flow:int tcp_listen_addr:varchar udp_listen_addr:varchar interface_name:varchar created_time:bigint updated_time:bigint status:int",
    "user": "id:int user:varchar pwd:varchar role_id:int exp_time:bigint flow:bigint in_flow:bigint out_flow:bigint flow_reset_time:bigint num:int created_time:bigint updated_time:bigint status:int",
    "user_tunnel": "id:int user_id:int tunnel_id:int speed_id:int num:int flow:bigint in_flow:bigint out_flow:bigint flow_reset_time:bigint exp_time:bigint status:int",
    "vite_config": "id:int name:varchar value:varchar time:bigint",
}


class ManagerError(Exception):
    pass


def run(args, *, output=None, timeout=120, env=None):
    try:
        result = subprocess.run(args, stdout=output if output is not None else subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise ManagerError("外部命令无法运行或超时；未显示可能含凭据的命令输出。") from None
    if result.returncode:
        # Compose/mysql errors can echo environment values and complete SQL.
        raise ManagerError("外部命令失败；请检查 Docker、权限、镜像发布和网络。原始输出已隐藏以保护凭据。")
    return result.stdout if output is None else ""


def fetch(url):
    prefix = os.environ.get("GITHUB_MIRROR_PREFIX", "").rstrip("/")
    if prefix:
        url = prefix + "/" + url
    return run(["curl", "--fail", "--silent", "--show-error", "--location", "--retry", "3", url])


def resolve_tag(tag):
    if not tag:
        revision = json.loads(fetch("https://api.github.com/repos/" + REPO + "/commits/main"))["sha"]
        tag = "sha-" + revision
    if not re.fullmatch(r"sha-[0-9a-f]{40}", tag):
        raise ManagerError("镜像必须使用 sha-<40位小写提交号>，不接受 latest。")
    return tag


def map_strings(value, transform):
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, list):
        return [map_strings(item, transform) for item in value]
    if isinstance(value, dict):
        return {key: map_strings(item, transform) for key, item in value.items()}
    return value


def materialized(config):
    # Compose JSON encodes literal dollars as $$. Work with real runtime values
    # internally, then escape exactly once when saving another Compose input.
    return map_strings(config, lambda value: value.replace("$$", "$"))


def atomic_write(path, content):
    fd, name = tempfile.mkstemp(prefix=".panel-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content if isinstance(content, bytes) else content.encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_config(path, config):
    atomic_write(path, json.dumps(map_strings(config, lambda value: value.replace("$", "$$")), ensure_ascii=True, indent=2) + "\n")


def confirm(message):
    print(message, flush=True)
    try:
        return input("确认继续请输入 yes（其他输入取消）: ").strip() == "yes"
    except EOFError:
        return False


class Manager:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        if not self.directory.is_dir():
            raise ManagerError("部署目录不存在。")
        self.source = self.directory / "docker-compose.yml"
        self.env = self.directory / ".env"
        self.marker = self.directory / ".panel-transaction.json"
        self.backup_root = self.directory / "panel-backups"
        self.project = None

    def prerequisites(self):
        if any(k.startswith("COMPOSE_") for k in os.environ):
            raise ManagerError("请先取消外部 COMPOSE_* 环境变量；仅支持单文件标准部署。")
        host = os.environ.get("DOCKER_HOST", "unix://local")
        endpoint = json.loads(run(["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"]))
        if not host.startswith("unix://") or not endpoint.startswith("unix://"):
            raise ManagerError("不支持远程 Docker；请在部署服务器本机运行。")
        run(["docker", "compose", "version", "--short"])

    def compose(self, file, *args, timeout=120):
        command = ["docker", "compose", "--project-directory", str(self.directory)]
        if self.project:
            command += ["--project-name", self.project]
        if self.env.is_file():
            command += ["--env-file", str(self.env)]
        return run(command + ["-f", str(file)] + list(args), timeout=timeout)

    def inspect(self, name):
        return json.loads(run(["docker", "inspect", name]))[0]

    def discover(self, require_health=True):
        if not self.source.is_file() or not self.env.is_file() or self.source.is_symlink() or self.env.is_symlink():
            raise ManagerError("请指定包含普通 docker-compose.yml 和 .env 的原部署目录。")
        config = materialized(json.loads(self.compose(self.source, "config", "--format", "json")))
        self.project = config.get("name")
        if not self.project or not isinstance(config.get("services"), dict):
            raise ManagerError("需要支持 config --format json 的 Docker Compose v2。")
        containers = {}
        for service, name in CONTAINERS.items():
            spec = config["services"].get(service, {})
            if spec.get("container_name") != name or spec.get("profiles") or spec.get("build"):
                raise ManagerError("不支持此部署：需要标准 mysql/backend/frontend 服务和容器名，且不能使用 build/profiles。")
            container = self.inspect(name)
            labels = container["Config"].get("Labels") or {}
            if (not container["State"]["Running"] or
                    labels.get("com.docker.compose.project") != self.project or
                    labels.get("com.docker.compose.service") != service or
                    Path(labels.get("com.docker.compose.project.working_dir", "/")).resolve() != self.directory):
                raise ManagerError("运行中的容器与指定部署目录/Compose 项目不一致。")
            files = labels.get("com.docker.compose.project.config_files", str(self.source)).split(",")
            if len(files) != 1 or (Path(files[0]) != self.source and
                                   self.backup_root.resolve() not in Path(files[0]).resolve().parents):
                raise ManagerError("不支持多文件或非标准 Compose 部署。")
            actual_env = dict(item.split("=", 1) for item in container["Config"].get("Env", []) if "=" in item)
            for key, value in spec.get("environment", {}).items():
                if value is not None and actual_env.get(key) != str(value):
                    raise ManagerError("配置与容器环境不一致；请先核对原 .env，禁止猜测凭据。")
            expected_ports = sorted((str(p["target"]) + "/" + p.get("protocol", "tcp"),
                                     str(p.get("published", "")), p.get("host_ip", "") or "0.0.0.0")
                                    for p in spec.get("ports", []))
            actual_ports = sorted((key, binding["HostPort"], binding.get("HostIp") or "0.0.0.0")
                                  for key, bindings in (container["HostConfig"].get("PortBindings") or {}).items()
                                  for binding in (bindings or []))
            if expected_ports != actual_ports:
                raise ManagerError("配置与容器端口不一致；停止操作以保留原访问方式。")
            if len(spec.get("volumes", [])) != len(container["Mounts"]):
                raise ManagerError("存在配置中没有记录的容器挂载，停止操作以避免丢失挂载。")
            for volume in spec.get("volumes", []):
                expected_source = volume["source"]
                if volume["type"] == "volume":
                    expected_source = config.get("volumes", {}).get(expected_source, {}).get("name", expected_source)
                if not any(m["Destination"] == volume["target"] and m["Type"] == volume["type"] and
                           m.get("Name" if volume["type"] == "volume" else "Source") == expected_source and
                           m.get("RW", True) == (not volume.get("read_only", False))
                           for m in container["Mounts"]):
                    raise ManagerError("配置与容器数据挂载不一致。")
            expected_networks = {config["networks"][key]["name"]: settings or {}
                                 for key, settings in spec.get("networks", {}).items()}
            actual_networks = container.get("NetworkSettings", {}).get("Networks", {})
            if not expected_networks or set(expected_networks) != set(actual_networks):
                raise ManagerError("配置与实际网络不一致；请先将手动连接的反代网络写入原 Compose。")
            for network, settings in expected_networks.items():
                actual = actual_networks[network]
                aliases = set(actual.get("Aliases") or []) - {name, service, container["Id"][:12]}
                ipam = actual.get("IPAMConfig") or {}
                if (aliases != set(settings.get("aliases") or []) or
                        ipam.get("IPv4Address", "") != settings.get("ipv4_address", "") or
                        ipam.get("IPv6Address", "") != settings.get("ipv6_address", "")):
                    raise ManagerError("配置与实际网络别名/静态地址不一致，停止操作。")
            if service == "backend" and require_health:
                healthcheck = spec.get("healthcheck") or {}
                if (healthcheck.get("disable") or not healthcheck.get("test") or
                        healthcheck["test"][0] == "NONE" or
                        container["State"].get("Health", {}).get("Status") != "healthy"):
                    raise ManagerError("原后端需要配置有效健康检查且已健康，才能保证回退验证可用。")
            current_id = json.loads(run(["docker", "image", "inspect", spec["image"]]))[0]["Id"]
            if current_id != container["Image"]:
                raise ManagerError("原镜像标签已变化，不能安全回退；请先人工核对镜像。")
            containers[service] = container
        db = config["services"]["mysql"]["environment"]
        backend = config["services"]["backend"]["environment"]
        if (not all(db.get(k) for k in ("MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD")) or
                backend.get("DB_HOST") != "mysql" or backend.get("DB_NAME") != db["MYSQL_DATABASE"] or
                backend.get("DB_USER") != db["MYSQL_USER"] or backend.get("DB_PASSWORD") != db["MYSQL_PASSWORD"] or
                not backend.get("JWT_SECRET")):
            raise ManagerError("不支持外部数据库或不一致的数据库/JWT 配置。")
        if not any(m["Destination"] == "/var/lib/mysql" for m in containers["mysql"]["Mounts"]):
            raise ManagerError("数据库没有持久数据挂载，拒绝迁移。")
        return config, containers

    def mysql(self, query):
        return run(["docker", "exec", "gost-mysql", "sh", "-c",
                    'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B "$MYSQL_DATABASE" -e "$1"', "sh", query])

    def check_schema(self):
        self.check_database()
        rows = self.mysql("SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,IFNULL(COLUMN_DEFAULT,'NULL'),EXTRA "
                          "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()")
        columns = {}
        for line in rows.splitlines():
            fields = line.split("\t")
            if len(fields) != 6:
                raise ManagerError("无法读取数据库 schema。")
            columns[(fields[0], fields[1])] = fields[2:]
        for table, description in SCHEMA.items():
            expected = dict(item.split(":") for item in description.split())
            for column, type_name in expected.items():
                actual = columns.get((table, column))
                if not actual or actual[0].split("(")[0] != type_name or "unsigned" in actual[0]:
                    raise ManagerError("数据库 schema 不兼容：" + table + "." + column + "；未修改任何表。")
                if column == "id" and "auto_increment" not in actual[3]:
                    raise ManagerError("数据库 schema 缺少自增主键：" + table)
            for (actual_table, column), values in columns.items():
                if actual_table == table and column not in expected and values[1:3] == ["NO", "NULL"] and "auto_increment" not in values[3]:
                    raise ManagerError("数据库 schema 存在额外必填字段：" + table + "." + column)

    def check_database(self):
        if not self.mysql("SELECT VERSION()").strip().startswith("5.7."):
            raise ManagerError("仅支持现有 MySQL 5.7；本操作不升级数据库。")
        engines = self.mysql("SELECT DISTINCT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")
        if set(engines.split()) != {"InnoDB"}:
            raise ManagerError("数据库包含非 InnoDB 表，不能保证事务备份。")

    def check_space(self, database_bytes):
        docker_root = Path(run(["docker", "info", "--format", "{{.DockerRootDir}}"]).strip())
        if not docker_root.is_dir():
            raise ManagerError("无法检查本机 Docker 存储目录的剩余空间；请在服务器本机以适当权限运行。")
        # Allow dump expansion and a minimum image/extraction reserve, but this
        # is only preflight, not a guarantee of image size or concurrent usage.
        for path, minimum in ((self.directory, max(1024**3, database_bytes * 3 + 256 * 1024**2)),
                              (docker_root, 2 * 1024**3)):
            if shutil.disk_usage(path).free < minimum:
                raise ManagerError("剩余空间不足 (space)：" + str(path) + "；停止操作。")

    def backup(self, config, containers):
        if self.backup_root.is_symlink():
            raise ManagerError("备份目录不能是符号链接。")
        self.backup_root.mkdir(mode=0o700, exist_ok=True)
        if self.backup_root.stat().st_mode & 0o077:
            raise ManagerError("请先将 panel-backups 权限设为 700。")
        backup = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=str(self.backup_root)))
        atomic_write(backup / "docker-compose.yml", self.source.read_bytes())
        atomic_write(backup / ".env", self.env.read_bytes())
        if (self.directory / "gost.sql").is_file():
            atomic_write(backup / "gost.sql", (self.directory / "gost.sql").read_bytes())
        rollback = copy.deepcopy(config)
        for service in ("backend", "frontend"):
            rollback["services"][service]["image"] = containers[service]["Image"]
        write_config(backup / "rollback.json", rollback)
        write_config(backup / "effective.json", config)
        atomic_write(backup / "images.json", json.dumps({s: c["Image"] for s, c in containers.items()}))
        partial = backup / "database.sql.partial"
        with partial.open("w") as stream:
            run(["docker", "exec", "gost-mysql", "sh", "-c",
                 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers --hex-blob --databases "$MYSQL_DATABASE"'],
                output=stream, timeout=1800)
            stream.flush()
            os.fsync(stream.fileno())
        if not partial.stat().st_size:
            raise ManagerError("数据库备份为空；停止操作。")
        partial.rename(backup / "database.sql")
        print("备份已完成（含敏感数据，请另存到服务器外）：" + str(backup), flush=True)
        return backup

    def start_service(self, file, service):
        self.compose(file, "up", "-d", "--no-deps", "--force-recreate", "--pull", "never", service, timeout=300)

    def wait_backend(self):
        for _ in range(90):
            state = self.inspect("springboot-backend")["State"]
            health = state.get("Health", {}).get("Status")
            if state["Running"] and health == "healthy":
                return
            if not state["Running"] or health == "unhealthy":
                break
            time.sleep(2)
        raise ManagerError("后端健康检查失败。")

    def check_frontend(self):
        if not self.inspect("vite-frontend")["State"]["Running"]:
            raise ManagerError("前端未运行。")
        for attempt in range(3):
            try:
                run(["docker", "exec", "vite-frontend", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1/"], timeout=30)
                return
            except ManagerError:
                if attempt == 2:
                    raise
                time.sleep(1)

    def candidate(self, config, tag):
        candidate = copy.deepcopy(config)
        for service in IMAGES:
            candidate["services"][service]["image"] = IMAGES[service] + ":" + tag
            candidate["services"][service]["logging"] = copy.deepcopy(BOUNDED_LOGGING)
        env = candidate["services"]["backend"].setdefault("environment", {})
        for key, value in {"LOG_FILE_MAX_SIZE": "20MB", "LOG_FILE_MAX_HISTORY": "7", "LOG_FILE_TOTAL_SIZE_CAP": "200MB"}.items():
            env.setdefault(key, value)
        candidate["services"]["backend"]["healthcheck"] = {
            "test": ["CMD", "wget", "-q", "--spider", "http://localhost:6365/flow/test"],
            "interval": "10s", "timeout": "5s", "retries": 9, "start_period": "60s"}
        return candidate

    def install(self, tag):
        allowed = {"panel_install.sh", "panel_manager.py", ".panel.lock"}
        if any(path.name not in allowed for path in self.directory.iterdir()):
            raise ManagerError("目录非空或已有部署文件；仅允许管理脚本，请使用新的空目录或迁移/更新。")
        names = set(run(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines())
        volumes = set(run(["docker", "volume", "ls", "--format", "{{.Name}}"]).splitlines())
        if names.intersection(CONTAINERS.values()) or volumes.intersection({"mysql_data", "backend_logs"}):
            raise ManagerError("发现已有容器或数据卷；禁止覆盖安装，请定位原部署目录后迁移。")
        tag = resolve_tag(tag)
        self.check_space(0)
        try:
            port = input("前端端口（默认 6366）: ").strip() or "6366"
        except EOFError:
            return
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ManagerError("端口必须是 1-65535 的数字。")
        if not confirm("在空目录安装本 fork：" + str(self.directory) + "\n目标：" + tag + "；端口：" + port):
            return
        revision = tag[4:]
        raw = "https://raw.githubusercontent.com/" + REPO + "/" + revision + "/"
        with tempfile.TemporaryDirectory(prefix=".panel-install-", dir=str(self.directory)) as temporary:
            stage = Path(temporary)
            atomic_write(stage / "docker-compose.yml", fetch(raw + "docker-compose-fork-v4.yml"))
            atomic_write(stage / "gost.sql", fetch(raw + "gost.sql"))
            if not (stage / "gost.sql").stat().st_size:
                raise ManagerError("初始化 SQL 下载为空，停止安装。")
            values = {"DB_NAME": "flux", "DB_USER": "flux_user", "DB_PASSWORD": secrets.token_hex(24),
                      "JWT_SECRET": secrets.token_hex(32), "FRONTEND_PORT": port, "IMAGE_TAG": tag}
            atomic_write(stage / ".env", "".join(key + "=" + value + "\n" for key, value in values.items()))
            # Compose gives exported shell values precedence over --env-file;
            # a stale export must not override generated credentials or port.
            install_env = {key: value for key, value in os.environ.items() if key not in values}
            config = materialized(json.loads(run(["docker", "compose", "--project-directory", str(self.directory),
                                     "--env-file", str(stage / ".env"), "-f", str(stage / "docker-compose.yml"),
                                     "config", "--format", "json"], env=install_env)))
            self.project = config["name"]
            if run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=" + self.project,
                    "--format", "{{.ID}}"]).strip():
                raise ManagerError("此 Compose 项目已有容器，即使名称不同也不能覆盖安装。")
            if config["services"]["mysql"]["image"] != "mysql:5.7" or any(
                    config["services"][s]["image"] != IMAGES[s] + ":" + tag for s in IMAGES):
                raise ManagerError("下载的 Compose 镜像不符合所选 fork 版本，停止安装。")
            for service in ("mysql", "backend", "frontend"):
                run(["docker", "pull", config["services"][service]["image"]], timeout=900)
            self.check_space(0)
            config = self.candidate(config, tag)
            atomic_write(self.env, (stage / ".env").read_bytes())
            atomic_write(self.directory / "gost.sql", (stage / "gost.sql").read_bytes())
            write_config(self.source, config)
        try:
            self.compose(self.source, "up", "-d", "--pull", "never", "mysql", timeout=300)
            for _ in range(90):
                state = self.inspect("gost-mysql")["State"]
                if state["Running"] and state.get("Health", {}).get("Status") == "healthy":
                    break
                if not state["Running"] or state.get("Health", {}).get("Status") == "unhealthy":
                    raise ManagerError("新数据库启动失败。")
                time.sleep(2)
            else:
                raise ManagerError("新数据库启动超时。")
            self.start_service(self.source, "backend")
            self.wait_backend()
            self.start_service(self.source, "frontend")
            self.check_frontend()
        except (Exception, KeyboardInterrupt):
            raise ManagerError("首次安装未完成；配置和新数据卷已保留，请排查后在部署目录运行 docker compose up -d；不要覆盖安装或删除卷。") from None
        print("安装基础检查通过。访问 http://服务器IP:" + port +
              "\n默认管理员：admin_user / admin_user；请立即修改密码并验证业务。", flush=True)

    def rollback(self, backup):
        print("正在尝试 rollback（仅恢复容器/配置，不覆盖数据库）...", flush=True)
        # Recovery must also work if the source is corrupt or the backend is down.
        self.project = json.loads((backup / "rollback.json").read_text())["name"]
        atomic_write(self.source, (backup / "docker-compose.yml").read_bytes())
        self.start_service(backup / "rollback.json", "backend")
        self.wait_backend()
        self.start_service(backup / "rollback.json", "frontend")
        self.check_frontend()
        self.marker.unlink()
        print("rollback 完成；仍需验证登录、节点和规则。数据库未回灌。", flush=True)

    def recover(self):
        if not self.marker.is_file() or self.marker.is_symlink():
            raise ManagerError("没有可恢复的中断事务。")
        backup = Path(json.loads(self.marker.read_text())["backup"]).resolve()
        if backup.parent != self.backup_root.resolve() or not (backup / "rollback.json").is_file():
            raise ManagerError("恢复记录无效；请人工核对备份。")
        if confirm("将使用备份恢复旧容器和配置：" + str(backup) + "；不恢复 SQL。"):
            self.rollback(backup)

    def migrate(self, operation, tag):
        if self.marker.exists():
            raise ManagerError("发现未完成事务；请先选择“恢复中断的更新”。")
        config, containers = self.discover(require_health=operation != "backup")
        if operation == "update" and not all(config["services"][s]["image"].startswith(IMAGES[s] + ":") for s in IMAGES):
            raise ManagerError("这是原作者版本；请显式选择“旧版迁移到本 fork”，不能用普通更新隐式迁移。")
        if operation == "backup":
            self.check_database()
        else:
            self.check_schema()
        size = int(self.mysql("SELECT COALESCE(SUM(DATA_LENGTH+INDEX_LENGTH),0) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()").strip())
        self.check_space(size)
        if operation == "backup":
            if confirm("导出部署目录和数据库备份：" + str(self.directory)):
                self.backup(config, containers)
            return
        tag = resolve_tag(tag)
        if not confirm("部署目录：" + str(self.directory) + "\n目标：" + tag +
                       "\n将备份并短暂替换前后端；保留端口/网络/数据卷，不升级数据库或节点。\n"
                       "基础检查不能代替业务兼容性验证；建议先在备份副本验证。"):
            return
        original = self.source.read_bytes()
        original_env = self.env.read_bytes()
        backup = self.backup(config, containers)
        for service in IMAGES:
            print("预先拉取 " + service + " " + tag, flush=True)
            run(["docker", "pull", IMAGES[service] + ":" + tag], timeout=900)
        self.check_space(size)
        candidate = self.candidate(config, tag)
        write_config(backup / "candidate.json", candidate)
        self.compose(backup / "candidate.json", "config", "--quiet")
        # Catch external edits/panel updates during the potentially long backup/pull.
        _, current = self.discover()
        if (self.source.read_bytes() != original or self.env.read_bytes() != original_env or
                any(current[s]["Id"] != containers[s]["Id"] or current[s]["Image"] != containers[s]["Image"] for s in CONTAINERS)):
            raise ManagerError("备份期间部署发生变化；停止操作，请重新检查。")
        atomic_write(self.marker, json.dumps({"backup": str(backup)}))
        try:
            atomic_write(self.source, (backup / "candidate.json").read_bytes())
            self.start_service(self.source, "backend")
            self.wait_backend()
            self.start_service(self.source, "frontend")
            self.check_frontend()
            self.marker.unlink()
        except (Exception, KeyboardInterrupt):
            # A second termination must not interrupt recovery halfway through.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                self.rollback(backup)
            except Exception:
                raise ManagerError("自动 rollback 失败；已保留备份及恢复标记。请使用菜单恢复，禁止卸载或覆盖数据库。") from None
            raise ManagerError("更新未完成，已回退旧容器/配置；数据库未恢复。") from None
        print("前后端基础健康检查通过。请验证登录、WebSocket、节点心跳、隧道和流量。\n"
              "MySQL/phpMyAdmin 未重建；它们的 Docker 日志限制需另行维护时应用。历史日志未删除。", flush=True)


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "migrate", "update", "backup", "recover"))
    parser.add_argument("--directory", default=".")
    parser.add_argument("--tag", default=os.environ.get("PANEL_IMAGE_TAG"))
    args = parser.parse_args()
    os.umask(0o077)
    signal.signal(signal.SIGTERM, interrupted)
    manager = Manager(args.directory)
    lock_path = manager.directory / ".panel.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w"):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ManagerError("另一个管理操作正在运行。") from None
        manager.prerequisites()
        if args.operation == "recover":
            manager.recover()
        elif args.operation == "install":
            manager.install(args.tag)
        else:
            manager.migrate(args.operation, args.tag)


if __name__ == "__main__":
    try:
        main()
    except (ManagerError, ValueError, KeyError, OSError, KeyboardInterrupt) as error:
        # Do not dump tracebacks/config contents, which can contain secrets.
        message = str(error) if isinstance(error, ManagerError) else "操作中断或配置格式不受支持；请检查部署与恢复记录。"
        print("错误：" + message, file=sys.stderr)
        sys.exit(1)
