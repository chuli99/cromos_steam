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
    sack: null,             // { price, price_per_gem, gems, currency }
    games: [],              // [{ appid, name, gems }]
    results: [],            // [{ appid, name, gemCost, profit, profitPositive, ... , status }]
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

  let $progress, $list, $startBtn, $onlyProfit, $sack;

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
        <div class="scp-bp-controls">
          <button id="scp-bp-start">Escanear boosters</button>
        </div>
        <label class="scp-bp-check">
          <input type="checkbox" id="scp-bp-onlyprofit" />
          Mostrar solo con profit
        </label>
        <div id="scp-bp-progress" class="scp-bp-progress">Listo para escanear.</div>
        <div id="scp-bp-list" class="scp-bp-list"></div>
        <div class="scp-bp-note">
          Compara el costo en gemas de cada booster (según el precio del Saco de Gemas)
          contra su precio de venta neto en el market. Escaneo secuencial y respetuoso
          del rate limit de Steam: puede tardar si hay muchos juegos. Click en un ítem
          para seleccionarlo en la página.
        </div>
      </div>`;
    document.body.appendChild(panel);

    $progress = panel.querySelector("#scp-bp-progress");
    $list = panel.querySelector("#scp-bp-list");
    $startBtn = panel.querySelector("#scp-bp-start");
    $onlyProfit = panel.querySelector("#scp-bp-onlyprofit");
    $sack = panel.querySelector("#scp-bp-sack");

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
  }

  function renderSack() {
    if (state.sack) {
      $sack.textContent =
        `Saco de gemas (1000): ${fmt(state.sack.price, state.sack.currency)} ` +
        `· ${fmt(state.sack.price_per_gem, state.sack.currency)}/gema`;
      $sack.classList.remove("scp-bp-sack-err");
    } else {
      $sack.textContent = "Saco de gemas: no disponible";
      $sack.classList.add("scp-bp-sack-err");
    }
  }

  function renderProgress(done, total, withProfit, reused) {
    $progress.textContent =
      `${done}/${total} escaneados · ${withProfit} con profit · ${reused} en caché`;
  }

  function renderList() {
    const items = state.results
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
        // Detalle al pasar el mouse: venta neta del booster vs costo en gemas.
        line.title =
          `Booster: ${fmt(r.boosterPrice, r.currency)} (neto ${fmt(r.boosterNet, r.currency)})\n` +
          `Costo en gemas: ${fmt(r.gemCostValue, r.currency)} (${r.gemCost} gemas)`;
      } else {
        val.className = "scp-bp-muted";
        val.textContent = r.status;
      }

      line.appendChild(name);
      line.appendChild(val);
      // Click: seleccionar ese juego en el selector de la página para poder crearlo.
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

  function queryBooster(appid, gemCost, name) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "GET_BOOSTER", appid, gemCost, name }, (r) => {
        if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
        else resolve(r);
      });
    });
  }

  // Arma la entry de un resultado a partir de la respuesta del backend.
  function handleResult(game, resp) {
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
      entry.boosterPrice = d.booster_price;
      entry.boosterNet = d.booster_net_price;
      entry.gemCostValue = d.gem_cost_value;
      if (d.profit == null) {
        // El booster no tiene precio de mercado: no se puede valuar.
        entry.status = "sin precio";
      } else {
        entry.profit = d.profit;
        entry.profitPositive = d.profit_positive;
        entry.status = "ok";
      }
    }

    state.results.push(entry);
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

    state.running = true;
    state.stop = false;
    state.results = [];
    $startBtn.textContent = "Detener";

    // Precio del saco (referencia para valuar las gemas). Una sola vez.
    const sackResp = await querySack();
    state.sack = sackResp && sackResp.ok ? sackResp.data : null;
    renderSack();

    const { scanDelayMs } = await chrome.storage.local.get("scanDelayMs");
    const delay = Number.isFinite(scanDelayMs) && scanDelayMs >= 0 ? scanDelayMs : DEFAULT_DELAY_MS;

    let done = 0;
    let withProfit = 0;
    let reused = 0;
    let backoff = 0;

    for (const game of games) {
      if (state.stop) break;

      const resp = await queryBooster(game.appid, game.gems, game.name);
      const entry = handleResult(game, resp);
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
