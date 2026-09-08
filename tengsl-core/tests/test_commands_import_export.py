from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[2]

def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")

def test_command_ack_and_journal_are_wired():
    main = read("tengsl-core/app/main.py")
    worker = read("tengsl-core/app/mqtt_worker.py")
    edge = read("tengsl-edge/app/main.py")
    pub = read("tengsl-edge/app/mqtt_publisher.py")
    assert "CommandLogModel" in main
    assert '@app.get("/api/commands"' in main
    assert "command_id" in main
    assert "command_ack" in worker
    assert "publish_command_ack" in edge
    assert "publish_command_ack" in pub

def test_import_export_endpoints_exist():
    main = read("tengsl-core/app/main.py")
    assert '@app.get("/api/objects/export")' in main
    assert '@app.post("/api/objects/import"' in main
    assert "ObjectsImportPayload" in main
    assert '@app.get("/api/objects/{object_id}/export")' in main
    assert '@app.post("/api/objects/{object_id}/import"' in main


def test_console_supports_per_object_import_export():
    app = read("tengsl-console/app.js")
    index = read("tengsl-console/index.html")
    dockerfile = read("tengsl-console/Dockerfile")
    assert "exportObject" in app
    assert "importObjectFile" in app
    assert "importSingleObjectFromFile" in app
    assert 'id="importObjectFile"' in index
    assert "COPY assets /usr/share/nginx/html/assets" in dockerfile
