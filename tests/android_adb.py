"""Run CLI/API acceptance on a USB-connected Android ARM64 device through ADB.

Python runs on the computer. The device executes MIRA as the ADB shell user in
an isolated /data/local/tmp directory. No APK, root, or Python on device required.
"""
import argparse
import concurrent.futures
import datetime
import json
from pathlib import Path
import random
import shlex
import subprocess
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--serial")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    devices = subprocess.run([str(args.adb), "devices"], capture_output=True,
                             text=True, encoding="utf-8", timeout=15, check=True)
    available = [line.split()[0] for line in devices.stdout.splitlines()[1:]
                 if len(line.split()) >= 2 and line.split()[1] == "device"]
    serial = args.serial
    if serial is None:
        if len(available) != 1:
            raise RuntimeError("Expected one authorized device; found {}".format(len(available)))
        serial = available[0]
    if serial not in available:
        raise RuntimeError("Selected device is not connected/authorized")
    prefix = [str(args.adb), "-s", serial]
    root = "/data/local/tmp/mira-acceptance-{}-{}".format(
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"), random.randrange(100000))
    home = root + "/data with spaces МИРА"
    remote_binary = root + "/mira"
    checks, commands, servers, forwards = [], [], [], []
    result = {"environment": "Android via ADB shell (not an APK sandbox)",
              "binary": str(binary), "work": root, "checks": checks, "commands": commands}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def adb(*parts, code=0, timeout=25):
        proc = subprocess.run(prefix + list(parts), capture_output=True, text=True,
                              encoding="utf-8", timeout=timeout)
        commands.append({"argv": [str(args.adb), "-s", "<device>"] + list(parts),
                         "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
        if code is not None and proc.returncode != code:
            raise AssertionError("ADB {}: exit {}, expected {}: {}".format(parts, proc.returncode, code, proc.stderr))
        return proc.stdout

    def shell(command, **kw):
        return adb("shell", command, **kw)

    def quote(value):
        return shlex.quote(str(value))

    def run(*parts, code=0, data_home=home, mira_env=None):
        argv = [remote_binary]
        if data_home is not None:
            argv += ["--home", data_home]
        argv += list(parts)
        if mira_env is not None:
            argv = ["env", "MIRA_HOME=" + mira_env] + argv
        return shell("cd {} && {}".format(quote(root), shlex.join(argv)), code=code)

    def cli(*parts, **kw):
        return json.loads(run(*parts, "--json", **kw))

    def read(path):
        return adb("exec-out", "cat " + quote(path))

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(("PASS " if condition else "FAIL ") + name, flush=True)
        if not condition:
            raise AssertionError(name)

    def forward(port):
        local = int(adb("forward", "tcp:0", "tcp:" + str(port)).strip())
        forwards.append(local)
        return local

    def get(port, path):
        with opener.open("http://127.0.0.1:{}{}".format(port, path), timeout=10) as response:
            return response.status, response.headers, response.read()

    def start(port):
        index = len(servers)
        pid_file = root + "/server-{}.pid".format(index)
        exit_file = root + "/server-{}.exit".format(index)
        log_file = root + "/server-{}.log".format(index)
        runner = "{} --home {} serve > {} 2>&1 & child=$!; printf '%s' \"$child\" > {}; wait \"$child\"; printf '%s' \"$?\" > {}".format(
            quote(remote_binary), quote(home), quote(log_file), quote(pid_file), quote(exit_file))
        shell("nohup sh -c {} > {} 2>&1 < /dev/null &".format(quote(runner), quote(root + "/launcher-{}.log".format(index))))
        local = forward(port)
        server = {"pid_file": pid_file, "exit_file": exit_file, "log_file": log_file, "local": local, "stopped": False}
        servers.append(server)
        for _ in range(100):
            try:
                if json.loads(get(local, "/api/v1/status")[2])["status"] == "ok":
                    server["pid"] = int(read(pid_file).strip())
                    return server
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        raise AssertionError("Server startup timeout: " + read(log_file))

    def stop(server, signal):
        shell("kill -{} {}".format(signal, server["pid"]))
        for _ in range(100):
            if shell("test -f " + quote(server["exit_file"]), code=None) == "":
                try:
                    exit_code = int(read(server["exit_file"]).strip())
                    break
                except (AssertionError, ValueError):
                    pass
            time.sleep(0.1)
        else:
            raise AssertionError("Server shutdown timeout")
        server["stopped"] = True
        check("graceful SIG" + signal, exit_code == 0)
        check("stopped and lock released", cli("status", code=1)["status"] == "stopped")

    try:
        shell("mkdir " + quote(root))
        adb("push", str(binary), remote_binary, timeout=60)
        shell("chmod 700 " + quote(remote_binary))
        metadata = {}
        for key, prop in [("manufacturer", "ro.product.manufacturer"), ("model", "ro.product.model"),
                          ("android", "ro.build.version.release"), ("api", "ro.build.version.sdk"),
                          ("abis", "ro.product.cpu.abilist")]:
            metadata[key] = shell("getprop " + prop).strip()
        metadata["uname"] = shell("uname -a").strip()
        metadata["user"] = shell("id").strip()
        metadata["binary_sha256"] = shell("sha256sum " + quote(remote_binary)).split()[0]
        result["device"] = metadata
        untouched = root + "/help-must-not-create"
        run("--help", data_home=untouched)
        run("--version", data_home=untouched)
        check("help/version have no filesystem side effects", shell("test ! -e " + quote(untouched)) == "")
        version = cli("version")
        check("version JSON", version == {"core": "0.1.0"})
        check("initial status", cli("status", code=1)["status"] == "stopped")
        check("unicode and spaces in home", cli("config", "paths")["root"] == home)
        check("first-run directories", shell(" && ".join("test -d " + quote(home + "/" + p) for p in ("config", "data", "logs"))) == "")
        check("default configuration", cli("config")["http"] == {"host": "127.0.0.1", "port": 8787})
        check("relative home", cli("config", "paths", data_home="relative-home")["root"] == root + "/relative-home")
        check("MIRA_HOME", cli("config", "paths", data_home=None, mira_env=root + "/env-home")["root"] == root + "/env-home")
        check("CLI home overrides environment", cli("config", "paths", mira_env=root + "/env-home")["root"] == home)
        system = cli("system")
        result["system"] = system
        check("system architecture", system["architecture"] == "aarch64" and "arm64-v8a" in metadata["abis"] and shell("uname -m").strip() == "aarch64")
        meminfo = shell("cat /proc/meminfo")
        total = int(next(line.split()[1] for line in meminfo.splitlines() if line.startswith("MemTotal:"))) * 1024
        check("system resources", system["ram_total_bytes"] == total and total > 0 and system["cpu"]["logical_cores"] > 0 and bool(system["disks"]) and bool(system["network_interfaces"]))
        cli("config", "set", "node_name", "МИРА acceptance")
        cli("config", "set", "logging.level", "DEBUG")
        port = random.randrange(20000, 40000)
        cli("config", "set", "http.port", str(port))
        config_file = home + "/config/mira.toml"
        before = read(config_file)
        for key, value in [("http.port", "0"), ("http.port", "65536"), ("http.host", "invalid"), ("node_name", ""), ("unknown", "x")]:
            run("config", "set", key, value, code=1)
        check("invalid configuration preserves TOML", read(config_file) == before)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(cli, "config", "set", k, v) for k, v in [("node_name", "МИРА concurrent"), ("logging.level", "DEBUG")]]
            for future in futures:
                future.result()
        saved = cli("config")
        check("concurrent configuration writes", saved["node_name"] == "МИРА concurrent" and saved["logging"]["level"] == "debug")
        server = start(port)
        local = server["local"]
        check("running CLI and HTTP status match", cli("status") == json.loads(get(local, "/api/v1/status")[2]))
        check("HTTP version", json.loads(get(local, "/api/v1/version")[2]) == version)
        http_system = json.loads(get(local, "/api/v1/system")[2])
        check("CLI and HTTP system agree", all(http_system[k] == system[k] for k in ("os", "os_version", "architecture", "hostname", "cpu", "ram_total_bytes")))
        for path, marker in [("/", b"MIRA"), ("/app.js", b"fetch("), ("/style.css", b"{")]:
            status, headers, body = get(local, path)
            check("embedded asset " + path, status == 200 and marker in body and headers.get("Cache-Control") == "no-store")
        try:
            get(local, "/missing-route")
            raise AssertionError("Missing route did not return 404")
        except urllib.error.HTTPError as error:
            check("unknown route", error.code == 404 and json.loads(error.read()) == {"error": "not_found"})
        run("serve", code=1)
        check("second server rejected", "Another MIRA server" in commands[-1]["stderr"])
        next_port = port + 1
        cli("config", "set", "http.port", str(next_port))
        check("status follows live endpoint after config change", cli("status")["status"] == "ok")
        stop(server, "TERM")
        server = start(next_port)
        check("restart uses new port", cli("status")["status"] == "ok")
        check("configuration survives restart", cli("config")["node_name"] == "МИРА concurrent")
        stop(server, "INT")
        logs = shell("cat {}/*.log".format(quote(home + "/logs")))
        (report.parent / "android-core.log").write_text(logs, encoding="utf-8")
        check("persistent INFO DEBUG ERROR logs", all(level in logs for level in ("INFO", "DEBUG", "ERROR")))
        check("shutdown flushed to log", logs.count("MIRA Core stopped") >= 2 and logs.count("Shutdown requested") >= 2)
        original = read(config_file)
        try:
            shell("printf '%s' {} > {}".format(quote("[invalid TOML\n"), quote(config_file)))
            run("config", code=1)
            check("corrupt TOML preserved", read(config_file) == "[invalid TOML\n")
        finally:
            shell("printf '%s' {} > {}".format(quote(original), quote(config_file)))
        result["passed"] = True
    except Exception as error:
        result["passed"] = False
        result["error"] = str(error)
        print("FAIL " + str(error), flush=True)
    finally:
        for server in servers:
            if not server["stopped"]:
                try:
                    pid = server.get("pid") or int(read(server["pid_file"]).strip())
                    shell("kill -TERM " + str(pid), code=None)
                except Exception as error:
                    result.setdefault("cleanup_errors", []).append(str(error))
        for local in forwards:
            try:
                adb("forward", "--remove", "tcp:" + str(local))
            except Exception as error:
                result.setdefault("cleanup_errors", []).append(str(error))
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Report: " + str(report), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
