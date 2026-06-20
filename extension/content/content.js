// Content script: detecta el appid en la URL de la store, pide el profit al
// service worker e inyecta un overlay con el desglose.

(function () {
  "use strict";

  // La URL de un juego es https://store.steampowered.com/app/{appid}/Nombre/
  const match = window.location.pathname.match(/\/app\/(\d+)/);
  if (!match) return;
  const appid = match[1];

  // Formatea un número como precio. currency 1 = USD; para otras monedas se
  // muestra el código junto al valor (v1 no mapea todos los símbolos).
  function fmt(value, currency) {
    const n = Number(value).toFixed(2);
    return currency === 1 ? `$${n}` : `${n} (cur ${currency})`;
  }

  // Crea (o reutiliza) el contenedor del overlay.
  function ensureOverlay() {
    let el = document.getElementById("scp-overlay");
    if (!el) {
      el = document.createElement("div");
      el.id = "scp-overlay";
      document.body.appendChild(el);
    }
    return el;
  }

  function renderLoading() {
    ensureOverlay().innerHTML = `
      <div class="scp-header">
        <span class="scp-logo">🃏 Card Profit</span>
        <button class="scp-close" title="Cerrar">×</button>
      </div>
      <div class="scp-body">Calculando profit…</div>`;
    wireClose();
  }

  function renderError(message) {
    ensureOverlay().innerHTML = `
      <div class="scp-header">
        <span class="scp-logo">🃏 Card Profit</span>
        <button class="scp-close" title="Cerrar">×</button>
      </div>
      <div class="scp-body scp-error">${message}</div>`;
    wireClose();
  }

  // Bloque opcional de foils (cálculo aparte). Solo si el backend lo incluyó.
  function foilsBlock(d) {
    const f = d.foils;
    if (!f) return "";
    if (!f.total_foils) {
      return `<div class="scp-foils scp-note">Sin foils para este juego.</div>`;
    }
    return `
      <div class="scp-foils">
        <div class="scp-subhead">✨ Foils (${f.total_foils})</div>
        <table class="scp-table">
          <tr><td>Precio promedio</td><td>${fmt(f.avg_foil_price, d.currency)}</td></tr>
          <tr><td>Promedio neto</td><td>${fmt(f.net_avg_foil_price, d.currency)}</td></tr>
        </table>
      </div>`;
  }

  function renderResult(d) {
    const profitClass = d.profit_positive ? "scp-pos" : "scp-neg";
    const profitSign = d.profit_positive ? "+" : "";
    ensureOverlay().innerHTML = `
      <div class="scp-header">
        <span class="scp-logo">🃏 Card Profit</span>
        <button class="scp-close" title="Cerrar">×</button>
      </div>
      <div class="scp-body">
        <div class="scp-game">${d.game_name}</div>
        <div class="scp-profit ${profitClass}">
          Profit: ${profitSign}${fmt(d.profit, d.currency)}
        </div>
        <table class="scp-table">
          <tr><td>Precio del juego</td><td>${fmt(d.game_price, d.currency)}</td></tr>
          <tr><td>Cromos totales</td><td>${d.total_cards}</td></tr>
          <tr><td>Cromos que dropean</td><td>${d.cards_dropped}</td></tr>
          <tr><td>Precio promedio</td><td>${fmt(d.avg_card_price, d.currency)}</td></tr>
          <tr><td>Valor bruto drop</td><td>${fmt(d.gross_card_value, d.currency)}</td></tr>
          <tr><td>Fee Steam</td><td>${(d.fee_rate * 100).toFixed(0)}%</td></tr>
          <tr><td>Valor neto drop</td><td>${fmt(d.net_card_value, d.currency)}</td></tr>
        </table>
        ${foilsBlock(d)}
        <div class="scp-note">Valor esperado, no garantizado. El profit real suele ser negativo.</div>
      </div>`;
    wireClose();
  }

  function wireClose() {
    const btn = document.querySelector("#scp-overlay .scp-close");
    if (btn) btn.addEventListener("click", () => {
      const el = document.getElementById("scp-overlay");
      if (el) el.remove();
    });
  }

  // Flujo principal: mostrar loading y pedir el profit al service worker.
  renderLoading();
  chrome.runtime.sendMessage({ type: "GET_PROFIT", appid }, (resp) => {
    if (chrome.runtime.lastError) {
      renderError(chrome.runtime.lastError.message);
      return;
    }
    if (!resp || !resp.ok) {
      renderError((resp && resp.error) || "Error desconocido");
      return;
    }
    renderResult(resp.data);
  });
})();
