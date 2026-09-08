import json

from app.notifications import subscribe_url, user_topic


def test_user_topic_is_stable_and_private():
    assert user_topic(1).startswith("tengsl-user-1-")
    assert user_topic(1) == user_topic(1)
    assert user_topic(1) != user_topic(2)


def test_subscribe_url_uses_configured_public_domain():
    url = subscribe_url(1)
    assert url.startswith("https://ntfy.morianas.ru/tengsl-user-1-")


def test_ntfy_json_payload_accepts_unicode():
    payload = {
        "topic": user_topic(1),
        "title": "TENGSL — пожар в зоне 1",
        "message": "Событие обнаружено",
        "priority": "max",
        "tags": ["tengsl", "rotating_light"],
    }
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    assert "пожар" in encoded.decode("utf-8")


def test_subscribe_url_has_no_ntfy_path_prefix():
    url = subscribe_url(42)
    assert "/ntfy/" not in url
    assert url.startswith("https://ntfy.morianas.ru/tengsl-user-42-")
