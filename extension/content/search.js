// Content script del buscador de Steam (store.steampowered.com/search…).
// Inyecta un panel para escanear los resultados visibles: por cada juego consulta
// el profit al backend y anota la fila con un badge.
//
// Estrategia anti-bloqueo de Steam:
//   - Escaneo SECUENCIAL real: no se dispara la siguiente petición hasta que vuelve
//     la anterior, así nunca se apilan requests (los juegos ya cacheados son instantáneos).
//   - Delay configurable entre juegos (popup) + backoff creciente si el backend falla
//     (posible rate limit de Steam).
//   - Arranque manual (botón) para no gastar el límite sin que el usuario lo decida.
//   - El backend ya cachea y throttlea priceoverview (1 req/3s), que es el endpoint
//     que Steam limita con más agresividad.

(function () {
  "use strict";

  // Solo en la página de búsqueda.
  if (!/\/search/.test(window.location.pathname)) return;

  const ROWS_SELECTOR = "#search_resultsRows a.search_result_row";
  const DEFAULT_DELAY_MS = 800;
  const MAX_BACKOFF_MS = 15000;

  const state = {
    running: false,
    stop: false,
    onlyProfit: false,
    hideDlc: true,
    results: [],            // { appid, name, profit, profitPositive, status, currency, dlc }
    byAppid: new Map(),     // appid -> fila <a>
  };

  // --- Utilidades ---

  function fmt(value, currency) {
    const n = Number(value).toFixed(2);
    return currency === 1 || currency == null ? `$${n}` : `${n} (cur ${currency})`;
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  // Extrae el appid de una fila de resultado. Ignora bundles/packages sin appid y,
  // si hay varios (separados por coma), toma el primero.
  function rowAppid(row) {
    const raw = row.getAttribute("data-ds-appid");
    if (!raw) return null;
    const id = raw.split(",")[0].trim();
    return /^\d+$/.test(id) ? id : null;
  }

  // Junta las filas actualmente cargadas en el DOM, deduplicadas por appid.
  function collectRows() {
    const map = new Map();
    document.querySelectorAll(ROWS_SELECTOR).forEach((row) => {
      const appid = rowAppid(row);
      if (appid && !map.has(appid)) map.set(appid, row);
    });
    return map;
  }

  // --- Badges en cada fila ---

  function ensureBadge(row) {
    row.classList.add("scp-row");
    let badge = row.querySelector(".scp-badge");
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "scp-badge";
      row.appendChild(badge);
    }
    return badge;
  }

  function setBadge(row, text, kind) {
    const badge = ensureBadge(row);
    badge.textContent = text;
    badge.className = `scp-badge scp-badge-${kind}`;
  }

  // --- Panel ---

  let $progress, $list, $startBtn, $onlyProfit, $hideDlc;

  function buildPanel() {
    const panel = document.createElement("div");
    panel.id = "scp-search-panel";
    panel.innerHTML = `
      <div class="scp-sp-header">
        <span class="scp-sp-logo">🃏 Card Profit · escáner</span>
        <button class="scp-sp-close" title="Cerrar">×</button>
      </div>
      <div class="scp-sp-body">
        <div class="scp-sp-controls">
          <button id="scp-sp-start">Escanear resultados</button>
        </div>
        <label class="scp-sp-check">
          <input type="checkbox" id="scp-sp-onlyprofit" />
          Mostrar solo con profit
        </label>
        <label class="scp-sp-check">
          <input type="checkbox" id="scp-sp-hidedlc" checked />
          Ocultar DLC
        </label>
        <div id="scp-sp-progress" class="scp-sp-progress">Listo para escanear.</div>
        <div id="scp-sp-list" class="scp-sp-list"></div>
        <div class="scp-sp-note">
          Escaneo secuencial y respetuoso del rate limit de Steam: los juegos nuevos
          tardan (se consulta el precio de cada cromo), los ya consultados son instantáneos.
          Al terminar las filas visibles, baja solo para cargar más resultados.
        </div>
      </div>`;
    document.body.appendChild(panel);

    $progress = panel.querySelector("#scp-sp-progress");
    $list = panel.querySelector("#scp-sp-list");
    $startBtn = panel.querySelector("#scp-sp-start");
    $onlyProfit = panel.querySelector("#scp-sp-onlyprofit");
    $hideDlc = panel.querySelector("#scp-sp-hidedlc");

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

    $hideDlc.addEventListener("change", () => {
      state.hideDlc = $hideDlc.checked;
      // Aplica/quita la ocultación sobre las filas de DLC ya detectadas.
      for (const r of state.results) {
        if (!r.dlc) continue;
        const row = state.byAppid.get(r.appid);
        if (row) row.style.display = state.hideDlc ? "none" : "";
      }
      renderList();
    });

    panel.querySelector(".scp-sp-close").addEventListener("click", () => panel.remove());
  }

  function renderProgress(done, total, withProfit) {
    $progress.textContent = `${done}/${total} escaneados · ${withProfit} con profit`;
  }

  function renderList() {
    const items = state.results
      .filter((r) => !(state.hideDlc && r.dlc))
      .filter((r) => (state.onlyProfit ? r.profitPositive : true))
      .sort((a, b) => (b.profit ?? -Infinity) - (a.profit ?? -Infinity));

    $list.textContent = "";
    for (const r of items) {
      const line = document.createElement("div");
      line.className = "scp-sp-item";

      const name = document.createElement("span");
      name.className = "scp-sp-name";
      name.textContent = r.name || `App ${r.appid}`;

      const val = document.createElement("span");
      if (r.status === "ok") {
        val.className = r.profitPositive ? "scp-sp-pos" : "scp-sp-neg";
        val.textContent = `${r.profitPositive ? "+" : ""}${fmt(r.profit, r.currency)}`;
      } else {
        val.className = "scp-sp-muted";
        val.textContent = r.status;
      }

      line.appendChild(name);
      line.appendChild(val);
      // Al clickear, llevar la fila correspondiente a la vista y resaltarla.
      line.addEventListener("click", () => {
        const row = state.byAppid.get(r.appid);
        if (row) {
          row.scrollIntoView({ behavior: "smooth", block: "center" });
          row.classList.add("scp-row-flash");
          setTimeout(() => row.classList.remove("scp-row-flash"), 1500);
        }
      });
      $list.appendChild(line);
    }
  }

  // Traduce el error del backend a una etiqueta corta para el badge/lista.
  function statusLabel(resp) {
    if (resp && resp.status === 404) return "sin cromos";
    if (resp && resp.status === 422) return "F2P";
    return "error";
  }

  // ¿El error corresponde a un DLC? (mensaje controlado por el backend).
  function isDlc(resp) {
    return resp && resp.status === 422 && /dlc/i.test(resp.error || "");
  }

  // Consulta el profit de un appid al service worker (siempre sin foils: más ágil).
  function queryProfit(appid) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "GET_PROFIT", appid, includeFoils: false }, (r) => {
        if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
        else resolve(r);
      });
    });
  }

  // Steam usa scroll infinito: bajar al fondo dispara la carga de más resultados.
  // Devuelve true si aparecieron filas nuevas dentro del tiempo de espera.
  async function loadMoreRows(prevCount) {
    window.scrollTo(0, document.documentElement.scrollHeight);
    const deadline = Date.now() + 4000;
    while (Date.now() < deadline) {
      if (state.stop) return false;
      await sleep(300);
      if (document.querySelectorAll(ROWS_SELECTOR).length > prevCount) return true;
    }
    return false;
  }

  // Procesa una respuesta: arma la entry, pinta el badge y registra el resultado.
  function handleResult(appid, row, resp) {
    const entry = {
      appid, name: null, profit: null, profitPositive: false,
      status: "error", currency: null, dlc: false,
    };

    if (resp && resp.ok) {
      const d = resp.data;
      entry.name = d.game_name;
      entry.profit = d.profit;
      entry.profitPositive = d.profit_positive;
      entry.currency = d.currency;
      entry.status = "ok";
      setBadge(row, `${d.profit_positive ? "+" : ""}${fmt(d.profit, d.currency)}`, d.profit_positive ? "pos" : "neg");
    } else if (isDlc(resp)) {
      entry.status = "DLC";
      entry.dlc = true;
      setBadge(row, "DLC", "dlc");
      if (state.hideDlc) row.style.display = "none";
    } else if (resp && (resp.status === 404 || resp.status === 422)) {
      entry.status = statusLabel(resp);
      setBadge(row, entry.status, "skip");
    } else {
      entry.status = "error";
      setBadge(row, "error", "err");
    }

    state.results.push(entry);
    return entry;
  }

  // --- Escaneo ---

  async function scanAll() {
    if (collectRows().size === 0) {
      $progress.textContent = "No se encontraron resultados con appid en la página.";
      return;
    }

    state.running = true;
    state.stop = false;
    state.results = [];
    state.byAppid = new Map();
    $startBtn.textContent = "Detener";

    const { scanDelayMs } = await chrome.storage.local.get("scanDelayMs");
    const delay = Number.isFinite(scanDelayMs) && scanDelayMs >= 0 ? scanDelayMs : DEFAULT_DELAY_MS;

    const processed = new Set();
    let done = 0;
    let withProfit = 0;
    let backoff = 0;

    while (!state.stop) {
      // Filas actuales (incluye las nuevas que Steam haya cargado por scroll).
      const rows = collectRows();
      rows.forEach((row, appid) => {
        if (!state.byAppid.has(appid)) state.byAppid.set(appid, row);
      });
      const pending = [...rows].filter(([appid]) => !processed.has(appid));

      if (pending.length === 0) {
        // Se agotaron las filas conocidas: intentar cargar más (scroll infinito).
        $progress.textContent = `Cargando más resultados… (${done} escaneados)`;
        const grew = await loadMoreRows(rows.size);
        if (!grew) break; // no hay más resultados -> fin
        continue;
      }

      for (const [appid, row] of pending) {
        if (state.stop) break;
        processed.add(appid);
        setBadge(row, "…", "pending");

        const resp = await queryProfit(appid);
        const entry = handleResult(appid, row, resp);

        if (entry.status === "ok" && entry.profitPositive) withProfit++;
        if (resp && (resp.ok || resp.status === 404 || resp.status === 422)) {
          backoff = 0; // resultado válido o de negocio: sin penalización
        } else {
          // Error de backend/red: posible rate limit -> backoff creciente.
          backoff = Math.min(backoff ? backoff * 2 : 2000, MAX_BACKOFF_MS);
        }

        done++;
        renderProgress(done, state.byAppid.size, withProfit);
        renderList();

        if (state.stop) break;
        await sleep(delay + backoff); // pausa entre juegos (+ backoff si hubo error)
      }
    }

    state.running = false;
    state.stop = false;
    $startBtn.textContent = "Escanear resultados";
    $progress.textContent = `Listo: ${done} escaneados · ${withProfit} con profit.`;
  }

  buildPanel();
})();
