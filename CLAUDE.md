# CLAUDE.md

Contexto de proyecto para Claude Code. Léelo antes de generar o modificar código.

## Qué es este proyecto

Un asistente de "segunda mano" que vigila Wallapop y/o Vinted en busca de un
producto concreto (ej. una cámara específica) y avisa por Telegram **solo**
cuando aparece un anuncio que cumple:

**Estado actual: solo Vinted está activo.** El scraper de Wallapop existe en
el código pero su endpoint real todavía no funciona (ver "Estado del
proyecto" más abajo), así que `config.example.yaml` solo trae una búsqueda
de Vinted. La arquitectura sigue soportando ambas plataformas — cuando se
arregle Wallapop, basta con añadir una búsqueda con `platform: wallapop` a
`config.yaml`.

- precio <= `PRICE_THRESHOLD`
- valoración del vendedor >= `SELLER_RATING_THRESHOLD`
- el anuncio no se ha notificado ya antes (deduplicación)

No es un proyecto de scraping masivo ni de reventa automatizada. Es una
herramienta personal, de bajo volumen, para no tener que refrescar la app
constantemente.

## Arquitectura

```
scrapers/          -> un módulo por plataforma (wallapop.py, vinted.py)
                       cada uno expone search(query) -> list[Listing]
filters.py          -> aplica umbral de precio y rating sobre los Listing
storage.py          -> SQLite: tabla `notified_listings` para dedup + histórico
notifier.py         -> envío de mensajes a Telegram (Bot API)
config.py           -> carga de .env / config.yaml (búsquedas, umbrales, tokens)
main.py             -> orquesta: scrape -> filter -> dedup -> notify
```

Cada scraper debe devolver una lista de objetos `Listing` con, como mínimo:

```python
@dataclass
class Listing:
    id: str
    title: str
    price: float
    currency: str
    url: str
    seller_rating: float | None
    seller_review_count: int | None
    image_url: str | None
    platform: str  # "wallapop" | "vinted"
```

## Cómo funcionan los scrapers (importante)

- Wallapop y Vinted **no tienen API pública oficial**. Sus webs consumen
  endpoints JSON internos (visibles en devtools -> Network -> XHR/Fetch al
  buscar algo). Estos scrapers llaman a esos endpoints directamente con
  `httpx`, en vez de parsear HTML.
- Estos endpoints pueden cambiar sin aviso. Si un scraper deja de funcionar,
  lo primero es re-inspeccionar la petición real en el navegador y comparar
  con el código.
- Cabeceras: usar un `User-Agent` realista de navegador. No falsificar
  identidad de usuario ni saltarse ningún login/paywall; solo se consultan
  endpoints de búsqueda pública, equivalentes a los que usa cualquier
  visitante anónimo de la web.
- **Rate limiting es obligatorio**: mínimo 1 request cada pocos segundos
  dentro de una misma ejecución, y el ciclo completo (main.py) no debe
  correr más de una vez cada 5-10 minutos. No paralelizar peticiones a la
  misma plataforma de forma agresiva.
- Manejar errores 429/403 con backoff, no con reintentos inmediatos.
- Los tests unitarios deben mockear las respuestas HTTP; nunca hacer
  peticiones reales de Wallapop/Vinted en un test automatizado.

## Deduplicación

- Tabla SQLite `notified_listings(id TEXT PRIMARY KEY, platform TEXT,
  price REAL, notified_at TIMESTAMP)`.
- Antes de notificar, comprobar si `(platform, id)` ya existe.
- Insertar inmediatamente después de notificar con éxito (no antes, para no
  perder anuncios si falla el envío a Telegram).

## Notificaciones (Telegram)

- Bot creado vía @BotFather; token y `chat_id` en `.env`, nunca hardcodeados
  ni committeados.
- Mensaje mínimo: título, precio, plataforma, rating del vendedor, enlace
  directo al anuncio. Si hay imagen, usar `sendPhoto` con caption en vez de
  `sendMessage`.
- Un fallo en el envío no debe tumbar el ciclo entero: loguear y continuar
  con el resto de resultados.

## Configuración

Todo lo que sea específico del usuario (búsquedas, umbrales, credenciales)
va en `config.yaml` + `.env`, nunca hardcodeado en el código:

```yaml
searches:
  - platform: wallapop
    query: "fujifilm x100v"
    price_threshold: 900
    seller_rating_threshold: 4.5
  - platform: vinted
    query: "fujifilm x100v"
    price_threshold: 850
    seller_rating_threshold: 4.5
```

```
# .env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## Convenciones de código

- Python 3.11+, tipado con `dataclasses` / type hints en todo el código.
- `httpx` para HTTP (soporta async si en algún momento se paraleliza entre
  plataformas distintas, nunca dentro de la misma).
- Logging con el módulo estándar `logging`, no `print`.
- Un solo punto de entrada: `main.py`, pensado para ser invocado por cron o
  un systemd timer. No mantener un proceso persistente salvo que se pida
  explícitamente (ej. migrar a `APScheduler`).
- Tests con `pytest`, mockeando HTTP con `respx` o `responses`.

## Cosas que NO hacer

- No implementar bypass de captchas, rotación de proxies para evadir
  bloqueos, ni suplantación de sesiones de usuario logueado.
- No aumentar la frecuencia de polling por debajo de unos pocos minutos.
- No scrapear más plataformas o categorías de las que el usuario pida
  explícitamente.
- No guardar ni exponer datos personales de vendedores más allá de lo
  necesario para el filtro (rating, nº valoraciones).

## Estado del proyecto / próximos pasos

(Actualizar esta sección a medida que avance el proyecto)

- [x] Scraper Vinted funcional y **validado contra la API real**
      (`www.vinted.es/api/v2/catalog/items`, con cabecera `Referer` — sin
      ella devuelve 403). Probado en real: trae anuncios correctos.
- [ ] **Scraper Wallapop NO funcional todavía.** El endpoint viejo
      (`api/v3/search`) está deprecado (400 constante). Se localizó el
      endpoint real actual (`api/v3/search/section`, sacado de los bundles
      JS de es.wallapop.com) y la cabecera que exige su WAF (`X-DeviceOS`),
      pero con los parámetros documentados en el propio JS del frontend
      sigue devolviendo `400 {"status":400,"message":"","errors":[]}` sin
      más detalle — falta al menos un parámetro que no se ha podido
      identificar por análisis estático del JS. Un Chrome headless real (vía
      CDP) fue bloqueado con 403 antes de ejecutar nada, probablemente por
      fingerprinting anti-bot; no se intentó evadirlo (fuera de alcance).
      Ver la nota de estado al principio de `scrapers/wallapop.py`. Próximo
      paso: capturar la petición real desde un navegador con sesión humana
      normal (DevTools -> Network -> XHR, buscar algo en
      es.wallapop.com/app/search) y comparar contra lo que ya hay.
- [x] Filtro + dedup
- [x] Notificador Telegram
- [x] main.py orquestando todo
- [x] Cron / systemd timer configurado (`deploy/snipebot.service` +
      `deploy/snipebot.timer`) y Tarea Programada de Windows
      (`deploy/register-task.ps1`)
- [x] Tests (`pytest` + `respx`, 52 tests, verde sin red real)
- [ ] Crear `config.yaml` y `.env` reales a partir de los `.example` (no se
      commitean)
- [ ] Primer ciclo real de punta a punta contra las dos plataformas (Vinted
      ya validado; Wallapop pendiente de lo de arriba)
