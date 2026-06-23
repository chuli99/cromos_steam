# Steam Card Profit Analyzer

Extensión de Chrome (Manifest V3) + backend FastAPI que, en la página de un juego
de la tienda de Steam, calcula si hay "profit" comparando el **precio del juego**
contra el **valor de venta estimado de los cromos (trading cards)** que ese juego
dropea.

> ⚠️ La herramienta es para **analizar y comparar**, no promete ganancia. Steam se
> queda con ~15% en cada venta del market y los cromos dropean solo la mitad del set,
> por lo que el profit real casi nunca es positivo. La UI muestra el número crudo
> (positivo o negativo) sin prometer nada.

La extensión **no** habla directamente con Steam: todo pasa por el backend, que actúa
como **proxy + caché + rate limiter** para no chocar contra los límites de Steam y
centralizar el caché entre usuarios.

```
Extensión (Chrome MV3)  ──►  Backend (FastAPI)  ──►  Steam (store + community market)
   content + popup            proxy + caché + throttle
```

Ver [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) para el detalle de arquitectura,
endpoints de Steam y modelo de cálculo.

## Estructura

```
backend/      # API FastAPI (proxy + caché + throttle hacia Steam)
extension/    # Extensión Chrome MV3 (content script + service worker + popup)
docs/         # Documentación de arquitectura
```

---

## Backend

### Requisitos

- Python 3.11+ (probado con 3.14)

### Setup

```bash
cd backend

# 1) Entorno virtual
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# 2) Dependencias
pip install -r requirements.txt

# 3) (Opcional) Configuración
cp .env.example .env   # y ajustá lo que quieras; todo tiene default razonable

# 4) Levantar el servidor
uvicorn app.main:app --reload
```

El backend queda en `http://localhost:8000`. Docs interactivas en
`http://localhost:8000/docs`.

### Endpoint principal

```
GET /api/profit/{appid}
```

Devuelve el desglose completo (`ProfitResponse`):

```jsonc
{
  "appid": 292030,
  "game_name": "The Witcher 3: Wild Hunt",
  "game_price": 2.99,
  "currency": 1,
  "total_cards": 6,
  "cards_dropped": 3,
  "avg_card_price": 0.1567,
  "gross_card_value": 0.47,
  "fee_rate": 0.15,
  "net_card_value": 0.4087,
  "profit": -2.5813,
  "profit_positive": false,
  "cards": [
    { "name": "Triss", "lowest_price": 0.16, "median_price": 0.16, "volume": 503, "success": true }
    // …
  ]
}
```

Respuestas de error esperadas:

| Código | Caso |
|--------|------|
| `404`  | appid inexistente, o juego sin cromos |
| `422`  | juego gratuito / sin precio (no aplica cálculo de profit) |

Ejemplo:

```bash
curl http://localhost:8000/api/profit/292030
```

#### Foils (opcional)

Con el parámetro `include_foils=true` se agrega un resumen del valor de mercado de
las **foils**, como cálculo aparte (no entran en el profit de los cromos normales,
porque son raras y distorsionarían el valor esperado del drop):

```bash
curl "http://localhost:8000/api/profit/292030?include_foils=true"
```

Agrega al `ProfitResponse` el bloque `foils` (o `null` si no se pidió):

```jsonc
{
  // …
  "foils": {
    "total_foils": 6,
    "avg_foil_price": 3.2,
    "net_avg_foil_price": 2.78,   // promedio tras descontar el fee de Steam
    "foils": [ { "name": "Triss (Foil)", "lowest_price": 2.5, "success": true } ]
  }
}
```

#### Booster packs (gemas)

Dos endpoints para valuar la creación de **booster packs** con gemas, comparándola
con su precio de venta en el market:

```
GET /api/gems/sack             # precio de referencia del Saco de Gemas (1000 gemas)
GET /api/booster/{appid}?gem_cost=400&name=Dota%202
```

`/api/booster/{appid}` compara el **costo en gemas** del booster (`gem_cost`, valuado
con el precio del Saco de Gemas) contra el **precio de venta** del booster pack (neto
del fee). Devuelve un `BoosterValue`:

```jsonc
{
  "appid": 570,
  "name": "Dota 2",
  "gem_cost": 400,
  "gem_price_per_1000": 1.0,    // precio del saco
  "gem_cost_value": 0.4,        // (400/1000) * precio_saco
  "booster_price": 1.0,         // precio de venta del booster pack
  "booster_net_price": 0.8696,  // tras descontar el fee de Steam
  "profit": 0.4696,             // booster_net_price - gem_cost_value
  "profit_positive": true
}
```

> El `gem_cost` y el `name` los provee la extensión leyéndolos de la página del booster
> creator. Si el booster no se vende en el market, `booster_price`/`profit` quedan en `null`.

### Configuración

Todo se configura por variables de entorno con prefijo `SCP_` o un archivo `.env`
(ver [`backend/.env.example`](backend/.env.example)). Lo más relevante:

| Variable | Default | Descripción |
|----------|---------|-------------|
| `SCP_CURRENCY` | `1` (USD) | Moneda del market para los cromos |
| `SCP_COUNTRY_CODE` | `ar` | País para el precio del juego (appdetails) |
| `SCP_CACHE_TTL_CARDS` | `21600` (6h) | TTL del precio de cromos |
| `SCP_CACHE_TTL_GAME` | `3600` (1h) | TTL del precio del juego |
| `SCP_CACHE_TTL_CARD_LIST` | `86400` (24h) | TTL de la lista de cromos |
| `SCP_COMMUNITY_INTERVAL` | `5.0` | Segundos mínimos entre requests a `steamcommunity.com` (priceoverview/search; ~12 req/min). Subilo (ej. `6.0`) si seguís viendo 429 en escaneos largos |
| `SCP_STORE_INTERVAL` | `2.0` | Segundos mínimos entre requests a `store.steampowered.com` (appdetails; ~150 req/5min, bajo el máximo ~200) |
| `SCP_MAX_RETRIES` | `3` | Reintentos ante 429/5xx/red (pocos: una request no debe colgarse minutos) |
| `SCP_COOLDOWN_429` | `20.0` | Pausa del host ante un 429 sin `Retry-After` (cooldown adaptativo) |
| `SCP_FEE_RATE` | `0.15` | Fee de Steam |
| `SCP_DROP_RATIO` | `0.5` | Proporción del set que dropea |

> El **secreto/`.env` nunca se versiona**: está en `.gitignore`. Solo se versiona
> `.env.example`.

### Tests

```bash
cd backend
pytest          # parser + cálculo de profit + integración del router (sin red)
```

Los tests de integración (`tests/test_router_integration.py`) ejercitan el endpoint
completo (`router + caché + throttle + cliente httpx con reintentos + parser`) contra
un Steam falso vía `httpx.MockTransport`, sin tocar la red.

---

## Extensión (Chrome MV3)

### Cargar sin empaquetar

1. Abrí `chrome://extensions`.
2. Activá el **Modo de desarrollador** (arriba a la derecha).
3. Clic en **"Cargar descomprimida"** (*Load unpacked*) y elegí la carpeta
   [`extension/`](extension/).
4. Abrí la página de un juego: `https://store.steampowered.com/app/{appid}/...`
   (por ejemplo The Witcher 3). Aparecerá un overlay abajo a la derecha con el
   desglose de profit.

### Escanear el buscador

En la página de búsqueda de Steam (`store.steampowered.com/search…`, con los filtros
que quieras: orden por precio, sin free-to-play, "con cromos", etc.) aparece un panel
**🃏 Card Profit · escáner** abajo a la derecha:

1. Clic en **"Escanear resultados"**: la extensión recorre los juegos cargados en la
   página y consulta el profit de cada uno, anotando cada fila con un badge
   (verde = profit positivo, rojo = negativo, gris = sin cromos / free-to-play,
   violeta = DLC).
2. El panel lista los resultados ordenados por profit; clic en uno lleva la fila a la
   vista. El check **"Mostrar solo con profit"** filtra la lista.
3. **"Ocultar DLC"** (activado por defecto): los DLC no dropean cromos propios, así
   que se detectan y se **ocultan** de los resultados. Destildalo para volver a verlos.
4. Podés **detener** el escaneo en cualquier momento.

**Continúa solo más allá de lo visible.** Steam carga los resultados por scroll
infinito (típicamente ~100 al inicio). Cuando el escáner termina las filas visibles,
**baja automáticamente** para que Steam cargue más y sigue, hasta llegar al final o
hasta que lo detengas.

**Cuidado con el rate limit de Steam.** El escaneo es **secuencial** (no dispara la
siguiente consulta hasta que vuelve la anterior) y respeta un **delay configurable**
entre juegos (popup → *"Delay entre juegos al escanear (ms)"*, default 800 ms), con
**backoff** si el backend falla. Como el backend cachea, los juegos ya consultados se
resuelven al instante; los nuevos tardan porque el backend **throttlea por host**
(toda request a Steam respeta un intervalo mínimo, por debajo del máximo de Steam:
~5 s en `steamcommunity.com` —priceoverview/search, ~12 req/min— y ~2 s en
`store.steampowered.com` —appdetails—). Ante un **429**, el host entra en **cooldown
adaptativo**: se pausa (lo que diga `Retry-After`, o 60 s) y sube su intervalo, que
luego decae al normalizarse. Detectar un DLC cuesta solo una consulta de `appdetails`
(se corta antes de pedir precios de cromos).

**Reutiliza lo ya escaneado.** Los juegos con un resultado reciente (caché local ~1 h,
incluye "sin cromos" y F2P/DLC) se reutilizan y **no** se vuelven a consultar; solo se
reintenta lo que dio **error** (red/429/5xx, que no se cachea). Para forzar un
re-escaneo limpio, *"Limpiar caché local"* desde el popup.

### Escanear booster packs (gemas)

En la página del **booster creator**
(`steamcommunity.com/tradingcards/boostercreator`) aparece un panel **💎 Booster
Profit** abajo a la derecha:

1. Muestra el **precio del Saco de Gemas** (1000 gemas) como referencia.
2. Clic en **"Escanear boosters"**: lee todos los juegos elegibles de la página y,
   por cada uno, compara su **costo en gemas** (valuado con el saco) contra el
   **precio de venta** del booster pack (neto del fee). Lista los resultados ordenados
   por profit (verde = positivo); **"Mostrar solo con profit"** filtra la lista.
3. Click en un ítem para **seleccionar ese juego** en el selector nativo de la página.

El escaneo es **secuencial** y respeta el delay/throttle del backend (igual que el
escáner del buscador), así que con muchos juegos puede tardar. Podés **detenerlo** en
cualquier momento.

**Reutiliza lo ya escaneado.** Los juegos consultados recientemente (caché local
~1 h) se reutilizan al instante y **no** se vuelven a consultar a Steam; solo se
reintenta lo que dio **error** (ej. un 429). El panel muestra cuántos se reutilizaron.
Para forzar un re-escaneo limpio, usá *"Limpiar caché local"* en el popup.

### Configurar la URL del backend

Por defecto la extensión apunta a `http://localhost:8000`. Para cambiarlo:

1. Clic en el ícono de la extensión → se abre el **popup**.
2. Editá el campo **"URL del backend"** y clic en **Guardar**.

> Si usás un backend en otro host (no localhost), agregá ese host a
> `host_permissions` en `extension/manifest.json`. El backend ya habilita CORS para
> el origin `chrome-extension://*`.

Desde el popup también podés activar **"Incluir foils"**: el overlay agrega un bloque
con el valor de mercado de las foils (cálculo aparte). Al cambiar el toggle se limpia
la caché local para que la próxima visita vuelva a pedir el desglose.

Desde el popup también podés **limpiar la caché local** (respuestas guardadas en
`chrome.storage.local` con TTL de 1h).

---

## Convenciones del proyecto

- Comentarios del código **en español**.
- Commits con **Conventional Commits** (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`).
- No se hardcodean secrets: configuración por entorno / `config.py`.
