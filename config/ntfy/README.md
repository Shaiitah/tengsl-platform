# TENGSL ntfy

TENGSL использует ntfy как отдельный канал доставки событий. Web UI ntfy не встраивается в TENGSL и доступен отдельно на `http://<host>:8082`.

Перед production замените `ORION_NTFY_TOPIC_SECRET` и `ORION_NTFY_PUBLISHER_TOKEN` в `.env`.
