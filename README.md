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

## Estructura

```
backend/      # API FastAPI (proxy + caché + throttle hacia Steam)
extension/    # Extensión Chrome MV3 (content script + service worker + popup)
docs/         # Documentación de arquitectura
```

Documentación detallada de setup en este README (más abajo) y de arquitectura en
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
