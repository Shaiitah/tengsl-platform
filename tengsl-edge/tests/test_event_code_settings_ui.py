from pathlib import Path


def test_dashboard_event_code_api_and_error_state_are_wired():
    root = Path(__file__).parents[2].joinpath("tengsl-console")
    source = (root / "app.js").read_text(encoding="utf-8")
    assert 'fetchJson("/api/event-codes")' in source
    assert "Ошибка загрузки:" in source
    assert "showAddEventCodeForm" in source


def test_dashboard_cache_bust_is_present():
    html = Path(__file__).parents[2].joinpath("tengsl-console", "index.html").read_text(encoding="utf-8")
    assert 'app.js?v=15' in html
    assert 'config.js?v=15' in html


def test_dashboard_monitoring_and_notification_stack_are_present():
    root = Path(__file__).parents[2].joinpath("tengsl-console")
    js = (root / "app.js").read_text(encoding="utf-8")
    css = (root / "styles.css").read_text(encoding="utf-8")
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "renderMonitoring" in js
    assert "notification-stack" in css
    assert "notificationHistory" in js
    assert 'data-tab="notifications"' in html
    assert 'id="notificationCenterList"' in html
    assert 'id="monitorOnline"' in html
