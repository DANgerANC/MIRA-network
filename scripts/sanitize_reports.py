"""Redact saved publication copies; never executes MIRA, builds or tests.

Best-effort redaction cannot replace a review of free-form output before sharing.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile


POLICY = "mira-report-redaction-v1"
NOTICE = "Privacy: sanitized publication copy; addresses and identifiers redacted."
SUMMARY_NOTICE = "> Сохранённые свидетельства обезличены: адреса, имена узлов и фактические локальные пути заменены маркерами. Результаты прежнего запуска сохранены; это не повторная проверка."
FIELDS = {
    "hostname": "[REDACTED_HOSTNAME]", "host_name": "[REDACTED_HOSTNAME]",
    "computer_name": "[REDACTED_HOSTNAME]", "mac_address": "[REDACTED_MAC]",
    "serial": "[REDACTED_DEVICE_ID]", "device_id": "[REDACTED_DEVICE_ID]",
    "machine_id": "[REDACTED_DEVICE_ID]", "mount_point": "[REDACTED_PATH]",
    "binary": "[REDACTED_PATH]", "work": "[REDACTED_PATH]",
    "root": "[REDACTED_PATH]", "config_file": "[REDACTED_PATH]",
    "data_dir": "[REDACTED_PATH]", "logs_dir": "[REDACTED_PATH]",
}
SECRET_FIELDS = {
    "password", "passwd", "api_key", "apikey", "access_token", "refresh_token",
    "client_secret", "private_key", "authorization", "cookie", "set_cookie",
    "token", "secret", "credentials",
}
PRIVATE_KEY = re.compile(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----", re.S)
HOST_KEYS = re.compile(r"-----BEGIN SSH HOST KEY KEYS-----.*?-----END SSH HOST KEY KEYS-----", re.S)
SSH_PUBLIC = re.compile(r"\b(?:ssh-rsa|ssh-ed25519|ecdsa-sha2-\S+)\s+[A-Za-z0-9+/=]{30,}[^\r\n]*")
FINGERPRINT = re.compile(r"\bSHA256:[A-Za-z0-9+/=]{20,}")
UUID = re.compile(r"(?i)\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b")
MAC = re.compile(r"(?i)(?<![0-9a-f:])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f:])")
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?![\w.])")
IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F]{0,4}(?:%[\w.-]+)?(?:/\d{1,3})?(?![\w:])")
WINDOWS_PATH = re.compile(r'''(?i)(?<!\w)[a-z]:[\\/][^\r\n"'`<>]*''')
UNIX_PATH = re.compile(r'''(?<!\w)/(?:home|Users|tmp|var/tmp|data/local/tmp)/[^\s"'`<>;|&)]+''')
ADB_DEVICE = re.compile(r"(?m)^(\s*)[^\s<>\[\]]+\s+(device|offline|unauthorized)(\s.*)?$")
ADB_SERIAL = re.compile(r"(?i)(\badb(?:\.exe)?\s+-s\s+)(?!<device>|\[REDACTED)[^\s\"']+")
ACCOUNT = re.compile(r"\b((?:uid|gid|groups)=\d+)\(([^)]+)\)")
LOGIN = re.compile(r"(?<!\w)[\w.-]+@[\w.-]+(?=[:\s])")
SECRET = re.compile(r'''(?i)(\b(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\b\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)''')


def embedded_json(value):
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return None


def collect(value, replacements):
    if isinstance(value, dict):
        for key, child in value.items():
            marker = FIELDS.get(key.lower())
            if marker and isinstance(child, str) and len(child) > 3 and not child.startswith("[REDACTED_"):
                replacements[child] = marker
            collect(child, replacements)
    elif isinstance(value, list):
        for child in value:
            collect(child, replacements)
    elif isinstance(value, str):
        child = embedded_json(value)
        if child is not None:
            collect(child, replacements)


class Redactor:
    def __init__(self, replacements):
        self.replacements = sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True)

    @staticmethod
    def ip(match):
        try:
            ipaddress.ip_address(match.group().split("/", 1)[0].split("%", 1)[0])
        except ValueError:
            return match.group()
        return "[REDACTED_IP]"

    def text(self, value, summary=False):
        value = PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", value)
        value = HOST_KEYS.sub("[REDACTED_HOST_KEYS]", value)
        value = SSH_PUBLIC.sub("[REDACTED_HOST_KEY]", value)
        value = FINGERPRINT.sub("[REDACTED_FINGERPRINT]", value)
        for original, marker in self.replacements:
            # Generic documented example paths are distinct from actual telemetry.
            if summary and marker == "[REDACTED_PATH]":
                continue
            value = value.replace(original, marker)
            value = value.replace(json.dumps(original, ensure_ascii=False)[1:-1], marker)
        value = SECRET.sub(r"\1[REDACTED_SECRET]", value)
        value = UUID.sub("[REDACTED_UUID]", value)
        value = MAC.sub("[REDACTED_MAC]", value)
        value = ADB_DEVICE.sub(r"\1[REDACTED_DEVICE_ID] \2", value)
        value = ADB_SERIAL.sub(r"\1[REDACTED_DEVICE_ID]", value)
        value = ACCOUNT.sub(lambda m: m.group(0) if m[2] in {"root", "shell"} else m[1] + "([REDACTED_USER])", value)
        value = LOGIN.sub("[REDACTED_USER]@[REDACTED_HOSTNAME]", value)
        value = UNIX_PATH.sub("[REDACTED_PATH]", value)
        if not summary:
            value = WINDOWS_PATH.sub("[REDACTED_PATH]", value)
            value = IPV4.sub(self.ip, value)
            value = IPV6.sub(self.ip, value)
        return value

    def data(self, value, field="", context=""):
        if field.replace("-", "_") in SECRET_FIELDS and value:
            return "[REDACTED_SECRET]"
        if isinstance(value, dict):
            return {key: self.data(child, key.lower(), context + "/" + field) for key, child in value.items()}
        if isinstance(value, list):
            return [self.data(child, field, context) for child in value]
        if not isinstance(value, str):
            return value
        if value and field in FIELDS:
            return FIELDS[field]
        if value and field == "name" and ("/disks" in context or "/network_interfaces" in context):
            return "[REDACTED_DEVICE_NAME]"
        child = embedded_json(value)
        if child is not None:
            return json.dumps(self.data(child), ensure_ascii=False, indent=2) + ("\n" if value.endswith("\n") else "")
        return self.text(value)


def write_changed(path, content):
    if path.read_text(encoding="utf-8-sig") == content:
        return False
    # Atomic replacement; no unredacted backup beside publication copies.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, default=Path("docs/validation"))
    parser.add_argument("--summaries", type=Path, nargs="*", default=[])
    args = parser.parse_args()
    root = args.reports.resolve(strict=True)
    if not root.is_dir():
        parser.error("--reports must name a directory of publication copies")
    reports, objects, replacements = [], {}, {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Linked report paths are not supported")
        if not path.is_file() or path.suffix.lower() not in {".json", ".txt", ".log"}:
            continue
        path.resolve(strict=True).relative_to(root)
        if path.name == "source-sha256.json":
            continue  # Historical relative source names/hashes are not host data.
        reports.append(path)
        if path.suffix.lower() == ".json":
            objects[path] = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(objects[path], dict):
                raise ValueError("Diagnostic report JSON must contain an object")
            collect(objects[path], replacements)
    for path in args.summaries:
        if path.is_symlink() or path.suffix.lower() != ".md" or not path.is_file():
            raise ValueError("--summaries must name regular Markdown publication copies")
    redactor, changed = Redactor(replacements), 0
    for path in reports:
        if path in objects:
            result = redactor.data(objects[path])
            result["privacy"] = {"sanitized": True, "policy": POLICY, "notice": "Publication copy; original check outcomes retained. Addresses, identifiers and local paths redacted; review free-form output before sharing."}
            content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        else:
            content = redactor.text(path.read_text(encoding="utf-8-sig"))
            if not content.startswith(NOTICE + "\n"):
                content = NOTICE + "\n" + content
        changed += write_changed(path, content)
    for path in args.summaries:
        content = redactor.text(path.read_text(encoding="utf-8-sig"), summary=True)
        if SUMMARY_NOTICE not in content:
            heading, separator, body = content.partition("\n")
            content = heading + separator + "\n" + SUMMARY_NOTICE + "\n" + body
        changed += write_changed(path, content)
    print(json.dumps({"files_processed": len(reports) + len(args.summaries), "files_changed": changed, "policy": POLICY}))


if __name__ == "__main__":
    main()
