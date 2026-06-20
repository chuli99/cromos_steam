// Popup: configura la URL del backend (guardada en chrome.storage.local) y permite
// limpiar la caché local de respuestas de profit.

const DEFAULT_BACKEND_URL = "http://localhost:8000";

const $url = document.getElementById("backend-url");
const $save = document.getElementById("save");
const $status = document.getElementById("status");
const $clear = document.getElementById("clear-cache");
const $foils = document.getElementById("include-foils");
const $scanDelay = document.getElementById("scan-delay");

const DEFAULT_SCAN_DELAY_MS = 800;

// Muestra un mensaje temporal de estado.
function showStatus(text, kind) {
  $status.textContent = text;
  $status.className = `status ${kind || ""}`;
  if (text) {
    setTimeout(() => {
      $status.textContent = "";
      $status.className = "status";
    }, 2500);
  }
}

// Carga la URL, el flag de foils y el delay de escaneo al abrir el popup.
async function load() {
  const { backendUrl, includeFoils, scanDelayMs } = await chrome.storage.local.get([
    "backendUrl",
    "includeFoils",
    "scanDelayMs",
  ]);
  $url.value = backendUrl || DEFAULT_BACKEND_URL;
  $foils.checked = Boolean(includeFoils);
  $scanDelay.value = Number.isFinite(scanDelayMs) ? scanDelayMs : DEFAULT_SCAN_DELAY_MS;
}

// Guarda el delay de escaneo (ms). Vacío o inválido => default.
$scanDelay.addEventListener("change", async () => {
  let value = parseInt($scanDelay.value, 10);
  if (!Number.isFinite(value) || value < 0) value = DEFAULT_SCAN_DELAY_MS;
  $scanDelay.value = value;
  await chrome.storage.local.set({ scanDelayMs: value });
  showStatus("Delay guardado ✓", "ok");
});

// Guarda el flag de foils. Como cambia el resultado, limpia la caché local para
// que la próxima visita vuelva a pedir el desglose al backend.
$foils.addEventListener("change", async () => {
  await chrome.storage.local.set({ includeFoils: $foils.checked });
  chrome.runtime.sendMessage({ type: "CLEAR_CACHE" }, () => {
    showStatus(`Foils ${$foils.checked ? "activadas" : "desactivadas"} ✓`, "ok");
  });
});

// Guarda la URL del backend (normalizando barras finales).
$save.addEventListener("click", async () => {
  let value = $url.value.trim();
  if (!value) value = DEFAULT_BACKEND_URL;
  value = value.replace(/\/+$/, "");
  try {
    // Valida que sea una URL bien formada.
    new URL(value);
  } catch {
    showStatus("URL inválida", "error");
    return;
  }
  await chrome.storage.local.set({ backendUrl: value });
  showStatus("Guardado ✓", "ok");
});

// Limpia las entradas de caché (claves profit:*) vía el service worker.
$clear.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "CLEAR_CACHE" }, (resp) => {
    if (resp && resp.ok) {
      showStatus(`Caché limpiada (${resp.cleared})`, "ok");
    } else {
      showStatus("No se pudo limpiar", "error");
    }
  });
});

load();
