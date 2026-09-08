from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_ack_topic_and_payload_are_present():
    config = (ROOT / "tengsl-edge/app/config.py").read_text(encoding="utf-8")
    publisher = (ROOT / "tengsl-edge/app/mqtt_publisher.py").read_text(encoding="utf-8")
    main = (ROOT / "tengsl-edge/app/main.py").read_text(encoding="utf-8")
    assert "command_ack_topic" in config
    assert "publish_command_ack" in publisher
    assert 'data.get("command_id")' in main
