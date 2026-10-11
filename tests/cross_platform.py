"""Native acceptance checks; Python standard library only (3.8+).

Run against a freshly built native binary on each target OS. Creates an isolated
data directory beside the JSON report; never uses the normal MIRA data directory.
Windows shutdown uses terminate(); Ctrl+C must be checked separately.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="mira-acceptance-", dir=report.parent))
    home = work / "data with spaces МИРА"
    env = dict(os.environ)
    env.pop("MIRA_HOME", None)
    checks = []
    commands = []
    servers = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    result = {"platform": platform.platform(), "machine": platform.machine(),
              "binary": str(binary), "work": str(work), "checks": checks,
              "commands": commands, "windows_graceful_shutdown_tested": False}

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(("PASS " if condition else "FAIL ") + name, flush=True)
        if not condition:
            raise AssertionError(name)

    def run(*parts, code=0, data_home=home, custom_env=None):
        argv = [str(binary)]
        if data_home is not None:
            argv += ["--home", str(data_home)]
        argv += list(parts)
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", timeout=20, env=custom_env or env,
                              cwd=work)
        commands.append({"argv": argv, "exit_code": proc.returncode,
                         "stdout": proc.stdout, "stderr": proc.stderr})
        if proc.returncode != code:
            raise AssertionError("{}: exit {} expected {}: {}".format(
                parts, proc.returncode, code, proc.stderr))
        return proc.stdout

    def cli(*parts, **kwargs):
        return json.loads(run(*parts, "--json", **kwargs))

    def free_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def get(port, path):
        with opener.open("http://127.0.0.1:{}{}".format(port, path), timeout=5) as response:
            return response.status, response.headers, response.read()

    def start(port):
        log = open(work / ("server-{}.stderr".format(len(servers))), "wb")
        proc = subprocess.Popen([str(binary), "--home", str(home), "serve"],
                                stdout=log, stderr=log, env=env, cwd=work)
        servers.append((proc, log))
        for _ in range(100):
            if proc.poll() is not None:
                raise AssertionError("Server exited: {}".format(proc.returncode))
            try:
                if json.loads(get(port, "/api/v1/status")[2])["status"] == "ok":
                    return proc
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        raise AssertionError("Server startup timeout")

    def stop(proc, interrupt=False):
        if os.name == "nt":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGINT if interrupt else signal.SIGTERM)
        code = proc.wait(timeout=10)
        if os.name != "nt":
            check("graceful " + ("SIGINT" if interrupt else "SIGTERM"), code == 0)
        check("stopped and lock released", cli("status", code=1)["status"] == "stopped")

    try:
        untouched = work / "help-must-not-create"
        run("--help", data_home=untouched)
        run("--version", data_home=untouched)
        check("help/version have no filesystem side effects", not untouched.exists())
        version = cli("version")
        check("version JSON", version == {"core": "0.1.0"})
        check("initial status", cli("status", code=1)["status"] == "stopped")
        paths = cli("config", "paths")
        check("unicode and spaces in home", Path(paths["root"]) == home)
        check("first-run directories", all((home / p).is_dir() for p in ("config", "data", "logs")))
        config = cli("config")
        check("default configuration", config["http"] == {"host": "127.0.0.1", "port": 8787})
        relative = cli("config", "paths", data_home=Path("relative-home"))
        check("relative home", Path(relative["root"]) == work / "relative-home")
        env_home = dict(env, MIRA_HOME=str(work / "env-home"))
        check("MIRA_HOME", Path(cli("config", "paths", data_home=None, custom_env=env_home)["root"]) == work / "env-home")
        check("CLI home overrides environment", Path(cli("config", "paths", custom_env=env_home)["root"]) == home)
        system = cli("system")
        result["system"] = system
        check("system architecture", system["architecture"] == {"AMD64": "x86_64", "arm64": "aarch64"}.get(platform.machine(), platform.machine()))
        check("system resources", system["ram_total_bytes"] > 0 and system["cpu"]["logical_cores"] == os.cpu_count() and bool(system["disks"]) and bool(system["network_interfaces"]))
        cli("config", "set", "node_name", "МИРА acceptance")
        cli("config", "set", "logging.level", "DEBUG")
        port = free_port()
        cli("config", "set", "http.port", str(port))
        config_file = home / "config" / "mira.toml"
        before = config_file.read_bytes()
        for key, value in [("http.port", "0"), ("http.port", "65536"), ("http.host", "invalid"), ("node_name", ""), ("unknown", "x")]:
            run("config", "set", key, value, code=1)
        check("invalid configuration preserves TOML", config_file.read_bytes() == before)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(cli, "config", "set", key, value) for key, value in
                       [("node_name", "МИРА concurrent"), ("logging.level", "DEBUG")]]
            for future in futures:
                future.result()
        saved = cli("config")
        check("concurrent configuration writes", saved["node_name"] == "МИРА concurrent" and saved["logging"]["level"] == "debug")
        server = start(port)
        check("running CLI and HTTP status match", cli("status") == json.loads(get(port, "/api/v1/status")[2]))
        check("HTTP version", json.loads(get(port, "/api/v1/version")[2]) == version)
        http_system = json.loads(get(port, "/api/v1/system")[2])
        check("CLI and HTTP system agree", all(http_system[k] == system[k] for k in ("os", "os_version", "architecture", "hostname", "cpu", "ram_total_bytes")))
        for path, marker in [("/", b"MIRA"), ("/app.js", b"fetch("), ("/style.css", b"{")]:
            status, headers, body = get(port, path)
            check("embedded asset " + path, status == 200 and marker in body and headers.get("Cache-Control") == "no-store")
        try:
            get(port, "/missing-route")
            raise AssertionError("Missing route did not return 404")
        except urllib.error.HTTPError as error:
            check("unknown route", error.code == 404 and json.loads(error.read()) == {"error": "not_found"})
        run("serve", code=1)
        check("second server rejected", "Another MIRA server" in commands[-1]["stderr"])
        next_port = free_port()
        cli("config", "set", "http.port", str(next_port))
        check("status follows live endpoint after config change", cli("status")["status"] == "ok")
        stop(server)
        server = start(next_port)
        check("restart uses new port", cli("status")["status"] == "ok")
        check("configuration survives restart", cli("config")["node_name"] == "МИРА concurrent")
        stop(server, interrupt=True)
        logs = "\n".join(p.read_text(encoding="utf-8") for p in (home / "logs").glob("*.log"))
        check("persistent INFO DEBUG ERROR logs", all(level in logs for level in ("INFO", "DEBUG", "ERROR")))
        if os.name != "nt":
            check("shutdown flushed to log", logs.count("MIRA Core stopped") >= 2 and logs.count("Shutdown requested") >= 2)
        original = config_file.read_bytes()
        try:
            config_file.write_bytes(b"[invalid TOML\n")
            run("config", code=1)
            check("corrupt TOML preserved", config_file.read_bytes() == b"[invalid TOML\n")
        finally:
            config_file.write_bytes(original)
        result["passed"] = True
    except Exception as error:
        result["passed"] = False
        result["error"] = str(error)
        print("FAIL " + str(error), flush=True)
    finally:
        for proc, log in servers:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            log.close()
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Report: " + str(report), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
