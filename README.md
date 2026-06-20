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
| `SCP_THROTTLE_INTERVAL` | `3.0` | Segundos mínimos entre requests a priceoverview |
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
