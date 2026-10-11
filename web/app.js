"use strict";

const element = (id) => document.getElementById(id);
const text = (id, value) => { element(id).textContent = value; };
const gib = (bytes) => `${(bytes / 1073741824).toFixed(2)} GiB`;
let refreshing = false;
let timer;

async function getJSON(path) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch(path, { cache: "no-store", signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

function rows(id, values, empty) {
  const body = element(id);
  body.replaceChildren();
  if (values.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 3;
    cell.textContent = empty;
    row.append(cell);
    body.append(row);
    return;
  }
  for (const valuesInRow of values) {
    const row = document.createElement("tr");
    for (const value of valuesInRow) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
}

function renderSystem(system) {
  text("os", system.os_version || system.os);
  text("architecture", system.architecture);
  text("hostname", system.hostname || "Не определено");
  text("ram", system.ram_total_bytes ? gib(system.ram_total_bytes) : "Не определено");
  text("ram-available", `Доступно ${gib(system.ram_available_bytes)}`);
  text("ips", system.local_ips.join(", ") || "Нет адресов, кроме loopback");
  text("cpu", `${system.cpu.model || "Модель не определена"} · ${system.cpu.logical_cores} логических ядер`);
  rows("disks", system.disks.map((disk) => [disk.mount_point, gib(disk.available_bytes), gib(disk.total_bytes)]), "ОС не сообщила о дисках");
  rows("networks", system.network_interfaces.map((net) => [net.name, net.mac_address, net.addresses.join(", ") || "Без IP"]), "ОС не сообщила об интерфейсах");
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  clearTimeout(timer);
  element("refresh").disabled = true;
  try {
    const [status, version, system] = await Promise.allSettled([
      getJSON("/api/v1/status"), getJSON("/api/v1/version"), getJSON("/api/v1/system"),
    ]);
    const online = status.status === "fulfilled" && status.value.status === "ok";
    text("status", online ? "Online" : "Offline");
    element("status").className = `status ${online ? "online" : "offline"}`;
    const messages = [];
    if (!online) messages.push("Ядро не отвечает. Запустите mira serve; показанные ранее данные могут устареть.");
    if (version.status === "fulfilled") text("version", version.value.core);
    else messages.push("Не удалось обновить версию.");
    if (system.status === "fulfilled") {
      renderSystem(system.value);
      messages.push(...system.value.warnings);
      text("updated", `Системные данные: ${new Date().toLocaleTimeString()} · обновление каждые 5 секунд`);
    } else {
      messages.push("Не удалось обновить системные данные; предыдущие значения могут устареть.");
    }
    text("message", messages.join(" "));
  } catch (error) {
    text("message", `Не удалось отобразить ответ ядра: ${error.message}`);
  } finally {
    refreshing = false;
    element("refresh").disabled = false;
    timer = setTimeout(refresh, 5000);
  }
}

element("refresh").addEventListener("click", refresh);
refresh();
