// Service worker (MV3): intermediario entre el content script y el backend.
// Llama a /api/profit/{appid}, cachea la respuesta en chrome.storage.local con TTL
// y la devuelve. Así, al revisitar la misma página no se vuelve a pegar al backend.

const DEFAULT_BACKEND_URL = "http://localhost:8000";
const CACHE_TTL_MS = 60 * 60 * 1000; // 1h, alineado con el TTL sugerido del backend.

// URL del backend configurable desde el popup (chrome.storage.local).
async function getBackendUrl() {
  const { backendUrl } = await chrome.storage.local.get("backendUrl");
  return (backendUrl || DEFAULT_BACKEND_URL).replace(/\/+$/, "");
}

// Devuelve la respuesta de profit, usando caché si está fresca.
async function getProfit(appid) {
  const cacheKey = `profit:${appid}`;
  const stored = (await chrome.storage.local.get(cacheKey))[cacheKey];
  if (stored && Date.now() - stored.ts < CACHE_TTL_MS) {
    return { ok: true, data: stored.data, cached: true };
  }

  const base = await getBackendUrl();
  try {
    const resp = await fetch(`${base}/api/profit/${appid}`);
    if (!resp.ok) {
      // Propagar el detalle del backend (ej: 422 free-to-play, 404 sin cromos).
      const body = await resp.json().catch(() => ({}));
      return { ok: false, error: body.detail || `HTTP ${resp.status}`, status: resp.status };
    }
    const data = await resp.json();
    await chrome.storage.local.set({ [cacheKey]: { ts: Date.now(), data } });
    return { ok: true, data, cached: false };
  } catch (err) {
    // Error de red: el backend probablemente no está levantado.
    return { ok: false, error: `No se pudo contactar el backend (${base}). ¿Está levantado?` };
  }
}

// Canal de mensajes con el content script y el popup.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "GET_PROFIT") {
    getProfit(msg.appid).then(sendResponse);
    return true; // respuesta asíncrona
  }
  if (msg && msg.type === "CLEAR_CACHE") {
    chrome.storage.local.get(null).then((all) => {
      const keys = Object.keys(all).filter((k) => k.startsWith("profit:"));
      chrome.storage.local.remove(keys).then(() => sendResponse({ ok: true, cleared: keys.length }));
    });
    return true;
  }
  return false;
});
