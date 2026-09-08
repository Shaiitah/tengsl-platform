import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tengsl-core"))

from app.event_codes import DEFAULT_EVENT_CODES, EVENT_TYPE_CHOICES
from app.schemas import EventCodeCreate, EventCodeUpdate


def test_default_code_23():
    code = DEFAULT_EVENT_CODES[23]
    assert code.label == "Задержка взятия"
    assert code.event_type == "info"


def test_default_code_24():
    code = DEFAULT_EVENT_CODES[24]
    assert code.label == "Взятие входа на охрану"
    assert code.event_type == "normal"


def test_supported_event_type_choices():
    assert "fault" in EVENT_TYPE_CHOICES
    assert "unknown" in EVENT_TYPE_CHOICES


def test_event_code_schema_validation():
    item = EventCodeCreate(code=23, label="Задержка", event_type="info")
    assert item.code == 23

    updated = EventCodeUpdate(enabled=False)
    assert updated.enabled is False
