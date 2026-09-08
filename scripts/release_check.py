#!/usr/bin/env python3
"""TENGSL release contract checker.

This script intentionally validates presence of required capabilities without
modifying application source. It is a regression guard for release builds.
"""
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "tengsl-core/app/main.py"
APPJS = ROOT / "tengsl-console/app.js"
INDEX = ROOT / "tengsl-console/index.html"
EDGE_MAIN = ROOT / "tengsl-edge/app/main.py"
REMOTE = ROOT / "tengsl-edge/app/remote_config.py"
FEATURES = ROOT / "PLATFORM_FEATURES.yml"

errors = []

def read(path):
    if not path.exists():
        errors.append(f"missing file: {path.relative_to(ROOT)}")
        return ""
    return path.read_text(encoding="utf-8", errors="replace")

main = read(MAIN)
appjs = read(APPJS)
index = read(INDEX)
edge = read(EDGE_MAIN)
remote = read(REMOTE)

if FEATURES.exists():
    manifest = yaml.safe_load(FEATURES.read_text(encoding="utf-8")) or {}
else:
    errors.append("missing PLATFORM_FEATURES.yml")
    manifest = {}

checks = [
    ("database.backup", '@app.post("/api/admin/database/backup")' in main),
    ("database.backups", '@app.get("/api/admin/database/backups")' in main),
    ("database.restore", '@app.post("/api/admin/database/restore/{import_id}")' in main),
    ("database.merge", '@app.post("/api/admin/database/merge/{import_id}")' in main),
    ("diagnostics.runtime_health", '@app.get("/api/health/detailed")' in main),
    ("diagnostics.pytest", 'pytest' in main and 'diagnostics/run' in main),
    ("agent.api_auth", 'X-TENGSL-Agent-Token' in main and 'agent_api_token' in main),
    ("agent.remote_config", 'api_token' in remote),
    ("notifications_ntfy", 'ntfy' in main.lower()),
    ("rbac", 'system.manage' in main and 'permissions' in main),
    ("alembic", (ROOT / "tengsl-core/alembic/versions").exists()),
    ("dashboard.database_panel", 'База данных' in index),
    ("dashboard.database_functions", all(
        x in appjs for x in [
            'createDatabaseBackup',
            'loadDatabaseBackups',
            'downloadDatabaseBackup',
            'importDatabaseBackup',
            'restoreDatabaseBackup',
            'mergeDatabaseBackup',
        ]
    )),
    ("edge.agent_control", 'tengsl/agent/control' in edge),
]

def enabled(path):
    cur = manifest.get("features", {})
    for part in path.split("."):
        if not isinstance(cur, dict):
            return False
        cur = cur.get(part)
    return cur is True

for name, ok in checks:
    if enabled(name) and not ok:
        errors.append(f"required feature missing: {name}")

if errors:
    print("TENGSL RELEASE CHECK: FAILED")
    for e in errors:
        print(" -", e)
    sys.exit(1)

print("TENGSL RELEASE CHECK: OK")
for name, ok in checks:
    if enabled(name):
        print(" ✓", name)
