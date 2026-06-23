# Arquitectura — Steam Card Profit Analyzer

## Visión general

```
┌─────────────────────────┐        ┌──────────────────────────────┐        ┌─────────────────────┐
│  Extensión Chrome (MV3)  │        │      Backend (FastAPI)       │        │       Steam         │
│                          │        │                              │        │                     │
│  content.js ─┐           │ HTTP   │  /api/profit/{appid}         │ HTTP   │  store appdetails   │
│              ├─► SW ─────┼───────►│   ├─ caché (aiocache, TTL)   ├───────►│  market search      │
│  popup.js ───┘           │        │   ├─ throttle (por host)     │        │  market priceovervw │
│  chrome.storage (TTL 1h) │        │   └─ parser + cálculo        │        │                     │
└─────────────────────────┘        └──────────────────────────────┘        └─────────────────────┘
```

La extensión nunca habla con Steam directamente. Centralizar todo en el backend
permite:

- **Caché compartida** entre todos los usuarios (menos requests a Steam).
- **Rate limiting** global de `priceoverview`, el endpoint con el límite más
  agresivo (~20 req/min).
- **CORS controlado**: la extensión solo confía en el backend.

---

## Backend

### Módulos

| Módulo | Responsabilidad |
|--------|-----------------|
| `app/main.py` | App FastAPI, CORS, montaje de routers, ciclo de vida del cliente httpx |
| `app/config.py` | `Settings` (pydantic-settings): moneda, TTLs, throttle, base URLs |
| `app/models.py` | Schemas pydantic v2 (`CardPrice`, `ProfitResponse`) |
| `app/cache.py` | Wrapper de aiocache (`get_or_set` read-through con TTL) |
| `app/throttle.py` | `AsyncThrottle` (semáforo + intervalo mínimo) **por host** de Steam |
| `app/steam/client.py` | Cliente httpx async compartido; reintentos + backoff ante 429/5xx |
| `app/steam/store.py` | `appdetails` → nombre, precio y `type` del juego (para descartar DLCs) |
| `app/steam/market.py` | `search/render` → lista de cromos; `priceoverview` → precio por cromo |
| `app/steam/parser.py` | Parseo de precios localizados; aplicación del fee |
| `app/routers/profit.py` | `GET /api/profit/{appid}`: orquestación + `compute_profit` (pura) |
| `app/routers/booster.py` | `GET /api/gems/sack` y `GET /api/booster/{appid}`: valor de booster packs (gemas vs market) |

### Endpoints de Steam usados

Todos son endpoints **no documentados pero estables**. Se cachea agresivamente y se
respeta el rate limit.

**1. Precio del juego — Storefront API**

```
GET https://store.steampowered.com/api/appdetails?appids={appid}&cc=ar&l=spanish
```
`data[appid].data.price_overview.final` viene en centavos. Si no existe
`price_overview`, el juego es gratis/sin precio → `422`. Si `data[appid].data.type`
es `"dlc"`, se devuelve `422` sin pedir cromos (un DLC no dropea cromos propios).

**2. Lista de cromos — Market search (modo JSON)**

```
GET https://steamcommunity.com/market/search/render/
      ?appid=753&norender=1&count=100
      &category_753_Game[]=tag_app_{appid}
      &category_753_item_class[]=tag_item_class_2
      &category_753_cardborder[]=tag_cardborder_0
```
- `norender=1` es obligatorio para recibir JSON.
- `item_class_2` = trading cards; `cardborder_0` = normales (las que dropean),
  `cardborder_1` = foils. El profit usa solo las normales; las foils se consultan
  aparte cuando se pide `include_foils=true` (`fetch_card_list(appid, foil=True)`).
- El `market_hash_name` viene como `{appid}-{nombre}` (ej: `292030-Triss`).

**3. Precio de cada cromo — priceoverview**

```
GET https://steamcommunity.com/market/priceoverview/
      ?appid=753&currency={currency}&market_hash_name={appid}-{nombre}
```
- Todos los cromos viven bajo `appid=753`.
- Respuesta: `{ success, lowest_price, median_price, volume }`.
- **Endpoint con el rate limit más agresivo → throttle + caché obligatorios.**

### Caché (TTLs por defecto)

| Dato | TTL | Motivo |
|------|-----|--------|
| Precio de cromos | 6h | Cambian lento; es el dato más caro de obtener |
| Precio del juego | 1h | Puede tener ofertas |
| Lista de cromos | 24h | Prácticamente estática por juego |

`get_or_set(key, factory, ttl)` implementa read-through: devuelve el valor cacheado o
ejecuta la corutina, guarda y devuelve. En v1 la caché es **en memoria del proceso**
(sin base de datos).

### Rate limiting y resiliencia

- **Throttle por host**: `client.get_json` aplica un `AsyncThrottle` (semáforo +
  intervalo mínimo) **por cada host de Steam**, así *toda* request —no solo
  `priceoverview`— queda espaciada y no se generan ráfagas que disparan 429. Los
  intervalos por defecto se eligen **bien por debajo** del máximo de Steam (el límite es
  una ventana deslizante: sostenerlo cerca del tope igual lo dispara y bloquea la IP por
  minutos, así que conviene prevenir, sobre todo en escaneos largos de cientos de ítems):
  `steamcommunity.com` (priceoverview/search) ~1 req/5s (≈12/min, máx ~20);
  `store.steampowered.com` (appdetails) ~1 req/2s (≈150/5min, máx ~200). Cada host se
  limita por separado (tienen rate limits propios).
- **Cooldown adaptativo ante 429**: cuando Steam responde 429, el throttle del host
  se *penaliza* (`penalize`): entra en cooldown por `Retry-After` (o `cooldown_429`, 60s
  por defecto) y **sube su intervalo** (× `_BUMP`, con tope); con cada éxito (`relax`)
  el intervalo **decae** hacia el base. Esto replica el cooldown de Steam y absorbe los
  picos sin frenar el régimen normal.
- `client.get_json` reintenta ante **429**, **5xx** y errores de red, con **backoff
  exponencial** topeado (`min(backoff_base ** intento, backoff_max)`), hasta `max_retries`.
- `User-Agent` explícito en todas las requests.

### Parser robusto

`parse_price` decide el separador decimal como el **último** `,` o `.` y descarta el
resto (miles):

| Entrada | Salida |
|---------|--------|
| `"$0.18"` | `0.18` |
| `"1,23€"` | `1.23` |
| `"$1,234.56"` | `1234.56` |
| `"1.234,56 €"` | `1234.56` |
| `None` / `"N/A"` | `None` |

Si un cromo devuelve `success: false` o sin precio, se marca `success=False` y se
**excluye del promedio** (no aborta el cálculo).

---

## Modelo de cálculo del profit

1. Precio del juego (`final` en centavos → unidades).
2. Lista de cromos normales → `total_cards`.
3. Precio (`lowest_price`) de cada cromo, vía throttle + caché.
4. Cromos que dropean (aprox. estándar): `cards_dropped = round(total_cards * drop_ratio)`
   con `drop_ratio = 0.5` (configurable).
5. Como qué cromos dropean es aleatorio, se usa **valor esperado**:

```
precio_promedio_cromo = sum(lowest_price de los cromos con precio) / nº con precio
valor_bruto_drop      = precio_promedio_cromo * cards_dropped
valor_neto_drop       = valor_bruto_drop / (1 + fee_rate)      # fee_rate = 0.15
profit                = valor_neto_drop - precio_juego
```

`compute_profit(...)` es una **función pura** (sin red), lo que permite testearla con
datos mockeados (`tests/test_profit.py`).

---

## Modelo de valor de booster packs

Un **booster pack** se crea gastando gemas (costo fijo por juego, ej. 400) y luego se
puede vender en el market. El valor se calcula así:

```
precio_gema   = precio_saco / 1000          # Saco de Gemas = ítem 753 del market
costo_gemas   = (gem_cost / 1000) * precio_saco
venta_neta    = precio_booster / (1 + fee_rate)   # booster pack = ítem 753 del market
profit        = venta_neta - costo_gemas
```

- El `gem_cost` y el `name` de cada juego los lee la extensión de la página del booster
  creator (`CBoosterCreatorPage.Init([...])`), no la API de Steam.
- El `market_hash_name` del booster se arma como `"{appid}-{nombre} Booster Pack"`.
- Tanto el Saco de Gemas como el booster usan `priceoverview` (mismo throttle/caché que
  los cromos): el escaneo de boosters solo pega a `steamcommunity.com`, una consulta por
  juego (el precio del saco se cachea y se pide una sola vez).

---

## Extensión (Chrome MV3)

| Componente | Rol |
|------------|-----|
| `content/content.js` | Página de juego: detecta el `appid` en `…/app/{appid}/…`, pide profit al SW e inyecta el overlay |
| `content/overlay.css` | Estilos del overlay (fijo abajo a la derecha) |
| `content/search.js` | Página de búsqueda (`/search…`): panel que escanea los resultados (cargando más por scroll), anota cada fila con un badge de profit y oculta los DLC |
| `content/search.css` | Estilos del panel del escáner y de los badges por fila |
| `content/booster.js` | Booster creator (`/tradingcards/boostercreator`): lee los juegos elegibles de la página y escanea el valor de cada booster (gemas vs market) |
| `content/booster.css` | Estilos del panel del escáner de boosters |
| `background/service-worker.js` | Llama a `/api/profit/{appid}`, `/api/booster/{appid}` y `/api/gems/sack`; cachea en `chrome.storage.local` (TTL 1h). Acepta override de foils (el escaneo pide siempre sin foils) |
| `popup/*` | Configura URL del backend, toggle de foils, delay de escaneo y limpieza de caché local |

**Flujo (página de juego):** `content.js` extrae el appid →
`chrome.runtime.sendMessage({GET_PROFIT})` → el service worker mira la caché
(`chrome.storage.local`), si está vencida hace `fetch` al backend, cachea y responde →
`content.js` renderiza el overlay con el desglose.

**Flujo (escáner de búsqueda):** `search.js` junta las filas con `data-ds-appid`
cargadas en el DOM y las procesa **secuencialmente** (una consulta a la vez, sin
disparar la siguiente hasta recibir la respuesta) con un **delay configurable** entre
juegos y **backoff** ante errores del backend. Cuando agota las filas visibles, hace
**scroll al fondo** para que Steam cargue más resultados (scroll infinito) y continúa,
hasta el final o hasta que el usuario detenga. Los **DLC** se detectan (el backend
responde `422` por `type=dlc`) y se ocultan. Esto, sumado al caché + throttle del
backend, mantiene el ritmo dentro del rate limit de Steam (el cuello de botella real
es `priceoverview`, ~20 req/min). Los juegos ya cacheados se resuelven al instante.

La caché del cliente (`chrome.storage.local`, TTL 1h) evita repegarle al backend al
revisitar la misma página. Guarda los resultados **definitivos** (con precio, "sin
cromos", F2P/DLC, "sin precio" de booster) pero **no** los errores transitorios
(429/5xx/red): así, al re-escanear, los ya resueltos se reutilizan al instante (sin
delay ni request a Steam) y solo se reintenta lo que falló. El service worker marca
cada respuesta con `cached: true/false` para que el escáner sepa si debe esperar.

---

## Decisiones de diseño

- **Sin base de datos en v1**: caché en memoria del proceso. Si se necesita persistir
  o compartir caché entre instancias, migrar a Redis (aiocache lo soporta cambiando el
  backend de `Cache`).
- **`compute_profit` pura**: separa la lógica de negocio de la I/O para testear sin red.
- **Currency configurable, default USD (1)**: USD es el más líquido/estable en el
  market de cromos.
- **Foils como cálculo aparte**: el profit usa solo cromos normales (las foils son
  raras y distorsionan el valor esperado del drop). Con `include_foils=true` se
  reporta su valor de mercado por separado (`FoilSummary`), sin mezclarlo en el profit.

## Próximos pasos sugeridos

- ✅ Tests de integración del router con `httpx.MockTransport` (sin pegarle a Steam) —
  ver `backend/tests/test_router_integration.py`.
- ✅ Soporte de foils como cálculo aparte (`include_foils=true`) — `FoilSummary` en
  `app/models.py`, `compute_foil_summary` en `app/routers/profit.py`, toggle en el popup.
- Caché persistente (Redis) para despliegue multi-instancia — **fuera de alcance**:
  el uso es local de instancia única, la caché en memoria alcanza.
- Publicación en Chrome Web Store — **fuera de alcance**: la extensión se usa
  localmente (cargar descomprimida).
