#!/usr/bin/env python3
"""Exercise PocketBase replication and crash recovery using disposable data."""

import argparse
import contextlib
import datetime
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def binary_path(value):
    found = shutil.which(value)
    if not found:
        raise ValueError(f"Executable introuvable : {value}")
    return str(Path(found).resolve())


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(base, path, method="GET", body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token
    req = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        content = response.read()
        return json.loads(content) if content else None


def wait_ready(base, process):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("PocketBase s'est arrêté ; consulter pocketbase.log.")
        try:
            request(base, "/api/health")
            return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)
    raise TimeoutError("PocketBase ne répond pas après 20 secondes.")


def rows(path):
    with contextlib.closing(sqlite3.connect(str(path))) as db:
        return [list(row) for row in db.execute(
            'SELECT id, seq, phase, payload FROM recovery_probe ORDER BY seq'
        )]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pocketbase", default="pocketbase")
    parser.add_argument("--litestream", default="litestream")
    parser.add_argument("--work-dir", type=Path, help="Nouveau dossier réservé au test")
    parser.add_argument("--replica-url", help="Racine S3/file ; un préfixe de test unique sera ajouté")
    parser.add_argument("--endpoint", help="Endpoint S3 ; clés via variables Litestream")
    parser.add_argument("--write-seconds", type=int, default=35)
    args = parser.parse_args()
    if not 5 <= args.write_seconds <= 300:
        parser.error("--write-seconds doit être compris entre 5 et 300")
    os.umask(0o077)
    pocketbase = binary_path(args.pocketbase)
    litestream = binary_path(args.litestream)
    if args.work_dir:
        root = args.work_dir.resolve()
        root.mkdir(parents=True, exist_ok=False)
    else:
        root = Path(tempfile.mkdtemp(prefix="pockethost-recovery-"))
    source = root / "source"
    restored = root / "restored"
    restored.mkdir()
    replica = (
        args.replica_url.rstrip("/") + "/recovery-test-" + secrets.token_hex(8)
        if args.replica_url else (root / "replica").as_uri()
    )
    report = {
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "work_dir": str(root),
        "destination": "external" if args.replica_url else "local-file",
        "monitor_interval": "10s",
        "sync_interval": "10s",
        "checks": {},
    }
    processes = []
    logs = []

    def launch(command, name):
        log = (root / f"{name}.log").open("wb")
        logs.append(log)
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    def run(command):
        return subprocess.run(command, capture_output=True, text=True, timeout=30)

    def restore(name):
        output = root / f"{name}.db"
        # Every output is new: no replacement of a running database.
        result = run([
            litestream, "restore", "-no-expand-env", "-config", str(root / "litestream.yml"),
            "-o", str(output), str(source / "data.db"),
        ])
        with (root / "restore.log").open("a") as log:
            log.write(result.stdout + result.stderr)
        if result.returncode:
            return None
        return output

    def await_replication(expected, label):
        (root / f"expected-{label}.json").write_text(json.dumps(expected, indent=2) + "\n")
        started = time.monotonic()
        attempt = 0
        while time.monotonic() - started < 75:
            if replication.poll() is not None:
                raise RuntimeError("Litestream s'est arrêté ; consulter litestream.log.")
            attempt += 1
            output = restore(f"{label}-{attempt}")
            if output:
                try:
                    actual = rows(output)
                    if actual == expected:
                        return round(time.monotonic() - started, 3)
                    (root / f"actual-{label}.json").write_text(json.dumps(actual, indent=2) + "\n")
                except sqlite3.OperationalError:
                    pass
            time.sleep(1)
        raise TimeoutError(f"Réplication non confirmée après 75 secondes : {label}")

    try:
        for name, executable in (("pocketbase", pocketbase), ("litestream", litestream)):
            version = run([executable, "--version" if name == "pocketbase" else "version"])
            if version.returncode:
                raise RuntimeError(f"Impossible de lire la version de {name}")
            report[name] = version.stdout.strip()
        email = "recovery-test@example.invalid"
        password = secrets.token_urlsafe(32)
        setup = run([
            pocketbase, "superuser", "upsert", email, password,
            "--dir", str(source),
        ])
        if setup.returncode:
            raise RuntimeError("Création du superuser de test impossible.")
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        server = launch([
            pocketbase, "serve", "--dir", str(source), "--http", f"127.0.0.1:{port}",
        ], "pocketbase")
        wait_ready(base, server)
        auth = request(base, "/api/collections/_superusers/auth-with-password", "POST", {
            "identity": email, "password": password,
        })
        token = auth["token"]
        request(base, "/api/collections", "POST", {
            "name": "recovery_probe", "type": "base",
            "fields": [
                {"name": "seq", "type": "number"},
                {"name": "phase", "type": "text"},
                {"name": "payload", "type": "text"},
            ],
        }, token)
        api = "/api/collections/recovery_probe/records"

        def create(seq, phase):
            return request(base, api, "POST", {
                "seq": seq, "phase": phase, "payload": f"test-{seq}-" + "x" * 256,
            }, token)

        initial = create(0, "initial")
        config = [
            "logging:", "  level: info", "  type: text", "  stderr: true",
            "snapshot:", "  interval: 1h", "  retention: 72h",
            "validation:", "  interval: 6h", "dbs:",
            f"  - path: {json.dumps(str(source / 'data.db'))}",
            "    monitor-interval: 10s", "    checkpoint-interval: 1m",
            "    busy-timeout: 30s", "    replica:",
            f"      url: {json.dumps(replica)}", "      sync-interval: 10s",
        ]
        if args.endpoint:
            config.extend([
                f"      endpoint: {json.dumps(args.endpoint)}",
                '      region: "auto"', "      force-path-style: true",
            ])
        (root / "litestream.yml").write_text("\n".join(config) + "\n")
        replication = launch([
            litestream, "replicate", "-no-expand-env", "-config", str(root / "litestream.yml"),
        ], "litestream")
        print(f"Dossier du test : {root}", flush=True)
        print("Vérification de la première copie…", flush=True)
        report["initial_replication_seconds"] = await_replication(rows(source / "data.db"), "initial")
        report["checks"]["initial_snapshot"] = True

        request(base, api + "/" + initial["id"], "PATCH", {"phase": "updated"}, token)
        for seq in range(1, 21):
            create(seq, "confirmed")
        deleted = create(21, "must-be-deleted")
        request(base, api + "/" + deleted["id"], "DELETE", token=token)
        confirmed = rows(source / "data.db")
        print("Vérification des créations, modification et suppression…", flush=True)
        report["incremental_replication_seconds"] = await_replication(confirmed, "incremental")
        report["checks"]["create_update_delete"] = True

        print(f"Écritures pendant {args.write_seconds}s, puis arrêt brutal…", flush=True)
        writes = []
        started = time.monotonic()
        for offset in range(args.write_seconds):
            record = create(100 + offset, "before-crash")
            writes.append({"id": record["id"], "at": time.monotonic()})
            delay = started + offset + 1 - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        expected_before_crash = rows(source / "data.db")
        crashed_at = time.monotonic()
        # SIGKILL prevents the final sync performed during a graceful shutdown.
        server.kill()
        replication.kill()
        server.wait(timeout=10)
        replication.wait(timeout=10)
        (root / "expected-before-crash.json").write_text(json.dumps(expected_before_crash, indent=2) + "\n")
        source.rename(root / "crashed-source")

        print("Restauration depuis la réplique seule…", flush=True)
        restore_started = time.monotonic()
        recovered = restore("recovered")
        if recovered is None:
            raise RuntimeError("Restauration impossible ; consulter restore.log.")
        recovered_rows = rows(recovered)
        report["restore_seconds"] = round(time.monotonic() - restore_started, 3)
        expected_by_id = {row[0]: row for row in expected_before_crash}
        recovered_ids = {row[0] for row in recovered_rows}
        if any(expected_by_id.get(row[0]) != row for row in recovered_rows):
            raise AssertionError("La base restaurée contient des données inattendues.")
        if any(row not in recovered_rows for row in confirmed):
            raise AssertionError("Des écritures confirmées dans la réplique ont été perdues.")
        recovered_writes = [write for write in writes if write["id"] in recovered_ids]
        lost_writes = [write for write in writes if write["id"] not in recovered_ids]
        if lost_writes and recovered_writes and min(w["at"] for w in lost_writes) < max(w["at"] for w in recovered_writes):
            raise AssertionError("Les écritures restaurées ne forment pas un préfixe cohérent.")
        if not recovered_writes:
            raise AssertionError("Aucune écriture de la phase continue n'a été répliquée.")
        report["checks"]["confirmed_writes_preserved_after_crash"] = True
        report["checks"]["consistent_write_prefix"] = True
        report["records_before_crash"] = len(expected_before_crash)
        report["records_recovered"] = len(recovered_rows)
        report["recent_writes_lost"] = len(lost_writes)
        report["last_recovered_write_age_seconds"] = round(crashed_at - recovered_writes[-1]["at"], 3)
        with contextlib.closing(sqlite3.connect(recovered)) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise AssertionError(f"Échec integrity_check : {integrity}")
        report["checks"]["sqlite_integrity"] = True

        shutil.move(str(recovered), restored / "data.db")
        new_port = free_port()
        new_base = f"http://127.0.0.1:{new_port}"
        recovered_server = launch([
            pocketbase, "serve", "--dir", str(restored), "--http", f"127.0.0.1:{new_port}",
        ], "restored-pocketbase")
        wait_ready(new_base, recovered_server)
        new_token = request(new_base, "/api/collections/_superusers/auth-with-password", "POST", {
            "identity": email, "password": password,
        })["token"]
        restored_records = request(new_base, api + "?perPage=500&sort=seq", token=new_token)
        actual = [[r["id"], r["seq"], r["phase"], r["payload"]] for r in restored_records["items"]]
        if actual != recovered_rows:
            raise AssertionError("Les données exposées par PocketBase restauré diffèrent.")
        report["checks"]["pocketbase_restarts_and_serves_restored_records"] = True
        report["result"] = "passed"
    except Exception as error:
        report["result"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report.setdefault("result", "interrupted")
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        for log in logs:
            log.close()
        (root / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
