"use strict";

/* -
Состояние приложения
- */
const state = {
  objects: [],
  agentStatus: {},
  selectedObjectId: null,
  zones: [],
  events: [],
  eventCodes: [],
  commands: [],
  alerts: [],
  lastNotificationKey: null,
  notifications: [],
  currentUser: null,
};

const WS_MAX_RECONNECT_DELAY = 30000;
let ws = null;
let wsReconnectDelay = 1000;

/* -
DOM-элементы
- */
const el = {
  objectList: document.getElementById("objectList"),
  connIndicator: document.getElementById("connIndicator"),
  banner: document.getElementById("banner"),
  bannerStatus: document.getElementById("bannerStatus"),
  bannerDetail: document.getElementById("bannerDetail"),
  zoneGrid: document.getElementById("zoneGrid"),
  zoneEmptyState: document.getElementById("zoneEmptyState"),
  zonesMeta: document.getElementById("zonesMeta"),
  eventLog: document.getElementById("eventLog"),
  eventEmptyState: document.getElementById("eventEmptyState"),
  manageZonesBtn: document.getElementById("manageZonesBtn"),
};

/* -
Авторизация и права
- */
function hasPermission(permission) {
  return Boolean(state.currentUser?.permissions?.includes(permission));
}

function showLogin() {
  const overlay = document.getElementById("loginOverlay");
  if (overlay) overlay.hidden = false;
}

function hideLogin() {
  const overlay = document.getElementById("loginOverlay");
  if (overlay) overlay.hidden = true;
}

async function loadCurrentUser() {
  const response = await fetch(`${window.TENGSL_CONFIG.apiBase}/api/auth/me`, { credentials: "include" });
  if (response.status === 401) { showLogin(); return false; }
  if (!response.ok) throw new Error("Не удалось определить пользователя");
  state.currentUser = await response.json();
  hideLogin();
  document.getElementById("currentUserName").textContent = state.currentUser.display_name || state.currentUser.username;
  document.getElementById("currentUserRole").textContent = state.currentUser.role;
  document.getElementById("usersNavBtn").style.display = hasPermission("users.view") ? "flex" : "none";
  const objectTools = document.querySelectorAll("#objectsLabel .btn-icon");
  if (objectTools[0]) objectTools[0].style.display = hasPermission("data.export") ? "inline-flex" : "none";
  if (objectTools[1]) objectTools[1].style.display = hasPermission("data.import") ? "inline-flex" : "none";
  if (objectTools[2]) objectTools[2].style.display = hasPermission("objects.manage") ? "inline-flex" : "none";
  return true;
}

window.changeMyPassword = async function(event) {
  event.preventDefault();
  const current = document.getElementById("currentPassword").value;
  const next = document.getElementById("newPassword").value;
  const repeat = document.getElementById("newPasswordRepeat").value;
  if (next !== repeat) { showNotification("Новые пароли не совпадают", "error"); return; }
  try {
    await fetchJsonWithMethod("/api/auth/password", "POST", { current_password: current, new_password: next });
    document.getElementById("changePasswordForm").reset();
    showNotification("Пароль изменён", "success");
  } catch (err) { showNotification(`Ошибка: ${err.message}`, "error"); }
};

window.logout = async function() {
  await fetch(`${window.TENGSL_CONFIG.apiBase}/api/auth/logout`, { method: "POST", credentials: "include" });
  state.currentUser = null;
  if (ws) ws.close();
  showLogin();
};

/* -
Утилиты
- */
function formatTime(isoString) {
  try {
    const d = new Date(isoString);
    return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "--:--:--";
  }
}

async function fetchJson(path) {
  const response = await fetch(`${window.TENGSL_CONFIG.apiBase}${path}`, { credentials: "include" });
  if (response.status === 401) { showLogin(); }
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function fetchJsonWithMethod(path, method, body) {
  const response = await fetch(`${window.TENGSL_CONFIG.apiBase}${path}`, {
    credentials: "include",
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.status === 204) return null;
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status}: ${text || response.statusText}`);
  }
  return response.json();
}


function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

let notificationStack = null;

function getNotificationStack() {
  if (!notificationStack) {
    notificationStack = document.createElement("div");
    notificationStack.className = "notification-stack";
    notificationStack.setAttribute("aria-live", "polite");
    document.body.appendChild(notificationStack);
  }
  return notificationStack;
}

const NOTIFICATION_STORAGE_KEY = "tengsl.notificationHistory.v1";
const MAX_NOTIFICATION_HISTORY = 200;

function loadNotificationHistory() {
  try {
    const saved = JSON.parse(localStorage.getItem(NOTIFICATION_STORAGE_KEY) || "[]");
    state.notifications = Array.isArray(saved) ? saved.slice(0, MAX_NOTIFICATION_HISTORY) : [];
  } catch {
    state.notifications = [];
  }
  renderNotificationCenter();
}

function persistNotificationHistory() {
  try {
    localStorage.setItem(NOTIFICATION_STORAGE_KEY, JSON.stringify(state.notifications.slice(0, MAX_NOTIFICATION_HISTORY)));
  } catch {
    // localStorage may be unavailable in private/restricted browser contexts.
  }
}

function updateNotificationBadge() {
  const badge = document.getElementById("notificationCount");
  if (!badge) return;
  const unread = state.notifications.filter(item => !item.read).length;
  badge.textContent = unread > 99 ? "99+" : String(unread);
  badge.hidden = unread === 0;
}

function renderNotificationCenter() {
  const list = document.getElementById("notificationCenterList");
  const empty = document.getElementById("notificationCenterEmpty");
  const summary = document.getElementById("notificationSummary");
  if (!list || !empty) { updateNotificationBadge(); return; }

  empty.style.display = state.notifications.length ? "none" : "block";
  list.innerHTML = state.notifications.map(item => `
    <article class="notification-history-row" data-type="${escapeHtml(item.type)}" data-unread="${item.read ? "false" : "true"}">
      <div class="notification__icon">${item.type === "error" || item.type === "warning" ? "!" : item.type === "success" ? "✓" : "i"}</div>
      <div class="notification-history__body">
        <div class="notification-history__head"><span class="notification-history__title">${escapeHtml(item.title)}</span><span class="notification-history__time">${escapeHtml(formatDateTime(item.timestamp))}</span></div>
        <div class="notification-history__message">${escapeHtml(item.message)}</div>
        <div class="notification-history__status">${item.read ? "прочитано" : "новое"}</div>
      </div>
    </article>`).join("");
  const unread = state.notifications.filter(item => !item.read).length;
  if (summary) summary.textContent = state.notifications.length ? `${state.notifications.length} уведомлений · ${unread} новых` : "Нет уведомлений";
  updateNotificationBadge();
}

function formatDateTime(isoString) {
  try { return new Date(isoString).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
  catch { return "--"; }
}

window.markAllNotificationsRead = function() {
  state.notifications.forEach(item => { item.read = true; });
  persistNotificationHistory();
  renderNotificationCenter();
};

window.clearNotifications = function() {
  if (!state.notifications.length) return;
  if (!confirm("Очистить историю уведомлений?")) return;
  state.notifications = [];
  persistNotificationHistory();
  renderNotificationCenter();
};

function showNotification(message, type = "info", options = {}) {
  const stack = getNotificationStack();
  const title = options.title || (type === "error" ? "Ошибка" : type === "success" ? "Готово" : type === "warning" ? "Внимание" : "Уведомление");
  const historyItem = { id: `${Date.now()}-${Math.random().toString(16).slice(2)}`, message: String(message), type, title: String(title), timestamp: new Date().toISOString(), read: false };
  state.notifications.unshift(historyItem);
  state.notifications = state.notifications.slice(0, MAX_NOTIFICATION_HISTORY);
  persistNotificationHistory();
  renderNotificationCenter();

  const notification = document.createElement("div");
  notification.className = `notification notification--${type}`;
  notification.innerHTML = `<div class="notification__icon">${type === "error" || type === "warning" ? "!" : type === "success" ? "✓" : "i"}</div><div class="notification__body"><div class="notification__title">${escapeHtml(title)}</div><div class="notification__message">${escapeHtml(message)}</div></div><button class="notification__close" type="button" aria-label="Закрыть">×</button>`;
  stack.appendChild(notification);
  const close = () => {
    notification.classList.remove("notification--visible");
    setTimeout(() => notification.remove(), 220);
  };
  notification.querySelector(".notification__close").addEventListener("click", close);
  requestAnimationFrame(() => notification.classList.add("notification--visible"));
  setTimeout(close, options.duration || (type === "error" ? 7000 : type === "warning" ? 5500 : 3500));
}
function classifyObjectStatus(status) {
  if (!status) return "unknown";
  if (status.status === "online") {
    const updated = status.updated_at ? new Date(status.updated_at).getTime() : 0;
    const ageSeconds = updated ? (Date.now() - updated) / 1000 : Infinity;
    return ageSeconds > 90 ? "stale" : "online";
  }
  if (status.status === "offline") return "offline";
  return "warning";
}

function objectStatusLabel(kind) {
  return ({ online: "Онлайн", offline: "Оффлайн", warning: "Проблемы", stale: "Нет обновления", unknown: "Нет данных" })[kind] || "Нет данных";
}

function formatAge(isoString) {
  if (!isoString) return "нет данных";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(isoString).getTime()) / 1000));
  if (seconds < 60) return `${seconds} сек назад`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} мин назад`;
  return `${Math.floor(minutes / 60)} ч назад`;
}

function renderMonitoring() {
  const counts = { online: 0, warning: 0, offline: 0, unknown: 0 };
  const overview = document.getElementById("objectsOverview");
  let activeAlarms = 0;

  const cards = state.objects.map(obj => {
    const status = state.agentStatus[obj.object_id];
    const kind = classifyObjectStatus(status);
    counts[kind === "stale" ? "warning" : kind]++;
    return { obj, status, kind };
  });

  if (state.selectedObjectId) {
    activeAlarms = state.zones.filter(z => z.last_event_type === "alarm").length;
  }

  document.getElementById("monitorOnline")?.replaceChildren(document.createTextNode(String(counts.online)));
  document.getElementById("monitorWarning")?.replaceChildren(document.createTextNode(String(counts.warning)));
  document.getElementById("monitorOffline")?.replaceChildren(document.createTextNode(String(counts.offline + counts.unknown)));
  document.getElementById("monitorAlarm")?.replaceChildren(document.createTextNode(String(activeAlarms)));
  const updated = document.getElementById("monitorUpdated");
  if (updated) updated.textContent = new Date().toLocaleTimeString("ru-RU", {hour:"2-digit", minute:"2-digit"});

  if (overview) {
    overview.innerHTML = cards.map(({obj,status,kind}) => `
      <button class="object-monitor-card object-monitor-card--${kind}" type="button" data-object-id="${escapeHtml(obj.object_id)}">
        <span class="object-monitor-card__dot"></span>
        <span class="object-monitor-card__body"><span class="object-monitor-card__name">${escapeHtml(obj.name || obj.object_id)}</span><span class="object-monitor-card__meta">${objectStatusLabel(kind)} · ${status ? formatAge(status.updated_at) : "ожидание агента"}</span></span>
      </button>`).join("") || '<div class="empty-state">Объектов пока нет.</div>';
    overview.querySelectorAll(".object-monitor-card").forEach(button => {
      button.addEventListener("click", () => selectObject(button.dataset.objectId));
    });
  }

  const selected = document.getElementById("selectedObjectStatus");
  const meta = document.getElementById("selectedObjectMeta");
  if (selected && state.selectedObjectId) {
    const status = state.agentStatus[state.selectedObjectId];
    const kind = classifyObjectStatus(status);
    selected.dataset.status = kind;
    selected.textContent = objectStatusLabel(kind);
    if (meta) meta.textContent = status ? `${status.detail || "Связь с edge-агентом"} · ${formatAge(status.updated_at)}` : "Агент ещё не прислал статус";
  } else if (selected) {
    selected.textContent = "—";
    selected.removeAttribute("data-status");
    if (meta) meta.textContent = "Выбери объект слева";
  }
}

function renderAlerts() {
  const list = document.getElementById("alertList");
  const empty = document.getElementById("alertEmptyState");
  const summary = document.getElementById("alertSummary");
  if (!list || !empty) return;
  const alerts = [];
  state.objects.forEach(obj => {
    const status = state.agentStatus[obj.object_id];
    const kind = classifyObjectStatus(status);
    if (["offline","warning","stale"].includes(kind)) alerts.push({severity: kind === "offline" ? "alarm" : "attention", title: obj.name || obj.object_id, detail: status?.detail || objectStatusLabel(kind)});
  });
  if (state.selectedObjectId) state.zones.filter(z => ["alarm","fault"].includes(z.last_event_type)).slice(0,10).forEach(zone => alerts.push({severity:zone.last_event_type,title:zone.zone_name || `Зона ${zone.zone_number}`,detail:zone.last_label || statusLabel(zone.last_event_type)}));
  state.alerts = alerts;
  empty.style.display = alerts.length ? "none" : "block";
  list.innerHTML = alerts.map(item => `<div class="alert-row" data-status="${item.severity}"><span class="alert-row__dot"></span><div class="alert-row__body"><b>${escapeHtml(item.title)}</b><span>${escapeHtml(item.detail)}</span></div></div>`).join("");
  if (summary) summary.textContent = alerts.length ? `${alerts.length} активных проблем` : "нет проблем";
}

function notifyCriticalEvent(event) {
  if (!["alarm","fault"].includes(event.event_type)) return;
  const key = `${event.object_id}:${event.zone_number}:${event.primary_code}:${event.timestamp}`;
  if (state.lastNotificationKey === key) return;
  state.lastNotificationKey = key;
  showNotification(`${event.zone_name || `Зона ${event.zone_number}`}: ${event.label || statusLabel(event.event_type)}`, event.event_type === "alarm" ? "error" : "warning", {title: event.event_type === "alarm" ? "Тревога" : "Неисправность"});
}

/* -
Список объектов
- */
async function loadObjects() {
  const objects = await fetchJson("/api/objects");
  state.objects = objects;
  
  for (const obj of objects) {
    try {
      const status = await fetchJson(`/api/objects/${obj.object_id}/status`);
      state.agentStatus[obj.object_id] = status;
    } catch {
      // статус может быть ещё не получен
    }
  }
  
  renderObjectList();
  renderMonitoring();
  renderAlerts();
  
  if (state.selectedObjectId && !objects.find(o => o.object_id === state.selectedObjectId)) {
    state.selectedObjectId = null;
    state.zones = [];
    state.events = [];
    renderZones();
    renderEventLog();
    renderBanner();
  }
}

function renderObjectList() {
  el.objectList.innerHTML = "";
  if (state.objects.length === 0) {
    el.objectList.innerHTML = '<li class="empty-state">Нет объектов. <button class="btn-link" onclick="showAddObjectForm()">Добавить первый</button></li>';
    return;
  }
  for (const obj of state.objects) {
    const status = state.agentStatus[obj.object_id];
    const statusValue = classifyObjectStatus(status);
    const li = document.createElement("li");
    li.className = "object-item";
    li.setAttribute("role", "button");
    li.setAttribute("tabindex", "0");
    li.setAttribute("aria-current", String(obj.object_id === state.selectedObjectId));
    li.addEventListener("click", () => selectObject(obj.object_id));
    li.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") selectObject(obj.object_id);
    });
    li.innerHTML = `
      <span class="object-item__dot object-item__dot--${statusValue}" title="${objectStatusLabel(statusValue)}"></span>
      <span class="object-item__body">
        <div class="object-item__name">${obj.name || obj.object_id}</div>
        <div class="object-item__id">${obj.object_id} · ${status ? formatAge(status.updated_at) : "нет статуса"}</div>
      </span>
      <span class="object-item__actions">
        ${hasPermission("objects.manage") ? `<button class="btn-icon-sm" onclick="event.stopPropagation(); showEditObjectForm('${escapeHtml(obj.object_id)}')" title="Настройки объекта" aria-label="Настройки объекта"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.2 2.8h3.6l.5 1.7 1.5.9 1.7.4-1.1 3.4-1.5-.9v1.8l1.5.9-1.1 3.4-1.7-.4-1.5.9H6.2l-.5-1.7-1.5-.9-1.7.4-1.1-3.4 1.5-.9V9.3l-1.5-.9 1.1-3.4 1.7.4 1.5-.9z"/><circle cx="8" cy="8" r="2"/></svg></button>` : ""}
      </span>
    `;
    el.objectList.appendChild(li);
  }
}

async function selectObject(objectId) {
  state.selectedObjectId = objectId;
  renderObjectList();
  el.manageZonesBtn.style.display = hasPermission("objects.manage") ? "inline-block" : "none";
  el.bannerStatus.textContent = "Загрузка…";
  el.bannerDetail.textContent = "";

  try {
    const [zones, events] = await Promise.all([
      fetchJson(`/api/objects/${objectId}/zones`),
      fetchJson(`/api/objects/${objectId}/events?limit=50`),
    ]);
    state.zones = zones;
    state.events = events;
    renderZones();
    renderEventLog();
    renderBanner();
    renderMonitoring();
    renderAlerts();
  } catch (err) {
    el.bannerStatus.textContent = "Не удалось загрузить данные";
    el.bannerDetail.textContent = String(err);
  }
  reconnectWebSocket();
}

/* -
Сетка зон
- */
function statusLabel(eventType) {
  switch (eventType) {
    case "normal": return "Норма";
    case "alarm": return "Тревога";
    case "attention": return "Внимание";
    case "fault": return "Неисправность";
    case "disabled": return "Отключен";
    case "info": return "Информация";
    default: return "Нет данных";
  }
}

function renderZones() {
  el.zonesMeta.textContent = `${state.zones.length} зон`;
  el.zoneEmptyState.style.display = state.zones.length === 0 ? "block" : "none";
  el.zoneGrid.innerHTML = "";
  if (state.zones.length === 0) {
    el.zoneGrid.appendChild(el.zoneEmptyState);
    return;
  }
  for (const zone of state.zones) {
    el.zoneGrid.appendChild(buildZoneCard(zone));
  }
}

function buildZoneCard(zone) {
  const card = document.createElement("div");
  card.className = "zone-card";
  card.dataset.zoneNumber = String(zone.zone_number);
  card.dataset.status = zone.last_event_type || "unknown";

  const codeBadge = zone.last_primary_code != null
    ? `<span class="zone-card__code">#${zone.last_primary_code}</span>`
    : "";

  card.innerHTML = `
    <div class="zone-card__top">
      <span class="zone-card__number">Зона ${zone.zone_number}</span>
      <span class="zone-card__label">${codeBadge}${zone.last_label || statusLabel(zone.last_event_type)}</span>
    </div>
    <div class="zone-card__name">${zone.zone_name || "Без названия"}</div>
    <div class="zone-card__device">${zone.device_type || "—"}</div>
    <div class="zone-card__actions" ${hasPermission("commands.execute") ? "" : "hidden"}>
      <button class="btn-zone-control btn-arm" onclick="handleZoneCommand(${zone.zone_number}, 'arm')" title="Взять под охрану">🔒</button>
      <button class="btn-zone-control btn-disarm" onclick="handleZoneCommand(${zone.zone_number}, 'disarm')" title="Снять с охраны">🔓</button>
      <button class="btn-zone-control btn-enable" onclick="handleZoneCommand(${zone.zone_number}, 'enable')" title="Включить контроль">✅</button>
      <button class="btn-zone-control btn-disable" onclick="handleZoneCommand(${zone.zone_number}, 'disable')" title="Отключить контроль">⛔</button>
    </div>
  `;
  return card;
}

function updateZoneCard(event) {
  let zone = state.zones.find((z) => z.zone_number === event.zone_number);
  if (!zone) {
    zone = {
      zone_number: event.zone_number,
      zone_name: event.zone_name,
      device_type: event.device_type,
      last_event_type: event.event_type,
      last_label: event.label,
      last_primary_code: event.primary_code,
    };
    state.zones.push(zone);
  } else {
    zone.last_event_type = event.event_type;
    zone.last_label = event.label;
    zone.last_primary_code = event.primary_code;
  }
  renderZones();
}

/* -
Журнал событий
- */
function renderEventLog() {
  el.eventEmptyState.style.display = state.events.length === 0 ? "block" : "none";
  el.eventLog.innerHTML = "";
  if (state.events.length === 0) {
    el.eventLog.appendChild(el.eventEmptyState);
    return;
  }
  for (const event of state.events) {
    el.eventLog.appendChild(buildEventRow(event));
  }
}

function buildEventRow(event) {
  const row = document.createElement("div");
  row.className = "event-row";
  row.dataset.status = event.event_type || "unknown";

  const code = event.primary_code != null ? `<span class="event-row__code">#${event.primary_code}</span>` : "";
  const zoneName = event.zone_name || `Зона ${event.zone_number}`;

  row.innerHTML = `
    <span class="event-row__time">${formatTime(event.timestamp)}</span>
    <span class="event-row__desc"><b>${zoneName}</b> — ${code}${event.label || statusLabel(event.event_type)}</span>
    <span class="event-row__label">${statusLabel(event.event_type)}</span>
  `;
  return row;
}

function prependEvent(event) {
  state.events.unshift(event);
  if (state.events.length > 200) state.events.length = 200;
  renderEventLog();
}

/* -
Баннер общего статуса
- */
function renderBanner() {
  const activeAlarms = state.zones.filter((z) => z.last_event_type === "alarm");
  const agentStatus = state.agentStatus[state.selectedObjectId];

  if (agentStatus && agentStatus.status !== "online") {
    el.banner.dataset.state = "calm";
    el.bannerStatus.textContent = "Агент недоступен";
    el.bannerDetail.textContent = agentStatus.detail || "Нет связи с edge-агентом на объекте";
    return;
  }

  if (activeAlarms.length > 0) {
    el.banner.dataset.state = "alarm";
    el.bannerStatus.textContent =
      activeAlarms.length === 1 ? "1 активная тревога" : `${activeAlarms.length} активных тревог`;
    el.bannerDetail.textContent = activeAlarms.map((z) => z.zone_name || `Зона ${z.zone_number}`).join(", ");
    return;
  }

  el.banner.dataset.state = "calm";
  el.bannerStatus.textContent = "Всё в норме";
  el.bannerDetail.textContent = state.zones.length > 0 ? `${state.zones.length} зон под наблюдением` : "Нет данных по зонам";
}

/* -
WebSocket
- */
function setConnState(connState) {
  el.connIndicator.dataset.state = connState;
}

function reconnectWebSocket() {
  if (ws) {
    ws.onclose = null;
    ws.close();
  }
  if (!state.selectedObjectId) return;

  setConnState("connecting");
  const url = `${window.TENGSL_CONFIG.wsBase}/ws/events?object_id=${encodeURIComponent(state.selectedObjectId)}`;
  ws = new WebSocket(url);

  ws.onopen = () => {
    wsReconnectDelay = 1000;
    setConnState("connected");
  };

  ws.onmessage = (msg) => {
    let payload;
    try {
      payload = JSON.parse(msg.data);
    } catch {
      return;
    }

    if (payload.type === "event" && payload.data.object_id === state.selectedObjectId) {
      updateZoneCard(payload.data);
      prependEvent(payload.data);
      notifyCriticalEvent(payload.data);
      renderBanner();
      renderMonitoring();
      renderAlerts();
    } else if (payload.type === "agent_status" && payload.data.object_id === state.selectedObjectId) {
      const previous = state.agentStatus[payload.data.object_id];
      state.agentStatus[payload.data.object_id] = payload.data;
      renderObjectList();
      renderBanner();
      renderMonitoring();
      renderAlerts();
      if (payload.data.status === "offline" && previous?.status !== "offline") {
        showNotification(`${payload.data.object_id}: ${payload.data.detail || "агент недоступен"}`, "error", {title:"Потеря связи"});
      } else if (payload.data.status === "online" && previous?.status === "offline") {
        showNotification(`${payload.data.object_id}: связь восстановлена`, "success", {title:"Связь восстановлена"});
      }
    } else if (payload.type === "command_status" && payload.data.object_id === state.selectedObjectId) {
      const item = state.commands.find(entry => entry.command_id === payload.data.command_id);
      if (item) {
        item.status = payload.data.status;
        item.detail = payload.data.detail || "";
        item.completed_at = payload.data.timestamp;
      } else {
        state.commands.unshift(payload.data);
      }
      if (state.commands.length > 100) state.commands.length = 100;
      renderCommands();
      showNotification(`Команда ${payload.data.status === "executed" ? "выполнена" : "завершилась ошибкой"}: ${payload.data.detail || ""}`, payload.data.status === "executed" ? "success" : "error");
    }
  };

  ws.onclose = () => {
    setConnState("disconnected");
    setTimeout(reconnectWebSocket, wsReconnectDelay);
    wsReconnectDelay = Math.min(wsReconnectDelay * 1.5, WS_MAX_RECONNECT_DELAY);
  };

  ws.onerror = () => {
    ws.close();
  };
}

/* -
Периодическое обновление списка объектов
- */
function startObjectListPolling() {
  setInterval(() => {
    loadObjects().catch((err) => console.error("Failed to refresh object list:", err));
  }, 15000);

  setInterval(() => { renderMonitoring(); renderAlerts(); }, 5000);
}

/* -
Управление зонами (CRUD)
- */
el.manageZonesBtn.addEventListener("click", () => showZoneManager());

function showZoneManager() {
  const existing = document.querySelector(".modal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.className = "modal";
  modal.innerHTML = `
    <div class="modal__backdrop"></div>
    <div class="modal__content">
      <div class="modal__header">
        <h3>Управление зонами</h3>
        <button class="modal__close" onclick="closeZoneManager()">&times;</button>
      </div>
      <div class="modal__body">
        <button class="btn btn-primary btn-sm" onclick="showAddZoneForm()">+ Добавить зону</button>
        <div id="zoneManagerList" class="zone-manager-list"></div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  loadZoneManagerList();
}

window.closeZoneManager = function() {
  const modal = document.querySelector(".modal");
  if (modal) modal.remove();
  if (state.selectedObjectId) {
    selectObject(state.selectedObjectId);
  }
};

async function loadZoneManagerList() {
  const list = document.getElementById("zoneManagerList");
  if (!list || !state.selectedObjectId) return;

  try {
    const zones = await fetchJson(`/api/objects/${state.selectedObjectId}/zones`);
    if (zones.length === 0) {
      list.innerHTML = '<div class="empty-state" style="margin-top: 16px;">Зон пока нет. Добавьте первую.</div>';
      return;
    }
    list.innerHTML = zones.map(zone => `
      <div class="zone-manager-item">
        <div class="zone-manager-item__body">
          <div class="zone-manager-item__number">Зона ${zone.zone_number}</div>
          <div class="zone-manager-item__name">${zone.zone_name || "Без названия"}</div>
          <div class="zone-manager-item__meta">${zone.device_type || "—"} · адрес ${zone.register_address ?? "—"}</div>
        </div>
        <div class="zone-manager-item__actions">
          <button class="btn btn-secondary btn-sm" onclick="showEditZoneForm(${zone.id})">Изменить</button>
          <button class="btn btn-danger btn-sm" onclick="handleDeleteZone(${zone.id}, ${zone.zone_number})">Удалить</button>
        </div>
      </div>
    `).join("");
  } catch (err) {
    list.innerHTML = `<div class="error">Ошибка загрузки: ${err.message}</div>`;
  }
}

window.showAddZoneForm = function() {
  const body = document.querySelector(".modal__body");
  body.innerHTML = `
    <h4>Новая зона</h4>
    <form onsubmit="handleCreateZone(event)">
      <div class="form-group">
        <label>Номер зоны *</label>
        <input type="number" name="zone_number" required min="1" class="form-control" placeholder="Например: 1">
      </div>
      <div class="form-group">
        <label>Название</label>
        <input type="text" name="zone_name" class="form-control" placeholder="Например: Извещатель у входа">
      </div>
      <div class="form-group">
        <label>Тип устройства</label>
        <input type="text" name="device_type" class="form-control" placeholder="Например: С2000-ДИП34А">
      </div>
      <div class="form-group">
        <label>Адрес регистра</label>
        <input type="number" name="register_address" class="form-control" placeholder="Например: 40000">
      </div>
      <div class="form-actions">
        <button type="button" class="btn btn-secondary" onclick="showZoneManager()">Отмена</button>
        <button type="submit" class="btn btn-primary">Создать</button>
      </div>
    </form>
  `;
};

window.showEditZoneForm = function(zoneId) {
  const zone = state.zones.find(z => z.id === zoneId);
  if (!zone) {
    fetchJson(`/api/objects/${state.selectedObjectId}/zones`).then(zones => {
      const found = zones.find(z => z.id === zoneId);
      if (found) renderEditZoneForm(found);
    });
    return;
  }
  renderEditZoneForm(zone);
};

function renderEditZoneForm(zone) {
  const body = document.querySelector(".modal__body");
  body.innerHTML = `
    <h4>Редактировать зону ${zone.zone_number}</h4>
    <form onsubmit="handleUpdateZone(event, ${zone.id})">
      <div class="form-group">
        <label>Номер зоны</label>
        <input type="number" value="${zone.zone_number}" disabled class="form-control">
      </div>
      <div class="form-group">
        <label>Название</label>
        <input type="text" name="zone_name" value="${zone.zone_name || ""}" class="form-control">
      </div>
      <div class="form-group">
        <label>Тип устройства</label>
        <input type="text" name="device_type" value="${zone.device_type || ""}" class="form-control">
      </div>
      <div class="form-group">
        <label>Адрес регистра</label>
        <input type="number" name="register_address" value="${zone.register_address ?? ""}" class="form-control">
      </div>
      <div class="form-actions">
        <button type="button" class="btn btn-secondary" onclick="showZoneManager()">Отмена</button>
        <button type="submit" class="btn btn-primary">Сохранить</button>
      </div>
    </form>
  `;
}

window.handleCreateZone = async function(event) {
  event.preventDefault();
  const form = event.target;
  const data = {
    zone_number: parseInt(form.zone_number.value, 10),
    zone_name: form.zone_name.value.trim(),
    device_type: form.device_type.value.trim(),
    register_address: form.register_address.value ? parseInt(form.register_address.value, 10) : null,
  };

  try {
    await fetchJsonWithMethod(`/api/objects/${state.selectedObjectId}/zones`, "POST", data);
    showZoneManager();
  } catch (err) {
    showNotification(`Ошибка создания зоны: ${err.message}`, "error");
  }
};

window.handleUpdateZone = async function(event, zoneId) {
  event.preventDefault();
  const form = event.target;
  const data = {
    zone_name: form.zone_name.value.trim(),
    device_type: form.device_type.value.trim(),
    register_address: form.register_address.value ? parseInt(form.register_address.value, 10) : null,
  };

  try {
    await fetchJsonWithMethod(`/api/objects/${state.selectedObjectId}/zones/${zoneId}`, "PUT", data);
    showZoneManager();
  } catch (err) {
    showNotification(`Ошибка обновления: ${err.message}`, "error");
  }
};

window.handleDeleteZone = async function(zoneId, zoneNumber) {
  if (!confirm(`Удалить зону ${zoneNumber}?`)) return;

  try {
    await fetchJsonWithMethod(`/api/objects/${state.selectedObjectId}/zones/${zoneId}`, "DELETE");
    loadZoneManagerList();
  } catch (err) {
    showNotification(`Ошибка удаления: ${err.message}`, "error");
  }
};

/* -
Управление зонами (команды arm/disarm/enable/disable)
- */
async function sendZoneCommand(objectId, zoneNumber, command) {
  const response = await fetch(
    `${window.TENGSL_CONFIG.apiBase}/api/objects/${objectId}/zones/${zoneNumber}/command`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command }),
    }
  );
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status}: ${text || response.statusText}`);
  }
  return response.json();
}

async function loadCommands() {
  const body = document.getElementById("commandsTableBody");
  if (!body) return;
  body.innerHTML = '<tr><td colspan="7" class="empty-state">Загрузка…</td></tr>';
  try {
    const params = state.selectedObjectId ? `?object_id=${encodeURIComponent(state.selectedObjectId)}&limit=100` : "?limit=100";
    state.commands = await fetchJson(`/api/commands${params}`);
    renderCommands();
  } catch (err) {
    body.innerHTML = `<tr><td colspan="7" class="empty-state">Ошибка загрузки: ${escapeHtml(err.message)}</td></tr>`;
  }
}

const COMMAND_LABELS = {
  arm: "Взять", disarm: "Снять", enable: "Включить контроль", disable: "Выключить контроль"
};
const COMMAND_STATUS_LABELS = { sent: "Отправлена", executed: "Выполнена", failed: "Ошибка", pending: "Ожидание" };

function renderCommands() {
  const body = document.getElementById("commandsTableBody");
  if (!body) return;
  if (!state.commands.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty-state">Команд пока нет</td></tr>';
    return;
  }
  body.innerHTML = state.commands.map(item => `
    <tr>
      <td>${formatTime(item.requested_at)}</td>
      <td>${escapeHtml(item.object_id)}</td>
      <td>${item.zone_number}</td>
      <td>${escapeHtml(COMMAND_LABELS[item.command] || item.command)}</td>
      <td>${escapeHtml(COMMAND_STATUS_LABELS[item.status] || item.status)}</td>
      <td>${escapeHtml(item.detail || "—")}</td>
      <td><span class="event-row__code">${escapeHtml(item.command_id.slice(0, 10))}</span></td>
    </tr>
  `).join("");
}

async function loadAdministration() {
  const grid = document.getElementById("adminServiceGrid");
  if (!grid) return;
  try {
    const data = await fetchJson("/api/admin/status");
    const mode = data.environment === "development" ? "development" : "production";
    document.getElementById("adminEnvironment").textContent = `Режим: ${mode}`;
    const current = document.getElementById("adminModeCurrent");
    if (current) current.textContent = `Текущий режим: ${mode === "development" ? "Development" : "Production"}`;
    const devBtn = document.getElementById("adminModeDev");
    const prodBtn = document.getElementById("adminModeProd");
    if (devBtn) devBtn.disabled = mode === "development";
    if (prodBtn) prodBtn.disabled = mode === "production";
    const items = [
      ["Backend", data.backend], ["PostgreSQL", data.database], ["MQTT", data.mqtt], ["WebSocket", data.websocket],
      ["Hot reload", {status: data.development.hot_reload ? "ok" : "info", detail: data.development.hot_reload ? "Включён" : "В production отключён"}],
      ["Хранилище БД", {status: "ok", detail: "Persistent volume"}],
    ];
    grid.innerHTML = items.map(([name, item]) => `<div class="stat-card"><div class="stat-card__value admin-status admin-status--${escapeHtml(item.status)}">${item.status === "ok" ? "OK" : item.status === "error" ? "ERROR" : "INFO"}</div><div class="stat-card__label">${escapeHtml(name)}</div><div class="panel__meta">${escapeHtml(item.detail || "")}</div></div>`).join("");
  } catch (err) {
    grid.innerHTML = `<div class="empty-state">Ошибка: ${escapeHtml(err.message)}</div>`;
  }
}

window.adminSetMode = async function(mode) {
  const label = mode === "development" ? "Development" : "Production";
  if (!confirm(`Переключить TENGSL в режим ${label}? Backend и Edge Agent будут перезапущены без пересборки.`)) return;
  try {
    await fetchJsonWithMethod("/api/admin/mode", "POST", {mode});
    showNotification(`Переключение в ${label} запущено. Сервисы перезапускаются…`, "success");
    setTimeout(loadAdministration, 3500);
  } catch (err) {
    showNotification(`Ошибка переключения режима: ${err.message}`, "error");
  }
};

window.adminRestartBackend = async function() {
  if (!confirm("Перезагрузить Backend? Контейнер будет перезапущен без пересборки.")) return;
  try {
    await fetchJsonWithMethod("/api/admin/backend/restart", "POST");
    showNotification("Backend перезагружается без rebuild", "success");
    setTimeout(loadAdministration, 2500);
  } catch (err) { showNotification(`Ошибка перезагрузки Backend: ${err.message}`, "error"); }
};

window.adminRestartAgent = async function() {
  if (!confirm("Перезагрузить Edge Agent? Текущие опросчики будут остановлены и агент запустится снова.")) return;
  try {
    await fetchJsonWithMethod("/api/admin/agent/restart", "POST");
    showNotification("Команда перезагрузки Edge Agent отправлена", "success");
    setTimeout(loadAdministration, 2500);
  } catch (err) { showNotification(`Ошибка перезагрузки агента: ${err.message}`, "error"); }
};

window.runDiagnostics = async function() {
  const btn = document.querySelector('#tab-diagnostics button[onclick="runDiagnostics()"]');
  if (btn) { btn.disabled = true; btn.textContent = "⏳ Диагностика…"; }
  try {
    const data = await fetchJsonWithMethod("/api/admin/diagnostics/run", "POST");
    if (data.status === "running") {
      showNotification("Диагностика запущена. Результат появится после завершения.", "success");
      await loadTestResults();
      await pollDiagnosticsResult(data.job_id);
    } else {
      await loadTestResults();
    }
  } catch (err) {
    showNotification(`Ошибка диагностики: ${err.message}`, "error");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ Запустить диагностику"; }
  }
};

async function pollDiagnosticsResult(jobId) {
  for (let attempt = 0; attempt < 130; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    try {
      const data = await fetchJson("/api/test-results");
      await renderTestResults(data);
      if (data.overall_status !== "running") {
        showNotification(data.failed ? `Диагностика завершена с ошибками: ${data.failed}` : "Диагностика завершена успешно", data.failed ? "error" : "success");
        await loadTestJournal();
        return;
      }
    } catch (err) {
      // A transient reload/restart should not make the result disappear.
      if (attempt > 10) console.warn("Diagnostics polling:", err);
    }
  }
  showNotification("Диагностика выполняется дольше 130 секунд. Результат сохранится автоматически.", "info");
}

async function loadTestResults() {
  try {
    const data = await fetchJson("/api/test-results");
    await renderTestResults(data);
  } catch (err) {
    const overall = document.getElementById("testOverall");
    if (overall) overall.textContent = "Ошибка";
    const failures = document.getElementById("testFailures");
    if (failures) failures.textContent = `Не удалось загрузить результаты: ${err.message}`;
    console.warn("Loading test results:", err);
  }
}

async function renderTestResults(data) {
  const suites = document.getElementById("testSuitesBody");
  const failedCount = Number(data.failed || 0);
  const totalCount = Number(data.total || 0);
  const overallLabel = data.overall_status === "running" ? "Выполняется…" : totalCount === 0 ? "Не запускались" : failedCount > 0 ? `Есть ошибки (${failedCount})` : "OK";
  document.getElementById("testOverall").textContent = overallLabel;
  document.getElementById("testPassed").textContent = data.passed ?? 0;
  document.getElementById("testFailed").textContent = data.failed ?? 0;
  document.getElementById("testSkipped").textContent = data.skipped ?? 0;
  document.getElementById("testGeneratedAt").textContent = `Запуск: ${new Date(data.generated_at).toLocaleString("ru-RU")}`;
  suites.innerHTML = (data.suites || []).map(item => `
    <tr><td>${escapeHtml(item.suite)}</td><td>${item.status === "passed" ? "OK" : item.status === "running" ? "RUNNING" : "FAIL"}</td><td>${item.passed}</td><td>${item.failed}</td><td>${item.skipped}</td><td>${Number(item.duration_seconds || 0).toFixed(2)} c</td></tr>
  `).join("") || '<tr><td colspan="6" class="empty-state">Нет результатов</td></tr>';
  const failures = document.getElementById("testFailures");
  failures.innerHTML = data.failures?.length ? `<b>Ошибки:</b><ul>${data.failures.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : data.overall_status === "running" ? "Диагностика выполняется в фоне…" : "";
}

window.exportObject = async function(objectId) {
  try {
    const data = await fetchJson(`/api/objects/${encodeURIComponent(objectId)}/export`);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `tengsl-object-${objectId}-${new Date().toISOString().slice(0,10)}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showNotification(`Объект «${objectId}» экспортирован`, "success");
  } catch (err) {
    showNotification(`Ошибка экспорта объекта: ${err.message}`, "error");
  }
};

window.importObjectFile = function(objectId) {
  const input = document.getElementById("importObjectFile");
  if (!input) return;
  input.dataset.objectId = objectId;
  input.value = "";
  input.click();
};

window.importSingleObjectFromFile = async function(event) {
  const file = event.target.files?.[0];
  const objectId = event.target.dataset.objectId;
  event.target.value = "";
  delete event.target.dataset.objectId;
  if (!file || !objectId) return;

  try {
    const payload = JSON.parse(await file.text());
    const imported = Array.isArray(payload.objects) ? payload.objects : (payload.object ? [payload.object] : []);
    if (imported.length !== 1) throw new Error("Файл должен содержать ровно один объект");
    if (imported[0].object_id !== objectId) {
      const ok = confirm(`Файл содержит объект «${imported[0].object_id}», а импорт выполняется в «${objectId}». Продолжить и заменить конфигурацию объекта «${objectId}» данными из файла?`);
      if (!ok) return;
    }
    const result = await fetchJsonWithMethod(`/api/objects/${encodeURIComponent(objectId)}/import`, "POST", { version: payload.version || 1, object: imported[0] });
    await loadObjects();
    if (state.selectedObjectId === objectId) await selectObject(objectId);
    showNotification(`Объект «${objectId}» импортирован: зон создано ${result.zones_created}, обновлено ${result.zones_updated}`, "success");
  } catch (err) {
    showNotification(`Ошибка импорта объекта: ${err.message}`, "error");
  }
};

window.exportObjects = async function() {
  try {
    const data = await fetchJson("/api/objects/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `tengsl-objects-${new Date().toISOString().slice(0,10)}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showNotification(`Экспортировано объектов: ${(data.objects || []).length}`, "success");
  } catch (err) {
    showNotification(`Ошибка экспорта: ${err.message}`, "error");
  }
};

window.importObjectsFromFile = async function(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  try {
    const payload = JSON.parse(await file.text());
    if (!Array.isArray(payload.objects)) throw new Error("В файле отсутствует массив objects");
    const result = await fetchJsonWithMethod("/api/objects/import", "POST", payload);
    await loadObjects();
    showNotification(`Импортировано: объектов создано ${result.objects_created}, обновлено ${result.objects_updated}; зон создано ${result.zones_created}, обновлено ${result.zones_updated}`, "success");
  } catch (err) {
    showNotification(`Ошибка импорта: ${err.message}`, "error");
  }
};

window.handleZoneCommand = async function(zoneNumber, command) {
  if (!hasPermission("commands.execute")) { showNotification("Недостаточно прав для выполнения команды", "error"); return; }
  if (!state.selectedObjectId) return;
  
  const commandLabels = {
    "arm": "взять под охрану",
    "disarm": "снять с охраны",
    "enable": "включить контроль",
    "disable": "отключить контроль",
  };
  
  if (!confirm(`Вы уверены, что хотите ${commandLabels[command]} зону ${zoneNumber}?`)) {
    return;
  }
  
  try {
    const result = await sendZoneCommand(state.selectedObjectId, zoneNumber, command);
    showNotification(`Команда "${commandLabels[command]}" отправлена для зоны ${zoneNumber}; ожидается подтверждение`, "success");
    if (result?.command_id) {
      state.commands.unshift({
        command_id: result.command_id, object_id: state.selectedObjectId, zone_number: zoneNumber,
        command, status: "sent", detail: "Ожидание подтверждения от агента", requested_at: new Date().toISOString()
      });
      if (state.commands.length > 100) state.commands.length = 100;
      renderCommands();
    }
  } catch (err) {
    showNotification(`Ошибка: ${err.message}`, "error");
  }
};

window.exportLatestTestRun = async function() {
  try {
    const response = await fetch(`${window.TENGSL_CONFIG.apiBase}/api/admin/test-runs/latest/export`, { credentials: "include" });
    if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `tengsl-diagnostics-${new Date().toISOString().slice(0,19).replace(/[T:]/g,"-")}.txt`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  } catch (err) { showNotification(`Ошибка выгрузки: ${err.message}`, "error"); }
};

window.exportTestRun = async function(runId) {
  try {
    const response = await fetch(`${window.TENGSL_CONFIG.apiBase}/api/admin/test-runs/${encodeURIComponent(runId)}/export`, { credentials: "include" });
    if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `tengsl-diagnostics-${runId}.txt`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  } catch (err) { showNotification(`Ошибка выгрузки: ${err.message}`, "error"); }
};

async function loadTestJournal() {
  const body = document.getElementById("testJournalBody");
  if (!body) return;
  try {
    const runs = await fetchJson("/api/admin/test-runs?limit=100");
    body.innerHTML = runs.length ? runs.map(r => {
      const date = new Date(r.completed_at || r.started_at);
      const result = r.overall_status === "passed" ? "OK" : r.overall_status === "running" ? "Выполняется…" : "Ошибка";
      return `<tr><td>${date.toLocaleString("ru-RU")}</td><td>${result}</td><td>${r.total}</td><td>${r.passed}</td><td>${r.failed}</td><td>${r.skipped}</td><td>${Number(r.duration_seconds || 0).toFixed(2)} с</td><td>${escapeHtml(r.requested_by || "—")}</td><td><button class="btn btn-secondary btn-sm" onclick="exportTestRun('${escapeHtml(r.id)}')">TXT</button></td></tr>`;
    }).join("") : '<tr><td colspan="9" class="empty-state">Запусков ещё не было</td></tr>';
  } catch (err) { body.innerHTML = `<tr><td colspan="9" class="empty-state">Ошибка: ${escapeHtml(err.message)}</td></tr>`; }
}

/* -
Переключение вкладок
- */
window.switchTab = function(tabName) {
  if ((tabName === "administration" || tabName === "test-journal") && !hasPermission("system.manage")) { showNotification("Нет доступа к администрированию", "error"); return; }
  document.querySelectorAll(".tab-content").forEach(tab => tab.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach(btn => btn.classList.remove("active"));
  
  const targetTab = document.getElementById(`tab-${tabName}`);
  if (targetTab) targetTab.classList.add("active");
  
  const targetBtn = document.querySelector(`.nav-btn[data-tab="${tabName}"]`);
  if (targetBtn) targetBtn.classList.add("active");
  
  const objectsLabel = document.getElementById("objectsLabel");
  const objectList = document.getElementById("objectList");
  if (tabName === "console") {
    objectsLabel.style.display = "flex";
    objectList.style.display = "flex";
  } else {
    objectsLabel.style.display = "none";
    objectList.style.display = "none";
  }
  
  if (tabName === "stats") loadStats();
  if (tabName === "commands") loadCommands();
  if (tabName === "settings") loadEventCodes();
  if (tabName === "diagnostics") loadTestResults();
  if (tabName === "test-journal") loadTestJournal();
  if (tabName === "administration" && hasPermission("system.manage")) loadAdministration();
  if (tabName === "users" && hasPermission("users.view")) loadUsers();
  if (tabName === "notifications") {
    state.notifications.forEach(item => { item.read = true; });
    persistNotificationHistory();
    renderNotificationCenter();
  }
};

/* -
Управление объектами (CRUD)
- */
window.showAddObjectForm = function() {
  showObjectForm(null);
};

window.showEditObjectForm = function(objectId) {
  const obj = state.objects.find(o => o.object_id === objectId);
  if (obj) showObjectForm(obj);
};

function showObjectForm(obj) {
  const isEdit = obj !== null;
  const existing = document.querySelector(".modal");
  if (existing) existing.remove();
  
  const modal = document.createElement("div");
  modal.className = "modal";
  modal.innerHTML = `
    <div class="modal__backdrop" onclick="closeObjectForm()"></div>
    <div class="modal__content">
      <div class="modal__header">
        <h3>${isEdit ? "Редактировать объект" : "Новый объект"}</h3>
        <button class="modal__close" onclick="closeObjectForm()">&times;</button>
      </div>
      <div class="modal__body">
        <form onsubmit="handleObjectFormSubmit(event, ${isEdit ? `'${obj.object_id}'` : "null"})">
          <div class="form-group">
            <label>Идентификатор объекта *</label>
            <input type="text" name="object_id" required ${isEdit ? "disabled" : ""} 
                   value="${isEdit ? obj.object_id : ""}" 
                   class="form-control" placeholder="Например: DOM-001">
            <small style="color: var(--text-muted); font-size: 11px;">Уникальный ID, нельзя изменить</small>
          </div>
          <div class="form-group">
            <label>Название</label>
            <input type="text" name="name" value="${isEdit ? obj.name : ""}" 
                   class="form-control" placeholder="Например: Склад №1">
          </div>
          <div class="form-group">
            <label>IP-адрес шлюза RS-485→Ethernet *</label>
            <input type="text" name="modbus_host" required 
                   value="${isEdit ? obj.modbus_host : ""}" 
                   class="form-control" placeholder="Например: 192.168.10.112">
          </div>
          <div class="form-group">
            <label>Порт Modbus TCP</label>
            <input type="number" name="modbus_port" value="${isEdit ? obj.modbus_port : 502}" 
                   class="form-control" min="1" max="65535">
          </div>
          <div class="form-group">
            <label>Modbus Unit ID</label>
            <input type="number" name="modbus_unit_id" value="${isEdit ? obj.modbus_unit_id : 1}" 
                   class="form-control" min="1" max="247">
          </div>
          ${isEdit ? `
          <div class="object-config-tools">
            <div class="object-config-tools__title">Конфигурация объекта</div>
            <div class="object-config-tools__actions">
              <button type="button" class="btn btn-secondary btn-sm" onclick="exportObject('${escapeHtml(obj.object_id)}')"><svg class="btn__icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2v8M5 7l3 3 3-3M3 12v2h10v-2"/></svg>Экспортировать</button>
              <button type="button" class="btn btn-secondary btn-sm" onclick="importObjectFile('${escapeHtml(obj.object_id)}')"><svg class="btn__icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 14V6M5 9l3-3 3 3M3 2v2h10V2"/></svg>Импортировать</button>
            </div>
          </div>` : ""}
          <div class="form-actions">
            ${isEdit ? `<button type="button" class="btn btn-danger" onclick="handleDeleteObject('${obj.object_id}')">Удалить</button>` : ""}
            <button type="button" class="btn btn-secondary" onclick="closeObjectForm()">Отмена</button>
            <button type="submit" class="btn btn-primary">${isEdit ? "Сохранить" : "Создать"}</button>
          </div>
        </form>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
}

window.closeObjectForm = function() {
  const modal = document.querySelector(".modal");
  if (modal) modal.remove();
};

window.handleObjectFormSubmit = async function(event, existingObjectId) {
  event.preventDefault();
  const form = event.target;
  const isEdit = existingObjectId !== null;
  
  const data = {
    object_id: form.object_id.value.trim(),
    name: form.name.value.trim(),
    modbus_host: form.modbus_host.value.trim(),
    modbus_port: parseInt(form.modbus_port.value, 10),
    modbus_unit_id: parseInt(form.modbus_unit_id.value, 10),
  };
  
  try {
    if (isEdit) {
      await fetchJsonWithMethod(`/api/objects/${existingObjectId}`, "PUT", data);
    } else {
      await fetchJsonWithMethod("/api/objects", "POST", data);
    }
    closeObjectForm();
    await loadObjects();
    showNotification(isEdit ? "Объект обновлён" : "Объект создан", "success");
  } catch (err) {
    showNotification(`Ошибка: ${err.message}`, "error");
  }
};

window.handleDeleteObject = async function(objectId) {
  if (!confirm(`Удалить объект "${objectId}" и все связанные данные? Это действие нельзя отменить.`)) return;
  
  try {
    await fetchJsonWithMethod(`/api/objects/${objectId}`, "DELETE");
    closeObjectForm();
    await loadObjects();
    showNotification("Объект удалён", "success");
  } catch (err) {
    showNotification(`Ошибка удаления: ${err.message}`, "error");
  }
};

/* -
Статистика
- */
async function loadStats() {
  setStatsLoadingState(true);
  try {
    const [health, stats] = await Promise.all([
      fetchJson("/api/health/detailed"),
      fetchJson("/api/stats"),
    ]);

    updateServiceCards(health);
    updateStatsCards(stats.totals);
    updateObjectsTable(stats.objects);
    updateEventStats(stats.totals);
  } catch (err) {
    console.error("Failed to load stats:", err);
    updateStatsError(err);
    showNotification(`Ошибка загрузки статистики: ${err.message}`, "error");
  } finally {
    setStatsLoadingState(false);
  }
}

function setStatsLoadingState(loading) {
  if (!loading) return;
  ["statObjects", "statZones", "statEvents24h", "statEventsTotal",
   "statAlarm24h", "statFault24h", "statAttention24h", "statUnknown24h"]
    .forEach(id => { const node = document.getElementById(id); if (node) node.textContent = "…"; });
}

function updateStatsError(err) {
  ["statObjects", "statZones", "statEvents24h", "statEventsTotal",
   "statAlarm24h", "statFault24h", "statAttention24h", "statUnknown24h"]
    .forEach(id => { const node = document.getElementById(id); if (node) node.textContent = "—"; });
  const tbody = document.getElementById("objectsTableBody");
  if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="empty-state">Ошибка загрузки: ${escapeHtml(err.message)}</td></tr>`;
}

function updateServiceCards(health) {
  const services = ["backend", "database", "mqtt", "websocket"];
  const cards = document.querySelectorAll(".service-card");
  cards.forEach((card, index) => {
    const data = health[services[index]];
    card.dataset.status = data.status;
    card.querySelector(".service-card__status").textContent = data.detail;
  });
}

function updateStatsCards(totals) {
  document.getElementById("statObjects").textContent = totals.objects ?? 0;
  document.getElementById("statZones").textContent = totals.zones ?? 0;
  document.getElementById("statEvents24h").textContent = totals.events_24h ?? 0;
  document.getElementById("statEventsTotal").textContent = totals.events_total ?? 0;
}

function updateEventStats(totals) {
  const values = {
    statAlarm24h: totals.alarm_events_24h ?? 0,
    statFault24h: totals.fault_events_24h ?? 0,
    statAttention24h: totals.attention_events_24h ?? 0,
    statUnknown24h: totals.unknown_events_24h ?? 0,
  };
  Object.entries(values).forEach(([id, value]) => {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
  });
}

function updateObjectsTable(objects) {
  const tbody = document.getElementById("objectsTableBody");
  
  if (objects.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">Объектов пока нет</td></tr>';
    return;
  }
  
  tbody.innerHTML = objects.map(obj => {
    const statusClass = obj.agent_status === "online" ? "status-online" : obj.agent_status === "offline" ? "status-offline" : "status-warning";
    const statusLabelStr = obj.agent_status === "online" ? "Онлайн" : obj.agent_status === "offline" ? "Оффлайн" : "Проблемы";
    const modbusAddr = `${obj.modbus_host || "—"}:${obj.modbus_port || 502}`;
    
    const lastEvent = obj.last_event_time 
      ? `${formatTime(obj.last_event_time)} — ${obj.last_event_label || "—"}`
      : "Нет событий";
    
    return `
      <tr>
        <td>
          <div class="object-cell">
            <div class="object-cell__name">${obj.name || obj.object_id}</div>
            <div class="object-cell__id">${obj.object_id}</div>
          </div>
        </td>
        <td><span class="modbus-addr">${modbusAddr}</span></td>
        <td>${obj.zones_count}</td>
        <td>${obj.events_count}</td>
        <td><span class="agent-status ${statusClass}">${statusLabelStr}</span></td>
        <td>${lastEvent}</td>
      </tr>
    `;
  }).join("");
}


/* -
Коды событий
- */
const EVENT_TYPE_LABELS = {
  normal: "Норма",
  alarm: "Тревога",
  attention: "Внимание",
  fault: "Неисправность",
  disabled: "Отключен",
  info: "Информация",
  unknown: "Неизвестно",
};

let eventCodesLoading = false;

async function loadEventCodes() {
  const body = document.getElementById("eventCodesTableBody");
  if (body && !state.eventCodes.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty-state">Загрузка…</td></tr>';
  }
  if (eventCodesLoading) return;
  eventCodesLoading = true;

  try {
    const data = await fetchJson("/api/event-codes");
    if (!Array.isArray(data)) throw new Error("API вернул некорректный формат списка кодов");
    state.eventCodes = data;
    renderEventCodes();
  } catch (err) {
    console.error("Failed to load event codes:", err);
    if (body) body.innerHTML = `<tr><td colspan="7" class="empty-state">Ошибка загрузки: ${escapeHtml(err.message)}</td></tr>`;
    showNotification(`Ошибка загрузки кодов: ${err.message}`, "error");
  } finally {
    eventCodesLoading = false;
  }
}

function renderEventCodes() {
  const body = document.getElementById("eventCodesTableBody");
  if (!body) return;
  const filter = (document.getElementById("eventCodeFilter")?.value || "").trim().toLowerCase();
  const rows = state.eventCodes.filter(code =>
    !filter || String(code.code).includes(filter) || code.label.toLowerCase().includes(filter)
  );
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty-state">Коды не найдены</td></tr>';
    return;
  }
  body.innerHTML = rows.map(code => `
    <tr>
      <td><span class="event-row__code">#${code.code}</span></td>
      <td><div><b>${escapeHtml(code.label)}</b>${code.description ? `<div class="event-code-description">${escapeHtml(code.description)}</div>` : ""}</div></td>
      <td>${escapeHtml(EVENT_TYPE_LABELS[code.event_type] || code.event_type)}</td>
      <td>${code.priority ?? "—"}</td>
      <td>${code.source === "bolid-re-guide" ? "РЭ С2000-ПП" : "Пользовательский"}</td>
      <td><label class="switch"><input type="checkbox" ${code.enabled ? "checked" : ""} onchange="toggleEventCode(${code.code}, this.checked)"><span></span></label></td>
      <td class="table-actions"><button class="btn btn-secondary btn-sm" onclick="showEditEventCodeForm(${code.code})">Изменить</button><button class="btn btn-danger btn-sm" onclick="deleteEventCode(${code.code})">Удалить</button></td>
    </tr>
  `).join("");
}

function eventTypeOptions(selected = "unknown") {
  return Object.entries(EVENT_TYPE_LABELS).map(([value, label]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`).join("");
}

function showEventCodeForm(code = null) {
  const modalOld = document.querySelector(".modal");
  if (modalOld) modalOld.remove();
  const isEdit = Boolean(code);
  const modal = document.createElement("div");
  modal.className = "modal";
  modal.innerHTML = `
    <div class="modal__backdrop" onclick="closeEventCodeForm()"></div>
    <div class="modal__content">
      <div class="modal__header"><h3>${isEdit ? `Код ${code.code}` : "Новый код события"}</h3><button class="modal__close" onclick="closeEventCodeForm()">&times;</button></div>
      <div class="modal__body">
        <form onsubmit="handleEventCodeSubmit(event, ${isEdit ? code.code : "null"})">
          <div class="form-group"><label>Код *</label><input class="form-control" type="number" name="code" min="0" max="255" required ${isEdit ? "disabled" : ""} value="${isEdit ? code.code : ""}"></div>
          <div class="form-group"><label>Название *</label><input class="form-control" type="text" name="label" maxlength="255" required value="${escapeHtml(isEdit ? code.label : "")}"></div>
          <div class="form-group"><label>Тип для dashboard</label><select class="form-control" name="event_type">${eventTypeOptions(isEdit ? code.event_type : "unknown")}</select></div>
          <div class="form-group"><label>Описание</label><textarea class="form-control" name="description" rows="3" maxlength="1024">${escapeHtml(isEdit ? code.description : "")}</textarea></div>
          <div class="form-group"><label>Приоритет</label><input class="form-control" type="number" name="priority" min="0" max="255" value="${isEdit && code.priority != null ? code.priority : ""}"></div>
          <div class="form-group"><label><input type="checkbox" name="enabled" ${!isEdit || code.enabled ? "checked" : ""}> Использовать эту расшифровку</label></div>
          <div class="form-actions"><button type="button" class="btn btn-secondary" onclick="closeEventCodeForm()">Отмена</button><button type="submit" class="btn btn-primary">${isEdit ? "Сохранить" : "Создать"}</button></div>
        </form>
      </div>
    </div>`;
  document.body.appendChild(modal);
}

window.showAddEventCodeForm = () => showEventCodeForm(null);
window.showEditEventCodeForm = (code) => {
  const found = state.eventCodes.find(item => item.code === code);
  if (found) showEventCodeForm(found);
};
window.closeEventCodeForm = () => document.querySelector(".modal")?.remove();

window.handleEventCodeSubmit = async function(event, existingCode) {
  event.preventDefault();
  const form = event.target;
  const data = {
    code: parseInt(form.code.value, 10),
    label: form.label.value.trim(),
    event_type: form.event_type.value,
    description: form.description.value.trim(),
    priority: form.priority.value ? parseInt(form.priority.value, 10) : null,
    enabled: form.enabled.checked,
  };
  try {
    if (existingCode === null) {
      await fetchJsonWithMethod("/api/event-codes", "POST", data);
    } else {
      delete data.code;
      await fetchJsonWithMethod(`/api/event-codes/${existingCode}`, "PUT", data);
    }
    closeEventCodeForm();
    await loadEventCodes();
    showNotification(existingCode === null ? "Код добавлен" : "Код обновлён", "success");
  } catch (err) {
    showNotification(`Ошибка: ${err.message}`, "error");
  }
};

window.toggleEventCode = async function(code, enabled) {
  try {
    await fetchJsonWithMethod(`/api/event-codes/${code}`, "PUT", { enabled });
    const item = state.eventCodes.find(entry => entry.code === code);
    if (item) item.enabled = enabled;
  } catch (err) {
    showNotification(`Ошибка: ${err.message}`, "error");
    renderEventCodes();
  }
};

window.deleteEventCode = async function(code) {
  if (!confirm(`Удалить расшифровку кода ${code}?`)) return;
  try {
    await fetchJsonWithMethod(`/api/event-codes/${code}`, "DELETE");
    await loadEventCodes();
    showNotification(`Код ${code} удалён`, "success");
  } catch (err) {
    showNotification(`Ошибка удаления: ${err.message}`, "error");
  }
};

/* -
ntfy notifications
- */
async function loadNotificationPreferences() {
  if (!state.currentUser) return;
  try {
    const rows = await fetchJson("/api/notifications");
    const body = document.getElementById("notificationPreferencesBody");
    const url = rows[0]?.subscribe_url || "—";
    document.getElementById("ntfySubscribeUrl").textContent = url;
    if (!body) return;
    body.innerHTML = rows.map(row => `<tr>
      <td>${escapeHtml(row.object_name || row.object_id)}</td>
      <td>${row.enabled ? "Включён" : "Отключён"}</td>
      <td><input type="checkbox" ${row.critical ? "checked" : ""} onchange="saveNotificationPreference('${escapeHtml(row.object_id)}')" data-notify-object="${escapeHtml(row.object_id)}" data-notify-field="critical"></td>
      <td><input type="checkbox" ${row.warning ? "checked" : ""} onchange="saveNotificationPreference('${escapeHtml(row.object_id)}')" data-notify-object="${escapeHtml(row.object_id)}" data-notify-field="warning"></td>
      <td><input type="checkbox" ${row.info ? "checked" : ""} onchange="saveNotificationPreference('${escapeHtml(row.object_id)}')" data-notify-object="${escapeHtml(row.object_id)}" data-notify-field="info"></td>
      <td><button class="btn btn-secondary btn-sm" onclick="toggleNotification('${escapeHtml(row.object_id)}', ${!row.enabled})">${row.enabled ? "Выключить" : "Включить"}</button></td>
    </tr>`).join("") || `<tr><td colspan="6" class="empty-state">Нет доступных объектов.</td></tr>`;
  } catch (err) { showNotification(`Ошибка ntfy: ${err.message}`, "error"); }
}
window.saveNotificationPreference = async function(objectId) {
  const row = document.querySelectorAll(`[data-notify-object="${CSS.escape(objectId)}"]`);
  const current = (await fetchJson("/api/notifications")).find(item => item.object_id === objectId);
  const values = Object.fromEntries([...row].map(input => [input.dataset.notifyField, input.checked]));
  try { await fetchJsonWithMethod(`/api/notifications/${encodeURIComponent(objectId)}`, "PUT", { enabled: current?.enabled ?? true, critical: values.critical ?? current?.critical ?? true, warning: values.warning ?? current?.warning ?? true, info: values.info ?? current?.info ?? false }); } catch (err) { showNotification(`Ошибка: ${err.message}`, "error"); }
};
window.toggleNotification = async function(objectId, enabled) {
  const rows = await fetchJson("/api/notifications"); const current = rows.find(item => item.object_id === objectId);
  if (!current) return;
  try { await fetchJsonWithMethod(`/api/notifications/${encodeURIComponent(objectId)}`, "PUT", { enabled, critical: current.critical, warning: current.warning, info: current.info }); await loadNotificationPreferences(); } catch (err) { showNotification(`Ошибка: ${err.message}`, "error"); }
};
window.testNtfyNotification = async function() {
  try { await fetchJsonWithMethod("/api/notifications/test", "POST", {}); showNotification("Тестовое ntfy-уведомление отправлено", "success"); } catch (err) { showNotification(`Ошибка ntfy: ${err.message}`, "error"); }
};

/* -
Пользователи
- */
async function loadUsers() {
  const users = await fetchJson("/api/users");
  const body = document.getElementById("usersTableBody");
  if (!body) return;
  body.innerHTML = users.map(user => `<tr><td>${escapeHtml(user.username)}</td><td>${escapeHtml(user.display_name)}</td><td>${escapeHtml(user.role)}</td><td>${user.role === "admin" ? "Все" : escapeHtml(user.object_ids.join(", ") || "Нет")}</td><td>${user.is_active ? "Активен" : "Отключён"}</td><td>${user.id === state.currentUser?.id ? "—" : `<button class="btn btn-secondary btn-sm" onclick="editUser(${user.id})">Изменить</button> <button class="btn btn-secondary btn-sm" onclick="deleteUser(${user.id})">Удалить</button>`}</td></tr>`).join("") || `<tr><td colspan="6" class="empty-state">Пользователей нет.</td></tr>`;
}
window.showUserForm = async function(user = null) {
  const roles = await fetchJson("/api/roles");
  const objects = state.objects;
  const username = prompt("Логин:", user?.username || ""); if (!username) return;
  const display_name = prompt("Имя:", user?.display_name || "");
  const role = prompt(`Роль (${roles.map(r => r.name).join(", ")}):`, user?.role || "operator"); if (!role) return;
  const password = prompt(user ? "Новый пароль (пусто — не менять):" : "Пароль (минимум 8 символов):", "");
  const objectText = prompt(`Объекты через запятую (${objects.map(o => o.object_id).join(", ")}):`, user?.object_ids?.join(", ") || "");
  const object_ids = objectText ? objectText.split(",").map(v => v.trim()).filter(Boolean) : [];
  try {
    if (user) await fetchJsonWithMethod(`/api/users/${user.id}`, "PUT", { display_name, role, object_ids, ...(password ? {password} : {}) });
    else await fetchJsonWithMethod("/api/users", "POST", { username, display_name, role, object_ids, password });
    await loadUsers(); showNotification("Пользователь сохранён", "success");
  } catch (err) { showNotification(`Ошибка: ${err.message}`, "error"); }
};
window.editUser = async function(id) { const users = await fetchJson("/api/users"); const user = users.find(u => u.id === id); if (user) showUserForm(user); };
window.deleteUser = async function(id) { if (!confirm("Удалить пользователя?")) return; try { await fetchJsonWithMethod(`/api/users/${id}`, "DELETE"); await loadUsers(); } catch (err) { showNotification(`Ошибка: ${err.message}`, "error"); } };
window.exportUsers = async function() { try { const data = await fetchJson("/api/users/export"); const blob = new Blob([JSON.stringify(data, null, 2)], {type:"application/json"}); const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download="tengsl-users.json"; a.click(); URL.revokeObjectURL(a.href); } catch(err) { showNotification(`Ошибка: ${err.message}`, "error"); } };
window.importUsersFromFile = async function(event) { const file=event.target.files?.[0]; if(!file)return; try { const data=JSON.parse(await file.text()); const result=await fetchJsonWithMethod("/api/users/import", "POST", data); await loadUsers(); showNotification(`Импортировано: создано ${result.users_created}, обновлено ${result.users_updated}`, "success"); } catch(err) { showNotification(`Ошибка импорта: ${err.message}`, "error"); } event.target.value=""; };

/* -
Инициализация
- */
async function init() {
  loadNotificationHistory();
  document.getElementById("loginForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("loginError");
    error.hidden = true;
    try {
      const response = await fetch(`${window.TENGSL_CONFIG.apiBase}/api/auth/login`, { method: "POST", credentials: "include", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ username: document.getElementById("loginUsername").value, password: document.getElementById("loginPassword").value }) });
      if (!response.ok) throw new Error("Неверный логин или пароль");
      await loadCurrentUser();
      await loadObjects();
      startObjectListPolling();
    } catch (err) { error.textContent = err.message; error.hidden = false; }
  });
  try {
    if (await loadCurrentUser()) { await loadObjects(); await loadNotificationPreferences(); startObjectListPolling(); }
  } catch (err) {
    el.bannerStatus.textContent = "Не удалось подключиться к backend";
    el.bannerDetail.textContent = String(err);
    setConnState("disconnected");
  }
}

init();