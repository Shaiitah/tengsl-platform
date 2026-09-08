# TENGSL — полный стек для пилота

Собирает edge-агент, брокер MQTT, PostgreSQL и backend (FastAPI) в одном
`docker-compose.yml`.

```
tengsl-platform/
├── docker-compose.yml
├── .env.example              # скопируй в .env
├── mosquitto.conf            # тестовый брокер без TLS/авторизации
├── edge-agent-config.yaml    # конфиг edge-агента (адрес Modbus, зоны)
├── tengsl-core/            # FastAPI + PostgreSQL + MQTT-воркер
├── tengsl-edge/         # опрос Modbus, публикация в MQTT
└── tengsl-console/          # статический дашборд (HTML/CSS/JS, без сборки)
```

## Запуск

```bash
cp .env.example .env
# отредактируй edge-agent-config.yaml — как минимум адрес modbus.host

docker compose up --build
```

После старта:
- **Дашборд**: http://localhost:8080
- **Backend API**: http://localhost:8000/api/health
- **Список объектов**: http://localhost:8000/api/objects
- **События объекта**: http://localhost:8000/api/objects/obj-001/events
- **WebSocket с live-событиями**: `ws://localhost:8000/ws/events` (или
  `ws://localhost:8000/ws/events?object_id=obj-001` для фильтра по объекту)

Объект (`ObjectModel`) создаётся в БД автоматически при первом полученном
событии или статусе от агента — отдельно регистрировать объекты заранее не
нужно для пилота.

## Проверка без реального прибора

Пока нет доступа к реальному С2000-ПП, можно проверить всю цепочку
backend + MQTT + WebSocket, опубликовав тестовое событие вручную. Формат
события отражает реальную структуру ответа прибора (два приоритетных
кода состояния в регистре) — здесь событие уже декодировано агентом,
поэтому просто задаём готовый `event_type`/`label`:

```bash
docker compose exec mqtt-broker mosquitto_pub \
  -t "tengsl/obj-001/events" \
  -m '{"object_id":"obj-001","zone_number":1,"zone_name":"Тест","device_type":"С2000-ДИП34А","register_address":40000,"raw_value":9496,"primary_code":37,"secondary_code":24,"event_type":"alarm","label":"Пожар","timestamp":"2026-09-05T12:00:00Z"}'
```

## Откуда взяты адреса регистров и коды событий

Карта зон (`config.example.yaml`) и таблица кодов событий (`status_codes`)
в edge-агенте построены по официальному руководству по эксплуатации
«С2000-ПП» (АЦДР.426469.020 РЭп, Изм.17 от 21.08.2025), раздел 1.1.5:
- формула адреса регистра состояния зоны: `40000 + (№ зоны Modbus - 1)`;
- ответ на запрос состояния зоны — два приоритетных кода события
  (старший и младший байт регистра), расшифровка кодов — таблица 1.1.5.12.1
  этого же документа.

Категории `event_type` (normal/alarm/attention/fault/disabled/unknown) —
наша собственная классификация кодов Bolid для окраски дашборда, не часть
документации производителя. При добавлении новых типов зон или приборов
сверяйся с таблицей 1.1.5.12.1 и таблицей зон в UPROG на своём объекте.

Событие должно появиться в `GET /api/objects/obj-001/events` и прилететь
всем подключённым WebSocket-клиентам.

## Где хранятся данные Postgres

Данные БД лежат в `./data/postgres` рядом с `docker-compose.yml` (bind mount,
не именованный Docker volume) — так их проще найти, забэкапить или удалить
руками, не разбираясь, куда Docker прячет volumes по умолчанию.

Из-за этого `docker compose down -v` **не удалит** данные Postgres — флаг
`-v` чистит только именованные volumes, а bind mount он не трогает. Чтобы
сбросить БД с нуля (например, после изменения схемы моделей backend'а):

```bash
docker compose down
rm -rf data/postgres
docker compose up -d
```

## Важные ограничения текущей версии (для пилота, не для прода)

- **Нет аутентификации в REST API и WebSocket** — любой, кто достучится до
  порта 8000, видит все события. Для реального использования нужен слой
  авторизации (API-ключи или JWT) — сейчас его нет.
- **MQTT без TLS и без логина/пароля** в тестовой конфигурации —
  `mosquitto.conf` явно помечен как тестовый. Перед выходом за пределы
  локальной машины включи `ORION_MQTT_USE_TLS=true`, задай
  `ORION_MQTT_USERNAME`/`ORION_MQTT_PASSWORD` и настрой Mosquitto
  соответствующим образом (listener 8883 + сертификаты + `password_file`).
- **Схема БД создаётся через `create_all`**, миграций (Alembic) нет — на
  первом этапе это ок, но при изменении моделей на существующей БД
  потребуется миграция вручную.
- **WebSocket-рассылка идёт в памяти одного процесса backend** — если
  когда-нибудь понадобится несколько реплик backend за балансировщиком,
  нужен будет общий broker (например, публиковать в Redis pub/sub и
  рассылать оттуда), иначе клиенты на разных репликах не увидят события
  друг друга.

## Modbus / С2000-ПП compatibility note

The edge agent keeps the Modbus addresses exactly as documented by С2000-ПП and by the previously working single-object agent. For zone state, `40000 + (zone_number - 1)` is passed directly to pymodbus using function 03 / Holding Registers. The agent does not subtract 40000 (or 30000/60000) before making the request.

Zone control uses function 06 at `40000 + (zone_number - 1)` with С2000-ПП command values: `24` arm, `109` disarm, `111` enable control, `112` disable control.

## Коды событий

Справочник кодов событий хранится в PostgreSQL в таблице `event_codes` и автоматически заполняется при старте backend стандартными кодами из руководства по эксплуатации С2000-ПП. Для каждого кода можно изменить подпись, тип отображения, описание, приоритет и включённость через вкладку «Настройки» в dashboard.

API:
- `GET /api/event-codes`
- `POST /api/event-codes`
- `PUT /api/event-codes/{code}`
- `DELETE /api/event-codes/{code}`

Новые события от edge-agent получают актуальную расшифровку из БД backend. Сырой `primary_code` при этом сохраняется без изменений.

## Тесты

Backend:
```bash
cd tengsl-core
pytest -q
```

Edge-agent:
```bash
cd tengsl-edge
pytest -q
```

Для локальной разработки зависимости тестов находятся в `requirements-dev.txt`.

## TENGSL branding

Проект переименован в **TENGSL** — самостоятельную платформу мониторинга и интеграции оборудования.

Основные компоненты:
- `tengsl-core` — backend/API/MQTT
- `tengsl-edge` — edge-agent
- `tengsl-console` — web-console

По умолчанию MQTT namespace — `tengsl`. Для плавной миграции старых агентов backend также принимает legacy topics `orion/...`; существующие установки могут сохранить `ORION_MQTT_TOPIC_PREFIX=orion` до плановой миграции.

### Migration note

`ORION_*` переменные окружения сохранены для совместимости со старыми установками; при этом новые примеры используют namespace `tengsl`. Backend временно подписывается также на legacy MQTT namespace `orion`.

## Авторизация и разграничение доступа

В backend добавлена сессионная аутентификация и RBAC с доступом к объектам.

### Роли

- `admin` — полный доступ;
- `dispatcher` — мониторинг и управление доступными зонами;
- `operator` — мониторинг доступных объектов и просмотр журнала;
- `viewer` — только просмотр.

Права проверяются на backend. Для неадминистративных пользователей дополнительно проверяется принадлежность объекта к `user_objects`; WebSocket событий также требует авторизацию и доступ к выбранному объекту.

### Первый администратор

При первом запуске создаётся администратор из переменных `TENGSL_ADMIN_USERNAME` и `TENGSL_ADMIN_PASSWORD`. Перед эксплуатацией обязательно замените пароль.

### Пользователи

В консоли появился раздел **Пользователи** для администратора: создание, изменение роли, пароля, активности и списка доступных объектов, а также JSON импорт/экспорт.

Экспорт пользователей не содержит паролей. При импорте новой записи пароль необходимо указать; для существующей записи пароль можно оставить пустым.

### Начальный администратор

При первом запуске автоматически создаётся администратор:

- логин: `admin`
- пароль: `password`

Пароль можно изменить в разделе **Настройки → Моя учётная запись**. Для рабочего/промышленного контура начальный пароль рекомендуется сменить сразу после первого входа.

## ntfy notifications

TENGSL can deliver event notifications through a self-hosted `binwiederhier/ntfy` service. The ntfy web UI is intentionally not embedded into the TENGSL dashboard. It is reverse-proxied under `/ntfy/` on the same public domain and is also exposed on host port `8082` for direct local access.

The TENGSL dashboard stays on `8080`. The default public domain is `https://morianas.ru`; change `ORION_PUBLIC_BASE_URL` in `.env` for another deployment. ntfy is exposed on the supported subdomain `https://ntfy.morianas.ru`; change `ORION_NTFY_PUBLIC_URL` and `NTFY_BASE_URL` when using another domain. Point the `ntfy.<domain>` DNS record and reverse proxy to the dashboard/nginx service. ntfy must not be configured under a URL sub-path.

Notification topics are private per TENGSL user and derived from `ORION_NTFY_TOPIC_SECRET`. Users can copy their subscription URL from **Настройки → ntfy** into the official ntfy client. Event delivery is filtered by the same object permissions as the TENGSL account and by per-object severity preferences.

Before production, set a long random `ORION_NTFY_TOPIC_SECRET` and a unique `ORION_NTFY_PUBLISHER_TOKEN` in `.env`.


### Диагностика и ntfy

Вкладка «Диагностика» не считает отсутствие файла внешних автотестов ошибкой: при `ORION_TEST_RESULTS_PATH` пустом результат будет «Не запускались». Если внешний runner передаёт результаты, статус вычисляется по фактическому счётчику `failed`. ntfy не размещается под `/ntfy`: для домена `morianas.ru` используется `https://ntfy.morianas.ru`, потому что сам ntfy не поддерживает URL sub-path. DNS `ntfy.morianas.ru` должен указывать на тот же reverse proxy, который передаёт запросы на dashboard/nginx.

## Переключение Development / Production из Dashboard

Проект использует один `docker-compose.yml`, совместимый с Synology Container Manager. Контейнеры не получают доступ к Docker socket.

Текущий runtime-режим хранится в `runtime/mode`. По умолчанию используется `production`; в поставляемом dev-наборе файл может содержать `development`.

На вкладке **Администрирование** администратор с правом `system.manage` может переключить режим. Backend атомарно обновляет `runtime/mode`, отправляет Edge Agent команду штатного перезапуска через MQTT и завершает собственный процесс; Docker `restart: unless-stopped` запускает их снова. При старте Backend/Agent читают режим из runtime-файла:

- `development` — Backend с `uvicorn --reload`, Edge Agent с `watchfiles`;
- `production` — обычный запуск без hot reload.

Переключение не требует Docker socket и не выполняет произвольные shell-команды. Пересборка нужна только при изменении Dockerfile, системных или Python-зависимостей.
