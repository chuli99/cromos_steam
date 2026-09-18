// Content script del booster creator (steamcommunity.com/tradingcards/boostercreator).
// Inyecta un panel que escanea TODOS los juegos elegibles para crear booster packs y,
// por cada uno, compara el costo en gemas (valuado con el precio del Saco de Gemas)
// contra el precio de venta del booster en el market. Lista los rentables primero.
//
// Estrategia anti-bloqueo de Steam (igual que el escáner del buscador):
//   - Escaneo SECUENCIAL: una consulta a la vez, sin apilar requests.
//   - Delay configurable entre juegos (popup) + backoff si el backend falla.
//   - Arranque manual (botón). El backend cachea y throttlea priceoverview.

(function () {
  "use strict";

  if (!/\/tradingcards\/boostercreator/.test(window.location.pathname)) return;

  const DEFAULT_DELAY_MS = 800;
  const MAX_BACKOFF_MS = 15000;

  // Modo "avg": ventana de días para el promedio de ventas recientes, y TTL del
  // caché en memoria de ``pricehistory`` (evita re-pedirlo al cambiar de pestaña o
  // reabrir el panel; se pierde al recargar la página, que alcanza para esto).
  const AVG_SAMPLE_DAYS = 2;
  const HISTORY_CACHE_TTL_MS = 6 * 60 * 60 * 1000; // 6h, alineado con cache_ttl_cards del backend

  const state = {
    running: false,
    stop: false,
    onlyProfit: false,
    // Modo de valuación: "sell" = precio de venta listado (hay que esperar comprador);
    // "quick" = pedido de compra más alto (venta instantánea contra buy orders);
    // "avg" = promedio ponderado por volumen de ventas de los últimos AVG_SAMPLE_DAYS días.
    mode: "sell",
    sack: null,             // { price, price_per_gem, gems, currency }
    sackError: null,        // motivo si falló la carga del saco (para mostrarlo)
    games: [],              // [{ appid, name, gems }]
    // Resultados separados por modo: cada apartado mantiene su propia lista.
    results: { sell: [], quick: [], avg: [] },
  };

  // --- Utilidades ---

  function fmt(value, currency) {
    if (value == null) return "—";
    const n = Number(value).toFixed(2);
    return currency === 1 || currency == null ? `$${n}` : `${n} (cur ${currency})`;
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  // URL del market de Steam para un market_hash_name dado (todo bajo appid 753: cromos).
  function steamMarketUrl(hash) {
    return `https://steamcommunity.com/market/listings/753/${encodeURIComponent(hash)}`;
  }

  // URL del market de Steam para el booster pack de un juego. El market_hash_name
  // del booster es "{appid}-{nombre} Booster Pack" bajo el appid 753 (cromos).
  function marketUrl(appid, name) {
    return steamMarketUrl(`${appid}-${name} Booster Pack`);
  }

  // market_hash_name del Saco de Gemas (mismo item que valúa el backend en GEM_SACK_HASH).
  const SACK_HASH = "753-Sack of Gems";

  // --- Modo "avg": promedio de ventas recientes (pricehistory) ---
  //
  // /market/pricehistory requiere sesión logueada: Steam no lo expone a requests
  // anónimas (por eso el backend no lo puede pedir él mismo, a diferencia de
  // priceoverview/orderbook). Como este content script corre en la página de
  // steamcommunity.com del propio usuario, el fetch es mismo-origen y el navegador
  // adjunta sus cookies de sesión solo; no hace falta ningún permiso extra.

  const MONTHS = { Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5, Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11 };

  // Parsea el formato propio de Steam: "Mon DD YYYY HH: +0" (mes en inglés, hora
  // UTC, offset literal). ``Date.parse`` no es confiable con este formato no
  // estándar entre navegadores, así que se arma la fecha a mano.
  function parseHistoryDate(str) {
    const m = /^(\w{3}) (\d{1,2}) (\d{4}) (\d{1,2}):/.exec(str || "");
    if (!m) return null;
    const month = MONTHS[m[1]];
    if (month == null) return null;
    return Date.UTC(Number(m[3]), month, Number(m[2]), Number(m[4]));
  }

  // Pide el historial de ventas crudo del ítem. ``null`` si falló o si el usuario
  // no está logueado (Steam responde success:false, no un error HTTP).
  async function fetchPriceHistory(hash) {
    const url = `https://steamcommunity.com/market/pricehistory/?appid=753&market_hash_name=${encodeURIComponent(hash)}`;
    let resp;
    try {
      resp = await fetch(url, { credentials: "same-origin" });
    } catch (e) {
      return null;
    }
    if (!resp.ok) return null;
    const data = await resp.json().catch(() => null);
    if (!data || !data.success || !Array.isArray(data.prices)) return null;
    return data.prices; // [[ "Mon DD YYYY HH: +0", precio, "volumen" ], ...]
  }

  // Promedio ponderado por volumen de las ventas dentro de los últimos ``days``
  // días. ``null`` si no hubo ventas en la ventana (no confundir con "sin datos").
  function computeRecentAvg(prices, days) {
    const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
    let totalVolume = 0;
    let totalValue = 0;
    for (const entry of prices) {
      const ts = parseHistoryDate(entry[0]);
      if (ts == null || ts < cutoff) continue;
      const vol = Number(entry[2]);
      if (!Number.isFinite(vol) || vol <= 0) continue;
      totalVolume += vol;
      totalValue += Number(entry[1]) * vol;
    }
    if (totalVolume <= 0) return null;
    return { avgPrice: totalValue / totalVolume, volume: totalVolume };
  }

  // Caché en memoria (vive lo que dure la página) del promedio ya calculado por
  // ítem: evita volver a pedir pricehistory si el panel se reabre o se cambia de
  // pestaña sin recargar. Distinto del caché del backend (ese es por HTTP request).
  const historyCache = new Map(); // hash -> { ts, avgPrice, volume }

  async function getRecentAvg(hash, days) {
    const cached = historyCache.get(hash);
    if (cached && Date.now() - cached.ts < HISTORY_CACHE_TTL_MS) {
      return { ...cached, cached: true };
    }
    const prices = await fetchPriceHistory(hash);
    if (!prices) return { avgPrice: null, volume: 0, cached: false, noSession: true };
    const avg = computeRecentAvg(prices, days);
    const result = { ts: Date.now(), avgPrice: avg ? avg.avgPrice : null, volume: avg ? avg.volume : 0 };
    historyCache.set(hash, result);
    return { ...result, cached: false };
  }

  // Steam inicializa la página con CBoosterCreatorPage.Init( [ {...}, ... ], ... ).
  // El content script no puede leer variables JS de la página, pero sí el texto de
  // los <script>: se extrae ese array (JSON balanceado, respetando strings).
  function scanJsonArray(text, start) {
    let depth = 0;
    let inStr = false;
    let esc = false;
    for (let i = start; i < text.length; i++) {
      const ch = text[i];
      if (inStr) {
        if (esc) esc = false;
        else if (ch === "\\") esc = true;
        else if (ch === '"') inStr = false;
      } else if (ch === '"') {
        inStr = true;
      } else if (ch === "[") {
        depth++;
      } else if (ch === "]") {
        depth--;
        if (depth === 0) return text.slice(start, i + 1);
      }
    }
    return null;
  }

  function extractBoosterData() {
    for (const s of document.querySelectorAll("script")) {
      const text = s.textContent || "";
      const idx = text.indexOf("CBoosterCreatorPage.Init(");
      if (idx === -1) continue;
      const start = text.indexOf("[", idx);
      if (start === -1) continue;
      const arr = scanJsonArray(text, start);
      if (!arr) continue;
      try {
        return JSON.parse(arr);
      } catch (e) {
        /* probar el siguiente script */
      }
    }
    return null;
  }

  // Normaliza las entradas crudas a { appid, name, gems }; descarta las inválidas.
  function parseGames() {
    const raw = extractBoosterData();
    if (!Array.isArray(raw)) return [];
    const games = [];
    for (const g of raw) {
      const appid = String(g.appid || "").trim();
      const gems = Number(g.price); // "price" es el costo en gemas
      if (!/^\d+$/.test(appid) || !Number.isFinite(gems) || gems <= 0) continue;
      games.push({ appid, name: g.name || `App ${appid}`, gems });
    }
    return games;
  }

  // --- Panel ---

  let $progress, $list, $startBtn, $onlyProfit, $sack, $sackText, $sackMarket, $note, $tabs;

  // Texto explicativo de cada modo (se muestra bajo la lista).
  const MODE_NOTES = {
    sell:
      "Compara el costo en gemas de cada booster (según el precio del Saco de Gemas) " +
      "contra su precio de venta LISTADO en el market (hay que esperar comprador). " +
      "Escaneo secuencial y respetuoso del rate limit de Steam. Click en un ítem para " +
      "seleccionarlo en la página; 🛒 abre su market.",
    quick:
      "⚡ Profit rápido: compara el costo en gemas contra el PEDIDO DE COMPRA más alto " +
      "vigente (venta instantánea y garantizada contra buy orders, cobrando menos). " +
      "Requiere más consultas a Steam por juego, así que el primer escaneo es más lento. " +
      "Click en un ítem para seleccionarlo; 🛒 abre su market.",
    avg:
      `📊 Punto intermedio: compara el costo en gemas contra el PROMEDIO ponderado por ` +
      `volumen de las ventas de los últimos ${AVG_SAMPLE_DAYS} días (no un solo listado ni ` +
      "la oferta de compra más baja). Usa tu propia sesión de Steam (pricehistory), así " +
      "que necesitás estar logueado; si no, este modo no va a traer resultados. " +
      "Click en un ítem para seleccionarlo; 🛒 abre su market.",
  };

  function buildPanel() {
    const panel = document.createElement("div");
    panel.id = "scp-booster-panel";
    panel.innerHTML = `
      <div class="scp-bp-header">
        <span class="scp-bp-logo">💎 Booster Profit</span>
        <button class="scp-bp-close" title="Cerrar">×</button>
      </div>
      <div class="scp-bp-body">
        <div id="scp-bp-sack" class="scp-bp-sack">
          <span id="scp-bp-sack-text">Saco de gemas: —</span>
          <a id="scp-bp-sack-market" class="scp-bp-market" title="Comprar en el mercado de Steam"
            target="_blank" rel="noopener noreferrer">🛒</a>
        </div>
        <div class="scp-bp-tabs">
          <button class="scp-bp-tab scp-bp-tab-active" data-mode="sell"
            title="Contra el precio de venta listado (hay que esperar comprador)">Listada</button>
          <button class="scp-bp-tab" data-mode="avg"
            title="Contra el promedio de ventas recientes (requiere estar logueado)">📊 Promedio</button>
          <button class="scp-bp-tab" data-mode="quick"
            title="Contra el pedido de compra más alto (venta instantánea garantizada)">⚡ Rápida</button>
        </div>
        <div class="scp-bp-controls">
          <button id="scp-bp-start">Escanear boosters</button>
        </div>
        <label class="scp-bp-check">
          <input type="checkbox" id="scp-bp-onlyprofit" />
          Mostrar solo con profit
        </label>
        <div id="scp-bp-progress" class="scp-bp-progress">Listo para escanear.</div>
        <div id="scp-bp-list" class="scp-bp-list"></div>
        <div id="scp-bp-note" class="scp-bp-note"></div>
      </div>`;
    document.body.appendChild(panel);

    $progress = panel.querySelector("#scp-bp-progress");
    $list = panel.querySelector("#scp-bp-list");
    $startBtn = panel.querySelector("#scp-bp-start");
    $onlyProfit = panel.querySelector("#scp-bp-onlyprofit");
    $sack = panel.querySelector("#scp-bp-sack");
    $sackText = panel.querySelector("#scp-bp-sack-text");
    $sackMarket = panel.querySelector("#scp-bp-sack-market");
    $sackMarket.href = steamMarketUrl(SACK_HASH);
    // No propagar el click al contenedor (no tiene handler propio, pero mantiene
    // el mismo patrón que el 🛒 de la lista por consistencia).
    $sackMarket.addEventListener("click", (e) => e.stopPropagation());
    $note = panel.querySelector("#scp-bp-note");
    $tabs = panel.querySelectorAll(".scp-bp-tab");
    $note.textContent = MODE_NOTES[state.mode];

    // Cambiar de apartado: cada modo conserva sus propios resultados. Bloqueado
    // durante un escaneo para no mezclar listas.
    for (const tab of $tabs) {
      tab.addEventListener("click", () => {
        if (state.running || tab.dataset.mode === state.mode) return;
        state.mode = tab.dataset.mode;
        for (const t of $tabs) t.classList.toggle("scp-bp-tab-active", t === tab);
        $note.textContent = MODE_NOTES[state.mode];
        $progress.textContent = state.results[state.mode].length
          ? `${state.results[state.mode].length} resultados de este modo.`
          : "Listo para escanear.";
        renderList();
      });
    }

    $startBtn.addEventListener("click", () => {
      if (state.running) {
        state.stop = true;
        $startBtn.textContent = "Deteniendo…";
      } else {
        scanAll();
      }
    });

    $onlyProfit.addEventListener("change", () => {
      state.onlyProfit = $onlyProfit.checked;
      renderList();
    });

    panel.querySelector(".scp-bp-close").addEventListener("click", () => panel.remove());

    // Cargar el precio del saco al abrir (no solo al escanear): así se ve de entrada
    // y, si falla, el motivo queda visible para diagnosticar.
    loadSack();
  }

  // Pide el precio del Saco de Gemas y lo renderiza. Guarda el error si falla.
  async function loadSack() {
    $sackText.textContent = "Saco de gemas: cargando…";
    $sack.classList.remove("scp-bp-sack-err");
    const resp = await querySack();
    if (resp && resp.ok) {
      state.sack = resp.data;
      state.sackError = null;
    } else {
      state.sack = null;
      state.sackError = (resp && resp.error) || "no disponible";
    }
    renderSack();
    return state.sack;
  }

  function renderSack() {
    if (state.sack) {
      $sackText.textContent =
        `Saco de gemas (1000): ${fmt(state.sack.price, state.sack.currency)} ` +
        `· ${fmt(state.sack.price_per_gem, state.sack.currency)}/gema`;
      $sack.classList.remove("scp-bp-sack-err");
    } else {
      $sackText.textContent = `Saco de gemas: ${state.sackError || "no disponible"}`;
      $sack.classList.add("scp-bp-sack-err");
    }
  }

  function renderProgress(done, total, withProfit, reused) {
    $progress.textContent =
      `${done}/${total} escaneados · ${withProfit} con profit · ${reused} en caché`;
  }

  function renderList() {
    const items = state.results[state.mode]
      .filter((r) => (state.onlyProfit ? r.profitPositive : true))
      .sort((a, b) => (b.profit ?? -Infinity) - (a.profit ?? -Infinity));

    $list.textContent = "";
    for (const r of items) {
      const line = document.createElement("div");
      line.className = "scp-bp-item";

      const name = document.createElement("span");
      name.className = "scp-bp-name";
      name.textContent = `${r.name} · ${r.gemCost}💎`;

      const val = document.createElement("span");
      if (r.status === "ok") {
        val.className = r.profitPositive ? "scp-bp-pos" : "scp-bp-neg";
        val.textContent = `${r.profitPositive ? "+" : ""}${fmt(r.profit, r.currency)}`;
        // Detalle al pasar el mouse: venta neta (listado, buy order o promedio) vs costo en gemas.
        const label =
          state.mode === "quick" ? "Buy order más alto" : state.mode === "avg" ? "Promedio reciente" : "Booster";
        line.title =
          `${label}: ${fmt(r.boosterPrice, r.currency)} (neto ${fmt(r.boosterNet, r.currency)})\n` +
          `Costo en gemas: ${fmt(r.gemCostValue, r.currency)} (${r.gemCost} gemas)`;
      } else {
        val.className = "scp-bp-muted";
        val.textContent = r.status;
      }

      // Lado derecho: valor + botón que abre el market del booster en otra pestaña.
      const right = document.createElement("span");
      right.className = "scp-bp-right";
      right.appendChild(val);

      const market = document.createElement("a");
      market.className = "scp-bp-market";
      market.textContent = "🛒";
      market.title = "Ver en el mercado de Steam";
      market.href = marketUrl(r.appid, r.name);
      market.target = "_blank";
      market.rel = "noopener noreferrer";
      // No propagar el click al ítem (evita seleccionar el juego al abrir el market).
      market.addEventListener("click", (e) => e.stopPropagation());
      right.appendChild(market);

      line.appendChild(name);
      line.appendChild(right);
      // Click en el ítem: seleccionar ese juego en el selector de la página.
      line.addEventListener("click", () => selectGameInPage(r.appid));
      $list.appendChild(line);
    }
  }

  // Selecciona el juego en el <select> nativo del booster creator (si existe).
  function selectGameInPage(appid) {
    const sel = document.querySelector("#booster_game_selector");
    if (!sel) return;
    sel.value = appid;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    sel.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // --- Consultas al service worker ---

  function querySack() {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "GET_SACK" }, (r) => {
        if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
        else resolve(r);
      });
    });
  }

  // Modo "avg": primero calcula el promedio reciente EN EL NAVEGADOR (pricehistory,
  // requiere la sesión del usuario) y recién ahí le pide al backend el costo/profit
  // con ese valor. ``cached``/``noSession`` reflejan la parte de Steam (pricehistory);
  // el backend no cachea esta respuesta porque no le pega a Steam por el booster.
  async function queryBoosterAvg(appid, gemCost, name) {
    const hash = `${appid}-${name} Booster Pack`;
    const hist = await getRecentAvg(hash, AVG_SAMPLE_DAYS);
    const resp = await new Promise((resolve) => {
      const msg = {
        type: "GET_BOOSTER_AVG",
        appid,
        gemCost,
        name,
        avgPrice: hist.avgPrice,
        sampleDays: AVG_SAMPLE_DAYS,
        sampleVolume: hist.volume,
      };
      chrome.runtime.sendMessage(msg, (r) => {
        if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
        else resolve(r);
      });
    });
    return { ...resp, cached: hist.cached, noSession: Boolean(hist.noSession) };
  }

  function queryBooster(mode, appid, gemCost, name) {
    if (mode === "avg") return queryBoosterAvg(appid, gemCost, name);
    // "sell" -> precio listado (GET_BOOSTER); "quick" -> buy order (GET_BOOSTER_QUICK).
    const type = mode === "quick" ? "GET_BOOSTER_QUICK" : "GET_BOOSTER";
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ type, appid, gemCost, name }, (r) => {
        if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
        else resolve(r);
      });
    });
  }

  // Arma la entry de un resultado a partir de la respuesta del backend.
  // ``mode`` decide de qué campos leer el precio (listado, buy order o promedio); la
  // entry usa nombres genéricos (boosterPrice/boosterNet) para compartir el render.
  function handleResult(mode, game, resp) {
    const entry = {
      appid: game.appid,
      name: game.name,
      gemCost: game.gems,
      profit: null,
      profitPositive: false,
      boosterPrice: null,
      boosterNet: null,
      gemCostValue: null,
      currency: null,
      status: "error",
    };

    if (resp && resp.ok) {
      const d = resp.data;
      entry.currency = d.currency;
      if (mode === "quick") {
        entry.boosterPrice = d.buy_order_price;
        entry.boosterNet = d.buy_order_net;
      } else if (mode === "avg") {
        entry.boosterPrice = d.avg_sale_price;
        entry.boosterNet = d.avg_sale_net;
      } else {
        entry.boosterPrice = d.booster_price;
        entry.boosterNet = d.booster_net_price;
      }
      entry.gemCostValue = d.gem_cost_value;
      if (d.profit == null) {
        // Sin precio listado (venta), sin buy orders (rápida) o sin ventas
        // recientes / sin sesión de Steam (promedio).
        if (mode === "quick") entry.status = "sin buy orders";
        else if (mode === "avg") entry.status = resp.noSession ? "sin sesión" : "sin ventas recientes";
        else entry.status = "sin precio";
      } else {
        entry.profit = d.profit;
        entry.profitPositive = d.profit_positive;
        entry.status = "ok";
      }
    }

    state.results[mode].push(entry);
    return entry;
  }

  // --- Escaneo ---

  async function scanAll() {
    const games = parseGames();
    if (games.length === 0) {
      $progress.textContent = "No se encontraron juegos elegibles en la página.";
      return;
    }
    state.games = games;

    // El escaneo corre en el modo activo al arrancar (las pestañas quedan
    // bloqueadas mientras corre) y solo pisa los resultados de ESE modo.
    const mode = state.mode;
    state.running = true;
    state.stop = false;
    state.results[mode] = [];
    $startBtn.textContent = "Detener";

    // Precio del saco (referencia para valuar las gemas). Si no se cargó al abrir
    // (o falló), reintentar acá.
    if (!state.sack) await loadSack();

    const { scanDelayMs } = await chrome.storage.local.get("scanDelayMs");
    const delay = Number.isFinite(scanDelayMs) && scanDelayMs >= 0 ? scanDelayMs : DEFAULT_DELAY_MS;

    let done = 0;
    let withProfit = 0;
    let reused = 0;
    let backoff = 0;

    for (const game of games) {
      if (state.stop) break;

      const resp = await queryBooster(mode, game.appid, game.gems, game.name);
      const entry = handleResult(mode, game, resp);
      // ``cached`` = el resultado salió de la caché reciente (no pegó a Steam).
      const fromCache = Boolean(resp && resp.cached);
      if (fromCache) reused++;

      if (entry.profitPositive) withProfit++;
      if (resp && resp.ok) {
        backoff = 0; // resultado válido: sin penalización
      } else {
        // Error de backend/red: posible rate limit -> backoff creciente.
        backoff = Math.min(backoff ? backoff * 2 : 2000, MAX_BACKOFF_MS);
      }

      done++;
      renderProgress(done, games.length, withProfit, reused);
      renderList();

      if (state.stop) break;
      // Solo pausar cuando se consultó de verdad: los ya escaneados (caché) no
      // re-consultan ni gastan el delay, salvo que el resultado previo fuese error.
      if (!fromCache) await sleep(delay + backoff);
    }

    state.running = false;
    state.stop = false;
    $startBtn.textContent = "Escanear boosters";
    $progress.textContent =
      `Listo: ${done}/${games.length} · ${withProfit} con profit · ${reused} reutilizados.`;
  }

  buildPanel();
})();
