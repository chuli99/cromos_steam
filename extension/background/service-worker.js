// Service worker (MV3): intermediario entre el content script y el backend.
// Llama a /api/profit/{appid}, cachea la respuesta en chrome.storage.local con TTL
// y la devuelve. Así, al revisitar la misma página no se vuelve a pegar al backend.

const DEFAULT_BACKEND_URL = "http://localhost:8000";
const CACHE_TTL_MS = 60 * 60 * 1000; // 1h, alineado con el TTL sugerido del backend.
const FETCH_TIMEOUT_MS = 20000;      // corta requests colgadas (backend esperando un cooldown de Steam)

// URL del backend configurable desde el popup (chrome.storage.local).
async function getBackendUrl() {
  const { backendUrl } = await chrome.storage.local.get("backendUrl");
  return (backendUrl || DEFAULT_BACKEND_URL).replace(/\/+$/, "");
}

// fetch con timeout (AbortController): evita que la UI quede colgada si el backend
// se demora (p. ej. reintentando ante un 429 de Steam).
async function fetchWithTimeout(url) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, { signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}

// Mensaje de error de red unificado (distingue timeout de backend caído).
function backendError(err, base) {
  if (err && err.name === "AbortError") {
    return "El backend tardó demasiado (posible rate limit de Steam). Reintentá en un rato.";
  }
  return `No se pudo contactar el backend (${base}). ¿Está levantado?`;
}

// Devuelve la respuesta de profit, usando caché si está fresca.
// ``foilsOverride`` (bool) permite forzar el flag de foils sin tocar la config
// guardada; el escaneo de búsqueda lo usa para pedir SIEMPRE sin foils (más rápido).
async function getProfit(appid, foilsOverride) {
  let foils;
  if (typeof foilsOverride === "boolean") {
    foils = foilsOverride;
  } else {
    const { includeFoils } = await chrome.storage.local.get("includeFoils");
    foils = Boolean(includeFoils);
  }

  // La key distingue por flag de foils: el desglose cambia según se pidan o no.
  const cacheKey = `profit:${appid}:${foils ? "f" : "n"}`;
  const stored = (await chrome.storage.local.get(cacheKey))[cacheKey];
  if (stored && stored.result && Date.now() - stored.ts < CACHE_TTL_MS) {
    // Resultado reciente (positivo, negativo, sin cromos o F2P/DLC): se reutiliza
    // sin volver a pegarle a Steam. ``cached: true`` avisa al escáner para no esperar.
    return { ...stored.result, cached: true };
  }

  const base = await getBackendUrl();
  const query = foils ? "?include_foils=true" : "";
  try {
    const resp = await fetchWithTimeout(`${base}/api/profit/${appid}${query}`);
    if (!resp.ok) {
      // Propagar el detalle del backend (ej: 422 free-to-play, 404 sin cromos).
      const body = await resp.json().catch(() => ({}));
      const result = { ok: false, error: body.detail || `HTTP ${resp.status}`, status: resp.status };
      // Cachear solo resultados DEFINITIVOS de negocio (404 sin cromos, 422 F2P/DLC):
      // no son errores, no tiene sentido re-escanearlos. Los transitorios (429/5xx)
      // NO se cachean, así el próximo escaneo los reintenta.
      if (resp.status === 404 || resp.status === 422) {
        await chrome.storage.local.set({ [cacheKey]: { ts: Date.now(), result } });
      }
      return { ...result, cached: false };
    }
    const data = await resp.json();
    const result = { ok: true, data };
    await chrome.storage.local.set({ [cacheKey]: { ts: Date.now(), result } });
    return { ...result, cached: false };
  } catch (err) {
    // Error de red o timeout (transitorio): no cachear.
    return { ok: false, error: backendError(err, base) };
  }
}

// Precio de referencia del Saco de Gemas (1000 gemas). Sin caché local: el backend
// ya lo cachea y el panel lo pide una sola vez por escaneo.
async function getSackPrice() {
  const base = await getBackendUrl();
  try {
    const resp = await fetchWithTimeout(`${base}/api/gems/sack`);
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      return { ok: false, error: body.detail || `HTTP ${resp.status}`, status: resp.status };
    }
    return { ok: true, data: await resp.json() };
  } catch (err) {
    return { ok: false, error: backendError(err, base) };
  }
}

// Valor de un booster pack (costo en gemas vs precio de venta). Cachea por
// appid+gemas en chrome.storage.local con el mismo TTL que el profit.
async function getBooster(appid, gemCost, name) {
  const cacheKey = `booster:${appid}:${gemCost}`;
  const stored = (await chrome.storage.local.get(cacheKey))[cacheKey];
  if (stored && stored.result && Date.now() - stored.ts < CACHE_TTL_MS) {
    // Reciente: se reutiliza sin re-consultar. ``cached: true`` evita la espera.
    return { ...stored.result, cached: true };
  }

  const base = await getBackendUrl();
  const qs = new URLSearchParams({ gem_cost: String(gemCost), name }).toString();
  try {
    const resp = await fetchWithTimeout(`${base}/api/booster/${appid}?${qs}`);
    if (!resp.ok) {
      // Errores (incl. 429/red): NO se cachean -> se reintentan en el próximo escaneo.
      const body = await resp.json().catch(() => ({}));
      return { ok: false, error: body.detail || `HTTP ${resp.status}`, status: resp.status };
    }
    const data = await resp.json();
    const result = { ok: true, data };
    await chrome.storage.local.set({ [cacheKey]: { ts: Date.now(), result } });
    return { ...result, cached: false };
  } catch (err) {
    return { ok: false, error: backendError(err, base) };
  }
}

// Canal de mensajes con el content script y el popup.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "GET_PROFIT") {
    getProfit(msg.appid, msg.includeFoils).then(sendResponse);
    return true; // respuesta asíncrona
  }
  if (msg && msg.type === "GET_BOOSTER") {
    getBooster(msg.appid, msg.gemCost, msg.name).then(sendResponse);
    return true; // respuesta asíncrona
  }
  if (msg && msg.type === "GET_SACK") {
    getSackPrice().then(sendResponse);
    return true; // respuesta asíncrona
  }
  if (msg && msg.type === "CLEAR_CACHE") {
    chrome.storage.local.get(null).then((all) => {
      const keys = Object.keys(all).filter(
        (k) => k.startsWith("profit:") || k.startsWith("booster:")
      );
      chrome.storage.local.remove(keys).then(() => sendResponse({ ok: true, cleared: keys.length }));
    });
    return true;
  }
  return false;
});
