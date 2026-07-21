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

  const state = {
    running: false,
    stop: false,
    onlyProfit: false,
    // Modo de valuación: "sell" = precio de venta listado (hay que esperar comprador);
    // "quick" = pedido de compra más alto (venta instantánea contra buy orders).
    mode: "sell",
    sack: null,             // { price, price_per_gem, gems, currency }
    sackError: null,        // motivo si falló la carga del saco (para mostrarlo)
    games: [],              // [{ appid, name, gems }]
    // Resultados separados por modo: cada apartado mantiene su propia lista.
    results: { sell: [], quick: [] },
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

  // URL del market de Steam para el booster pack de un juego. El market_hash_name
  // del booster es "{appid}-{nombre} Booster Pack" bajo el appid 753 (cromos).
  function marketUrl(appid, name) {
    const hash = `${appid}-${name} Booster Pack`;
    return `https://steamcommunity.com/market/listings/753/${encodeURIComponent(hash)}`;
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

  let $progress, $list, $startBtn, $onlyProfit, $sack, $note, $tabs;

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
        <div id="scp-bp-sack" class="scp-bp-sack">Saco de gemas: —</div>
        <div class="scp-bp-tabs">
          <button class="scp-bp-tab scp-bp-tab-active" data-mode="sell"
            title="Contra el precio de venta listado (hay que esperar comprador)">Venta listada</button>
          <button class="scp-bp-tab" data-mode="quick"
            title="Contra el pedido de compra más alto (venta instantánea garantizada)">⚡ Venta rápida</button>
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
    $sack.textContent = "Saco de gemas: cargando…";
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
      $sack.textContent =
        `Saco de gemas (1000): ${fmt(state.sack.price, state.sack.currency)} ` +
        `· ${fmt(state.sack.price_per_gem, state.sack.currency)}/gema`;
      $sack.classList.remove("scp-bp-sack-err");
    } else {
      $sack.textContent = `Saco de gemas: ${state.sackError || "no disponible"}`;
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
        // Detalle al pasar el mouse: venta neta (listado o buy order) vs costo en gemas.
        const label = state.mode === "quick" ? "Buy order más alto" : "Booster";
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

  function queryBooster(mode, appid, gemCost, name) {
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
  // ``mode`` decide de qué campos leer el precio (listado vs buy order); la entry
  // usa nombres genéricos (boosterPrice/boosterNet) para compartir el render.
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
      entry.boosterPrice = mode === "quick" ? d.buy_order_price : d.booster_price;
      entry.boosterNet = mode === "quick" ? d.buy_order_net : d.booster_net_price;
      entry.gemCostValue = d.gem_cost_value;
      if (d.profit == null) {
        // Sin precio listado (modo venta) o sin buy orders (modo rápido).
        entry.status = mode === "quick" ? "sin buy orders" : "sin precio";
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
