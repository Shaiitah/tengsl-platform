from pathlib import Path


def test_event_code_schema_migration_is_present():
    source = Path(__file__).parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS source" in source
    assert "ADD COLUMN IF NOT EXISTS enabled" in source
    assert "_ensure_event_code_schema()" in source


def test_stats_contains_event_type_breakdown():
    source = Path(__file__).parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")
    assert "events_by_type" in source
    assert "alarm_events_24h" in source
    assert "unknown_events_24h" in source


def test_stats_orders_query_before_async_execute():
    source = Path(__file__).parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")
    assert "session.execute(objects_query.order_by(ObjectModel.object_id))" in source
    assert "objects_result = objects_result.order_by(ObjectModel.object_id)" not in source
