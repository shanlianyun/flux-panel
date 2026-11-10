"""Run the actual manager against isolated Docker/curl process doubles, never Docker."""

import json
import fcntl
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TAG = "sha-" + "a" * 40
BACKEND = "ghcr.io/shanlianyun/flux-panel-backend:"
FRONTEND = "ghcr.io/shanlianyun/flux-panel-frontend:"

# The executable boundary is fake; file transactions, validation and orchestration
# are the real production code. Unexpected commands always fail the test.
FAKE_RUNTIME = r'''#!/usr/bin/env python3
import json, os, pathlib, signal, sys
root = pathlib.Path(os.environ["FIXTURE"])
args = sys.argv[1:]
opts = json.loads((root / "options.json").read_text())
with (root / "commands.jsonl").open("a") as f:
    f.write(json.dumps([pathlib.Path(sys.argv[0]).name] + args) + "\n")
def emit(value):
    print(json.dumps(value) if isinstance(value, (list, dict)) else value)
def fail():
    print("external error containing secret db$PASSWORD", file=sys.stderr)
    sys.exit(1)
if pathlib.Path(sys.argv[0]).name == "curl":
    if opts.get("download_fail"):
        if "-o" in args: pathlib.Path(args[args.index("-o")+1]).write_text("print('UNSAFE PARTIAL EXECUTION')")
        fail()
    url = args[-1]
    if "api.github.com" in url:
        data = json.dumps({"sha": "a" * 40})
        if "-o" in args: pathlib.Path(args[args.index("-o")+1]).write_text(data)
        else: print(data)
    elif url.endswith("panel_manager.py"):
        pathlib.Path(args[args.index("-o")+1]).write_text("import sys; print('helper fetched', sys.argv[1:])\n")
    elif url.endswith("gost.sql"): print((root / "init.sql").read_text())
    else:
        config = json.loads((root / "compose.json").read_text())
        config["services"].pop("phpmyadmin", None)
        config["services"]["frontend"]["ports"][0]["published"] = "${FRONTEND_PORT}"
        config["services"]["backend"].pop("ports", None)
        for service in ("backend", "frontend"):
            config["services"][service]["image"] = "ghcr.io/shanlianyun/flux-panel-" + service + ":${IMAGE_TAG}"
        for key in config["services"]["mysql"]["environment"]:
            config["services"]["mysql"]["environment"][key] = "${" + {"MYSQL_DATABASE": "DB_NAME", "MYSQL_USER": "DB_USER", "MYSQL_PASSWORD": "DB_PASSWORD", "MYSQL_ROOT_PASSWORD": "DB_PASSWORD"}[key] + "}"
        for key in config["services"]["backend"]["environment"]:
            if key != "DB_HOST": config["services"]["backend"]["environment"][key] = "${" + key + "}"
        emit(config)
    sys.exit()
state_path = root / "containers.json"
state = json.loads(state_path.read_text())
if args[:2] == ["compose", "version"]: print("2.30.0"); sys.exit()
if args[:2] == ["context", "inspect"]:
    print(json.dumps("tcp://remote:2375" if opts.get("remote") else "unix:///var/run/docker.sock")); sys.exit()
if args[0] == "info": print(str(root)); sys.exit()
if args[0] == "ps":
    print("unrelated-project-container" if "--filter" in args and opts.get("existing_project") else "\n".join(state))
    sys.exit()
if args[:2] == ["volume", "ls"]:
    if opts.get("existing_volume"): print("mysql_data")
    sys.exit()
if args[:2] == ["volume", "inspect"]:
    if opts.get("existing_volume"): emit([{}]); sys.exit()
    fail()
if args[0] == "inspect":
    names = args[1:]
    if any(n not in state for n in names): fail()
    emit([state[n] for n in names]); sys.exit()
if args[:2] == ["image", "inspect"]:
    name = args[-1]
    kind = "mysql" if name == "mysql:5.7" else ("backend" if "backend" in name else "frontend")
    image_id = name if name.startswith("sha256:") else "sha256:" + ("new-" if "ghcr.io" in name else "old-") + kind
    emit([{"Id": image_id}]); sys.exit()
if args[0] == "pull":
    if opts.get("pull_fail") and "frontend" in args[-1]: fail()
    if opts.get("edit_during_pull") and "frontend" in args[-1]:
        with (root / "deploy" / "docker-compose.yml").open("a") as f: f.write("\n# external edit\n")
    print("pulled"); sys.exit()
if args[0] == "exec":
    command = " ".join(args[2:])
    if "mysqldump" in command:
        if opts.get("dump_fail"): fail()
        if not opts.get("dump_empty"): print("-- verified nonempty fixture dump\nCREATE TABLE example (id int);")
    elif "information_schema.COLUMNS" in command: print((root / "schema.tsv").read_text(), end="")
    elif "information_schema.TABLES" in command and "ENGINE" in command:
        print("MyISAM" if opts.get("myisam") else "InnoDB")
    elif "information_schema.TABLES" in command: print(opts.get("db_size", 1024))
    elif "VERSION()" in command: print("8.0.0" if opts.get("mysql8") else "5.7.40")
    elif "wget" in command:
        if opts.get("http_fail") and state["vite-frontend"]["Image"] == "sha256:new-frontend": fail()
        print("<!doctype html><html>panel</html>")
    else: fail()
    sys.exit()
if args[0] == "compose":
    file = pathlib.Path(args[args.index("-f") + 1])
    try:
        config = json.loads(file.read_text())
        env_path = pathlib.Path(args[args.index("--env-file") + 1]) if "--env-file" in args else root / "missing"
        env = dict(line.split("=", 1) for line in env_path.read_text().splitlines() if "=" in line) if env_path.exists() else {}
        def interpolate(value):
            if isinstance(value, dict): return {k: interpolate(v) for k, v in value.items()}
            if isinstance(value, list): return [interpolate(v) for v in value]
            if isinstance(value, str):
                import re
                value = value.replace("$$", "\x00")
                value = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m[1], env.get(m[1], "")), value)
                return value.replace("\x00", "$")
            return value
        config = interpolate(config)
    except ValueError: config = json.loads((root / "compose.json").read_text())
    if "config" in args:
        if opts.get("config_fail") or (opts.get("candidate_invalid") and "--quiet" in args): fail()
        def render(value):
            if isinstance(value, dict): return {k: render(v) for k, v in value.items()}
            if isinstance(value, list): return [render(v) for v in value]
            return value.replace("$", "$$") if isinstance(value, str) else value
        if "--quiet" not in args: emit(render(config))
        sys.exit()
    if "up" in args:
        service = args[-1]
        if service not in ("mysql", "backend", "frontend"): fail()
        image = config["services"][service]["image"]
        is_new = "ghcr.io" in image
        if opts.get("rollback_fail") and not is_new: fail()
        if opts.get("up_fail") and is_new and service == "frontend": fail()
        if opts.get("interrupt") and is_new and service == "backend":
            os.kill(os.getppid(), signal.SIGTERM)
            sys.exit()
        name = {"backend": "springboot-backend", "frontend": "vite-frontend", "mysql": "gost-mysql"}[service]
        if name not in state:
            state[name] = json.loads((root / "initial-containers.json").read_text())[name]
        state[name]["Image"] = image if image.startswith("sha256:") else "sha256:new-" + service
        state[name]["Config"]["Image"] = image
        state[name]["Config"]["Env"] = [k + "=" + v for k, v in config["services"][service].get("environment", {}).items()]
        state[name]["State"]["Health"]["Status"] = "unhealthy" if opts.get("unhealthy") and is_new else "healthy"
        state_path.write_text(json.dumps(state))
        sys.exit()
fail()
'''


def schema_fixture():
    """Fixture comes from the repository's actual initialization DDL, not manager constants."""
    rows = []
    for table, body in re.findall(r"CREATE TABLE `(\w+)` \((.*?)\) ENGINE", (ROOT / "gost.sql").read_text(), re.S):
        for name, definition in re.findall(r"^  `(\w+)` (.*?)(?:,)?$", body, re.M):
            type_name = definition.split()[0]
            nullable = "NO" if "NOT NULL" in definition else "YES"
            default = "NULL" if "DEFAULT" not in definition else definition.split("DEFAULT ", 1)[1].strip("',")
            extra = "auto_increment" if name == "id" else ""
            rows.append("\t".join((table, name, type_name, nullable, default, extra)))
    return "\n".join(rows) + "\n"


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="panel-manager-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for command in ("docker", "curl"):
            path = self.bin / command
            path.write_text(FAKE_RUNTIME)
            path.chmod(0o755)
        self.deploy = self.root / "deploy"
        self.deploy.mkdir()
        self.source = self.deploy / "docker-compose.yml"
        self.source.write_text("# original configuration, preserve on rollback\nservices: {}\n")
        self.original = self.source.read_bytes()
        (self.deploy / ".env").write_text("DB_PASSWORD='db$PASSWORD'\nJWT_SECRET='jwt$SECRET'\n")
        self.compose = {
            "name": "original-project", "services": {
                "mysql": {"image": "mysql:5.7", "container_name": "gost-mysql", "environment": {
                    "MYSQL_DATABASE": "gost", "MYSQL_USER": "flux", "MYSQL_PASSWORD": "db$PASSWORD",
                    "MYSQL_ROOT_PASSWORD": "root-secret"}, "volumes": [{"type": "volume", "source": "mysql_data", "target": "/var/lib/mysql"}]},
                "backend": {"image": "brunuhville/springboot-backend:latest", "container_name": "springboot-backend",
                            "environment": {"DB_HOST": "mysql", "DB_NAME": "gost", "DB_USER": "flux", "DB_PASSWORD": "db$PASSWORD", "JWT_SECRET": "jwt$SECRET"},
                            "ports": [{"target": 6365, "published": "7635", "protocol": "tcp", "host_ip": "127.0.0.1"}],
                            "volumes": [{"type": "volume", "source": "backend_logs", "target": "/app/logs"}]},
                "frontend": {"image": "brunuhville/vite-frontend:latest", "container_name": "vite-frontend",
                             "ports": [{"target": 80, "published": "7636", "protocol": "tcp"}]},
                "phpmyadmin": {"image": "phpmyadmin/phpmyadmin", "ports": [{"target": 80, "published": "8066"}]}},
            "volumes": {"mysql_data": {"name": "mysql_data"}, "backend_logs": {"name": "backend_logs"}},
            "networks": {"default": {"name": "custom-existing-network"}}}
        state = {}
        for service in ("mysql", "backend", "frontend"):
            spec = self.compose["services"][service]
            spec["networks"] = {"default": None}
            if service == "backend":
                spec["healthcheck"] = {"test": ["CMD", "wget", "--spider", "http://localhost:6365/flow/test"]}
            mounts = [{"Type": "volume", "Name": v["source"], "Destination": v["target"], "RW": True} for v in spec.get("volumes", [])]
            ports = {str(p["target"]) + "/" + p.get("protocol", "tcp"): [{"HostPort": p["published"], "HostIp": p.get("host_ip", "0.0.0.0")}] for p in spec.get("ports", [])}
            state[spec["container_name"]] = {
                "Id": service + "-container", "Image": "sha256:old-" + service,
                "State": {"Running": True, "Health": {"Status": "healthy"}},
                "Config": {"Image": spec["image"], "Env": [k + "=" + v for k, v in spec.get("environment", {}).items()],
                           "Labels": {"com.docker.compose.project": "original-project", "com.docker.compose.service": service,
                                      "com.docker.compose.project.working_dir": str(self.deploy)}},
                "Mounts": mounts, "HostConfig": {"PortBindings": ports},
                "NetworkSettings": {"Networks": {"custom-existing-network": {"Aliases": [spec["container_name"], service], "IPAMConfig": None}}}}
        self.state = state
        (self.root / "initial-containers.json").write_text(json.dumps(state))
        self.save_fixture()
        (self.root / "schema.tsv").write_text(schema_fixture())
        (self.root / "init.sql").write_text((ROOT / "gost.sql").read_text())
        self.options = {}
        self.env = dict(os.environ, PATH=str(self.bin) + ":" + os.environ["PATH"], FIXTURE=str(self.root), PYTHONDONTWRITEBYTECODE="1")
        for key in list(self.env):
            if key.startswith(("COMPOSE_", "DOCKER_", "PANEL_")):
                del self.env[key]

    def save_fixture(self):
        (self.root / "compose.json").write_text(json.dumps(self.compose))
        (self.root / "containers.json").write_text(json.dumps(self.state))

    def run_manager(self, operation="migrate", answers="yes\n", tag=TAG):
        (self.root / "options.json").write_text(json.dumps(self.options))
        args = ["python3", str(ROOT / "panel_manager.py"), operation, "--directory", str(self.deploy)]
        if tag:
            args += ["--tag", tag]
        return subprocess.run(args, input=answers, text=True, capture_output=True, env=self.env, timeout=20)

    def commands(self):
        path = self.root / "commands.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def backups(self):
        return list((self.deploy / "panel-backups").glob("*"))

    def assert_untouched(self):
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(any("up" in c or "down" in c or "stop" in c for c in self.commands()))

    def test_migration_preserves_ports_volumes_credentials_and_other_services(self):
        result = self.run_manager()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        candidate = json.loads(self.source.read_text())
        self.assertEqual(candidate["services"]["backend"]["image"], BACKEND + TAG)
        self.assertEqual(candidate["services"]["frontend"]["image"], FRONTEND + TAG)
        self.assertEqual(candidate["services"]["mysql"], {**self.compose["services"]["mysql"], "environment": {k: v.replace("$", "$$") for k, v in self.compose["services"]["mysql"]["environment"].items()}})
        self.assertEqual(candidate["services"]["backend"]["ports"], [{"target": 6365, "published": "7635", "protocol": "tcp", "host_ip": "127.0.0.1"}])
        self.assertEqual(candidate["services"]["phpmyadmin"], self.compose["services"]["phpmyadmin"])
        self.assertEqual(candidate["networks"], self.compose["networks"])
        self.assertEqual(candidate["services"]["backend"]["environment"]["JWT_SECRET"], "jwt$$SECRET")
        self.assertEqual(candidate["services"]["backend"]["logging"]["options"], {"max-size": "10m", "max-file": "3"})
        backup = self.backups()[0]
        self.assertEqual((backup / "docker-compose.yml").read_bytes(), self.original)
        self.assertGreater((backup / "database.sql").stat().st_size, 0)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.source.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.deploy / ".panel-transaction.json").exists())
        commands = self.commands()
        pulls = [i for i, c in enumerate(commands) if c[1:2] == ["pull"]]
        starts = [i for i, c in enumerate(commands) if "up" in c]
        self.assertEqual(len(pulls), 2)
        self.assertLess(max(pulls), min(starts))
        for i in starts:
            self.assertIn("--no-deps", commands[i])
            self.assertIn(commands[i][-1], ("backend", "frontend"))
        self.assertNotIn("db$PASSWORD", result.stdout + result.stderr)

    def test_declining_confirmation_changes_nothing(self):
        result = self.run_manager(answers="no\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_untouched()
        self.assertEqual(self.backups(), [])

    def test_incompatible_schema_stops_without_upgrading_or_dropping_columns(self):
        path = self.root / "schema.tsv"
        path.write_text(path.read_text().replace("node\tport_sta\tint(10)", "node\tport\tint(10)"))
        result = self.run_manager()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("schema", result.stderr.lower())
        self.assert_untouched()

    def test_required_legacy_extra_column_rejects_migration(self):
        with (self.root / "schema.tsv").open("a") as f:
            f.write("user\tname\tvarchar(100)\tNO\tNULL\t\n")
        self.assertNotEqual(self.run_manager().returncode, 0)
        self.assert_untouched()

    def test_failed_or_empty_dump_cannot_replace_containers(self):
        for flag in ("dump_fail", "dump_empty"):
            with self.subTest(flag=flag):
                self.options = {flag: True}
                result = self.run_manager()
                self.assertNotEqual(result.returncode, 0)
                self.assert_untouched()

    def test_unpublished_image_stops_before_disruption(self):
        self.options["pull_fail"] = True
        self.assertNotEqual(self.run_manager().returncode, 0)
        self.assert_untouched()

    def test_wrong_project_or_changed_environment_rejects_deployment(self):
        self.state["springboot-backend"]["Config"]["Env"].append("JWT_SECRET=different")
        self.save_fixture()
        self.assertNotEqual(self.run_manager().returncode, 0)
        self.assert_untouched()

    def test_mysql_version_and_nontransactional_tables_are_rejected(self):
        for flag in ("mysql8", "myisam", "remote"):
            with self.subTest(flag=flag):
                self.options = {flag: True}
                self.assertNotEqual(self.run_manager().returncode, 0)
                self.assert_untouched()

    def test_low_space_rejects_before_dump_or_pull(self):
        self.options["db_size"] = 10**20
        result = self.run_manager()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("space", result.stderr.lower())
        self.assert_untouched()
        self.assertFalse(any("mysqldump" in " ".join(c) for c in self.commands()))

    def test_health_failure_restores_source_and_pins_old_images_without_db_restore(self):
        self.options["unhealthy"] = True
        result = self.run_manager()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.source.read_bytes(), self.original)
        state = json.loads((self.root / "containers.json").read_text())
        self.assertEqual(state["springboot-backend"]["Image"], "sha256:old-backend")
        self.assertEqual(state["vite-frontend"]["Image"], "sha256:old-frontend")
        self.assertFalse((self.deploy / ".panel-transaction.json").exists())
        self.assertFalse(any("down" in c or "--volumes" in c or "--rmi" in c or "-i" in c for c in self.commands()))

    def test_frontend_failure_also_rolls_back_backend(self):
        self.options["up_fail"] = True
        result = self.run_manager()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(json.loads((self.root / "containers.json").read_text())["springboot-backend"]["Image"], "sha256:old-backend")

    def test_sigterm_attempts_rollback(self):
        self.options["interrupt"] = True
        result = self.run_manager()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertIn("rollback", result.stdout.lower())

    def test_failed_rollback_keeps_recovery_marker_and_can_be_retried(self):
        self.options.update(unhealthy=True, rollback_fail=True)
        self.assertNotEqual(self.run_manager().returncode, 0)
        self.assertTrue((self.deploy / ".panel-transaction.json").exists())
        before = len(self.commands())
        self.assertNotEqual(self.run_manager().returncode, 0)
        self.assertFalse(any("up" in c for c in self.commands()[before:]))
        self.options = {}
        recovered = self.run_manager("recover")
        self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        self.assertFalse((self.deploy / ".panel-transaction.json").exists())
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_update_refuses_implicit_legacy_migration(self):
        result = self.run_manager("update")
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()

    def test_fork_update_works_after_successful_migration(self):
        self.assertEqual(self.run_manager().returncode, 0)
        result = self.run_manager("update")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.backups()), 2)

    def test_backup_accepts_old_schema_without_altering_it(self):
        (self.root / "schema.tsv").write_text("old schema\n")
        result = self.run_manager("backup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_untouched()
        self.assertGreater((self.backups()[0] / "database.sql").stat().st_size, 0)

    def empty_install(self):
        self.source.unlink()
        (self.deploy / ".env").unlink()
        self.state = {}
        self.save_fixture()

    def test_empty_install_uses_fork_images_and_random_private_credentials(self):
        self.empty_install()
        result = self.run_manager("install", answers="9636\nyes\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = json.loads(self.source.read_text())
        self.assertEqual(config["services"]["backend"]["image"], BACKEND + TAG)
        self.assertEqual(config["services"]["frontend"]["ports"][0]["published"], "9636")
        self.assertIn("DB_PASSWORD=", (self.deploy / ".env").read_text())
        self.assertNotIn("db$PASSWORD", self.source.read_text())
        self.assertEqual((self.deploy / ".env").stat().st_mode & 0o777, 0o600)

    def test_install_refuses_existing_deployment_and_orphaned_data(self):
        result = self.run_manager("install")
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()
        self.empty_install()
        self.options["existing_volume"] = True
        self.assertNotEqual(self.run_manager("install").returncode, 0)
        self.assertFalse(self.source.exists())
        self.assertFalse((self.deploy / ".env").exists())

    def test_install_download_failure_leaves_no_deployment_files(self):
        self.empty_install()
        self.options["download_fail"] = True
        self.assertNotEqual(self.run_manager("install", answers="\nyes\n").returncode, 0)
        self.assertFalse(self.source.exists())
        self.assertFalse((self.deploy / ".env").exists())

    def test_install_generated_credentials_and_port_override_inherited_environment(self):
        self.empty_install()
        self.env.update(DB_PASSWORD="inherited-weak", JWT_SECRET="inherited-jwt", FRONTEND_PORT="9966")
        result = self.run_manager("install", answers="9636\nyes\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        candidate = json.loads(self.source.read_text())
        saved = dict(line.split("=", 1) for line in (self.deploy / ".env").read_text().splitlines())
        backend_env = candidate["services"]["backend"]["environment"]
        self.assertEqual(backend_env["DB_PASSWORD"], saved["DB_PASSWORD"])
        self.assertEqual(backend_env["JWT_SECRET"], saved["JWT_SECRET"])
        self.assertGreaterEqual(len(backend_env["DB_PASSWORD"]), 48)
        self.assertEqual(candidate["services"]["frontend"]["ports"][0]["published"], "9636")

    def test_menu_exit_does_not_delete_script(self):
        script = self.deploy / "panel_install.sh"
        shutil.copyfile(ROOT / "panel_install.sh", script)
        result = subprocess.run(["bash", str(script)], input="6\n", text=True,
                                capture_output=True, env=self.env, cwd=self.deploy, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(script.is_file(), "the management entry point must remain available")

    def test_menu_failed_bootstrap_never_executes_partial_download(self):
        self.options["download_fail"] = True
        (self.root / "options.json").write_text(json.dumps(self.options))
        script = self.deploy / "panel_install.sh"
        shutil.copyfile(ROOT / "panel_install.sh", script)
        result = subprocess.run(["bash", str(script)], input="7\n\n", text=True,
                                capture_output=True, env=self.env, cwd=self.deploy, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("helper fetched", result.stdout)
        self.assertNotIn("UNSAFE PARTIAL EXECUTION", result.stdout)
        self.assertTrue(script.exists())
        self.assertTrue(any(c[0] == "curl" for c in self.commands()))

    def test_downloaded_menu_fetches_commit_pinned_helper_and_dispatches_migration(self):
        (self.root / "options.json").write_text("{}")
        script = self.deploy / "panel_install.sh"
        shutil.copyfile(ROOT / "panel_install.sh", script)
        result = subprocess.run(["bash", str(script)], input="7\n\n", text=True,
                                capture_output=True, env=self.env, cwd=self.deploy, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("'migrate'", result.stdout)
        urls = [c[-1] for c in self.commands() if c[0] == "curl"]
        self.assertEqual(urls[-1], "https://raw.githubusercontent.com/shanlianyun/flux-panel/" + "a" * 40 + "/panel_manager.py")

    def test_repository_menu_runs_actual_migration(self):
        (self.root / "options.json").write_text("{}")
        result = subprocess.run(["bash", str(ROOT / "panel_install.sh")], input="7\n\nyes\n", text=True,
                                capture_output=True, env={**self.env, "PANEL_IMAGE_TAG": TAG}, cwd=self.deploy, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.source.read_text())["services"]["backend"]["image"], BACKEND + TAG)

    def test_missing_explicit_tag_resolves_immutable_main_commit(self):
        result = self.run_manager(tag=None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.source.read_text())["services"]["backend"]["image"], BACKEND + TAG)

    def test_concurrent_manager_is_rejected(self):
        with (self.deploy / ".panel.lock").open("w") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_manager()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("另一个", result.stderr)
        self.assert_untouched()

    def test_invalid_candidate_and_external_edits_stop_before_replacement(self):
        self.options["candidate_invalid"] = True
        self.assertNotEqual(self.run_manager().returncode, 0)
        self.assert_untouched()
        self.options = {"edit_during_pull": True}
        self.assertNotEqual(self.run_manager().returncode, 0)
        self.assertEqual(self.source.read_bytes(), self.original + b"\n# external edit\n")
        self.assertFalse(any("up" in c for c in self.commands()))

    def test_recovery_marker_cannot_select_directory_outside_backup_root(self):
        (self.deploy / ".panel-transaction.json").write_text(json.dumps({"backup": str(self.root)}))
        self.assertNotEqual(self.run_manager("recover").returncode, 0)
        self.assert_untouched()

    def test_runtime_only_network_or_mount_is_not_silently_lost(self):
        self.state["vite-frontend"]["NetworkSettings"]["Networks"]["reverse-proxy"] = {"Aliases": ["panel"]}
        self.save_fixture()
        result = self.run_manager()
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()
        del self.state["vite-frontend"]["NetworkSettings"]["Networks"]["reverse-proxy"]
        self.state["springboot-backend"]["Mounts"].append({"Type": "bind", "Source": "/custom", "Destination": "/custom", "RW": True})
        self.save_fixture()
        self.assertNotEqual(self.run_manager().returncode, 0)
        self.assert_untouched()

    def test_missing_old_backend_healthcheck_is_rejected_before_migration(self):
        del self.compose["services"]["backend"]["healthcheck"]
        del self.state["springboot-backend"]["State"]["Health"]
        self.save_fixture()
        result = self.run_manager()
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()

    def test_install_refuses_other_compose_files_and_existing_project_labels(self):
        self.empty_install()
        other = self.deploy / "compose.yaml"
        other.write_text("services: {database: {image: mysql:5.7}}")
        result = self.run_manager("install", answers="\nyes\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.source.exists())
        other.unlink()
        self.options["existing_project"] = True
        result = self.run_manager("install", answers="\nyes\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.source.exists())
        self.assertFalse((self.deploy / ".env").exists())

    def test_latest_or_malformed_tag_is_rejected(self):
        for tag in ("latest", "sha-not-a-commit", "../file"):
            with self.subTest(tag=tag):
                self.assertNotEqual(self.run_manager(tag=tag).returncode, 0)
                self.assert_untouched()


class OfflineComposeTests(unittest.TestCase):
    def test_candidate_configs_preserve_real_compose_services_and_networks(self):
        docker = shutil.which("docker")
        if not docker or subprocess.run([docker, "compose", "version"], capture_output=True).returncode:
            self.skipTest("Docker Compose CLI unavailable; no daemon is needed")
        spec = importlib.util.spec_from_file_location("panel_manager", ROOT / "panel_manager.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        env = {**os.environ, "DB_NAME": "gost", "DB_USER": "flux", "DB_PASSWORD": "db$PASS",
               "JWT_SECRET": "jwt$$literal", "BACKEND_PORT": "7635", "FRONTEND_PORT": "7636", "IMAGE_TAG": TAG}
        with tempfile.TemporaryDirectory(prefix="panel-offline-candidate-") as directory:
            def render(path):
                return module.materialized(json.loads(subprocess.check_output([docker, "compose", "--project-directory", directory,
                    "-f", str(path), "config", "--format", "json"], text=True, env=env)))
            for name in ("docker-compose-v4.yml", "docker-compose-v6.yml", "docker-compose-fork-v4.yml", "docker-compose-fork-v6.yml"):
                with self.subTest(config=name):
                    original = render(ROOT / name)
                    candidate = module.Manager(directory).candidate(original, TAG)
                    path = Path(directory) / "candidate.json"
                    module.write_config(path, candidate)
                    result = render(path)
                    self.assertEqual(result["services"]["mysql"], original["services"]["mysql"])
                    self.assertEqual(result["networks"], original["networks"])
                    self.assertEqual(result["volumes"], original["volumes"])
                    self.assertEqual(result["services"]["backend"]["environment"]["DB_PASSWORD"], "db$PASS")
                    self.assertEqual(result["services"]["frontend"]["image"], FRONTEND + TAG)
                    self.assertEqual(result["services"]["backend"].get("ports"), original["services"]["backend"].get("ports"))

    def test_rendered_dollars_survive_real_compose_roundtrip(self):
        docker = shutil.which("docker")
        if not docker or subprocess.run([docker, "compose", "version"], capture_output=True).returncode:
            self.skipTest("Docker Compose CLI unavailable; no daemon is needed")
        spec = importlib.util.spec_from_file_location("panel_manager", ROOT / "panel_manager.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="panel-offline-compose-") as directory:
            root = Path(directory)
            (root / ".env").write_text("PASSWORD='db$WORD $$PAIR ${BRACE} $'\n")
            source = root / "compose.json"
            source.write_text(json.dumps({"services": {"test": {"image": "busybox",
                "environment": {"SECRET": "${PASSWORD}"}, "command": ["sh", "-c", "echo $$SECRET"],
                "labels": {"test": "cost$$5"}, "healthcheck": {"test": ["CMD-SHELL", "echo $$SECRET"]},
                "volumes": [{"type": "bind", "source": "./data$$dir", "target": "/data$$dir"}]}}}))
            def render(path):
                return json.loads(subprocess.check_output([docker, "compose", "--project-directory", directory,
                    "-f", str(path), "config", "--format", "json"], text=True))
            first = render(source)
            canonical = module.materialized(first)
            self.assertEqual(canonical["services"]["test"]["environment"]["SECRET"], "db$WORD $$PAIR ${BRACE} $")
            self.assertEqual(canonical["services"]["test"]["command"], ["sh", "-c", "echo $SECRET"])
            output = root / "roundtrip.json"
            module.write_config(output, canonical)
            self.assertEqual(render(output), first)


if __name__ == "__main__":
    unittest.main()
