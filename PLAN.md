# PLAN.md — SnipeBot de 0 a terminado

Plan de trabajo completo. Cada fase es entregable por sí sola y deja el
proyecto en un estado ejecutable. Marcar las casillas a medida que se cierran.

**Estado actual:** Fases 1-6 completas (código + 52 tests en verde) y Fase 7
completa salvo el commit final. Pendiente: validar los scrapers contra las
APIs reales de Wallapop/Vinted (ver checklist de `CLAUDE.md`).

---

## Fase 0 — Decisiones tomadas antes de escribir código

Se dejan escritas aquí para no re-discutirlas a mitad del proyecto.

| Decisión | Elección | Motivo |
|---|---|---|
| Layout | Módulos planos en la raíz + paquete `scrapers/` | Es lo que fija `CLAUDE.md`; el proyecto es pequeño |
| HTTP | `httpx` síncrono | Un ciclo cada 5-10 min no necesita async; menos superficie de error |
| Config | `config.yaml` (búsquedas/umbrales) + `.env` (secretos) | Separa lo versionable de lo que nunca se commitea |
| Dedup | SQLite con PK compuesta `(platform, id)` | Los IDs de Wallapop y Vinted pueden colisionar entre sí |
| Ejecución | Proceso de un solo tiro lanzado por cron/timer | `CLAUDE.md` prohíbe proceso persistente salvo petición explícita |
| Rating desconocido | Se descarta por defecto, configurable | Un anuncio sin rating no cumple el filtro; que el usuario decida |
| Tests | `pytest` + `respx`, cero red real | Requisito explícito de `CLAUDE.md` |

**Desviación consciente de `CLAUDE.md`:** el esquema documentado dice
`id TEXT PRIMARY KEY`, pero la comprobación descrita es por `(platform, id)`.
Se implementa PK compuesta, que es la lectura coherente de las dos frases.

---

## Fase 1 — Andamiaje del repositorio

- [x] `pyproject.toml` — deps (`httpx`, `PyYAML`, `python-dotenv`), extra `dev`
      (`pytest`, `respx`), config de pytest
- [x] `requirements.txt`
- [x] `.gitignore` — `.env`, `config.yaml`, `*.db`, `.venv/`, `__pycache__/`, locks
- [x] `.env.example` — plantilla con instrucciones para sacar el `chat_id`
- [x] `config.example.yaml` — ejemplo comentado con las dos búsquedas
- [x] Crear venv e instalar dependencias; dejar constancia del comando en README

**Hecho cuando:** `pip install -r requirements.txt` funciona en limpio y
`git status` no muestra ningún secreto como candidato a commit.

---

## Fase 2 — Núcleo: modelo y configuración

- [x] `models.py` — dataclass `Listing` (frozen, slots) con los 9 campos del
      contrato, más helpers de formato (`price_label`, `rating_label`) y
      `key -> (platform, id)`
- [x] `config.py`
  - [x] `SearchConfig`: `platform`, `query`, `price_threshold`,
        `seller_rating_threshold`, `min_price`, `min_seller_reviews`,
        `allow_unknown_rating`, `max_results`
  - [x] `TelegramConfig`: `bot_token`, `chat_id` (solo desde entorno)
  - [x] `ScrapingConfig`: `request_delay_seconds`, `timeout_seconds`,
        `max_retries`, `backoff_base_seconds`, `fetch_seller_details`,
        `user_agent`, `wallapop_latitude`, `wallapop_longitude`
  - [x] `AppConfig`: `searches`, `telegram`, `scraping`, `database_path`, `log_level`
  - [x] `load_config(path, env_file)` → lee YAML, carga `.env`, valida y
        devuelve `AppConfig`
  - [x] Validación con `ConfigError` y mensajes accionables: plataforma
        soportada, umbrales > 0, rating en 0-5, `request_delay_seconds >= 2`,
        lista de búsquedas no vacía, token y chat_id presentes

**Hecho cuando:** un `config.yaml` mal formado falla al arrancar con un mensaje
que dice exactamente qué campo está mal, no con un `KeyError`.

---

## Fase 3 — Persistencia y deduplicación

- [x] `storage.py` — clase `Storage` usada como context manager
  - [x] `notified_listings(id TEXT, platform TEXT, price REAL, title TEXT,
        url TEXT, notified_at TIMESTAMP, PRIMARY KEY (platform, id))`
  - [x] `init_schema()` idempotente (`CREATE TABLE IF NOT EXISTS`), `PRAGMA journal_mode=WAL`
  - [x] `is_notified(platform, listing_id) -> bool`
  - [x] `filter_new(listings) -> list[Listing]` (consulta en lote, no N queries)
  - [x] `mark_notified(listing)` — se llama **después** del envío correcto
  - [x] `purge_older_than(days)` para no crecer sin límite
  - [x] Crear el directorio padre de la BD si no existe

**Hecho cuando:** dos ejecuciones seguidas sobre los mismos datos notifican en
la primera y cero en la segunda.

---

## Fase 4 — Scrapers

### 4.1 `scrapers/base.py`
- [x] Excepciones `ScraperError`, `RateLimitedError`
- [x] `RateLimiter`: espera hasta que hayan pasado `request_delay_seconds`
      desde la petición anterior *a esa misma plataforma* (reloj monotónico)
- [x] `BaseScraper`: mantiene el `httpx.Client` (UA de navegador realista,
      timeout, cookies), método `get_json()` con:
  - [x] backoff exponencial ante 429/403/5xx, respetando `Retry-After`
  - [x] tope `max_retries` y luego abandonar esa búsqueda sin tumbar el ciclo
  - [x] logging de cada reintento
- [x] Interfaz `search(query, search_config) -> list[Listing]`
- [x] Caché de vendedores por `user_id` dentro del ciclo (no repetir peticiones)

### 4.2 `scrapers/wallapop.py`
- [x] Endpoint JSON interno de búsqueda (`api.wallapop.com/api/v3/...`) con
      cabeceras de navegador y `Referer` de `es.wallapop.com`
- [x] `_extract_items()` tolerante a varias formas de respuesta conocidas
      (payload anidado y `search_objects` antiguo)
- [x] `_parse_item()` → `Listing`; precio como dict `{amount, currency}` o
      plano; URL desde `web_slug`; imagen desde `images[0]`
- [x] Enriquecimiento opcional del vendedor (rating 0-5 y nº de reseñas)
- [x] Devolver `seller_rating=None` en vez de inventar valores si no se obtiene

### 4.3 `scrapers/vinted.py`
- [x] Bootstrap de sesión **anónima**: GET a la home para recoger cookies
      públicas (lo mismo que hace cualquier visitante sin cuenta; no hay login)
- [x] Endpoint `/api/v2/catalog/items` con `search_text`, `per_page`, orden por novedad
- [x] Re-bootstrap una sola vez si la API responde 401/403 por cookie caducada
- [x] `_parse_item()` → `Listing`
- [x] Vendedor vía `/api/v2/users/{id}`: `feedback_reputation` (0-1) → escala 0-5

### 4.4 `scrapers/__init__.py`
- [x] Registro `{"wallapop": WallapopScraper, "vinted": VintedScraper}` y
      `get_scraper(platform, scraping_config)`

**Hecho cuando:** con respuestas grabadas de ejemplo, cada scraper produce
`Listing` correctos, y ante 429 espera en vez de reintentar en bucle.

> Riesgo asumido: estos endpoints no son API pública y cambian sin aviso. Si un
> scraper deja de devolver resultados, lo primero es re-inspeccionar la petición
> real en devtools y comparar. Por eso el parseo es defensivo y loguea la forma
> inesperada en vez de reventar.

---

## Fase 5 — Filtro, notificación y orquestación

### 5.1 `filters.py`
- [x] `evaluate(listing, search) -> (bool, motivo)` — devuelve el motivo del
      descarte para poder depurar en modo DEBUG
- [x] `apply_filters(listings, search) -> list[Listing]`
- [x] Reglas: `min_price <= precio <= price_threshold`,
      `rating >= seller_rating_threshold`, `reseñas >= min_seller_reviews`,
      rating desconocido según `allow_unknown_rating`

### 5.2 `notifier.py`
- [x] `TelegramNotifier(bot_token, chat_id)` sobre la Bot API
- [x] `send_listing(listing) -> bool`: `sendPhoto` con caption si hay imagen,
      si no `sendMessage`; fallback a `sendMessage` si la foto falla
- [x] Mensaje: título, precio, plataforma, rating del vendedor, enlace directo
- [x] `parse_mode=HTML` con escapado de todo lo que venga del anuncio, y
      caption recortada a 1024 caracteres
- [x] Un fallo de envío se loguea y devuelve `False`; **nunca** tumba el ciclo
      ni marca el anuncio como notificado

### 5.3 `main.py`
- [x] `argparse`: `--config`, `--env-file`, `--db`, `--dry-run`, `--verbose`
- [x] Configuración de `logging` (nada de `print`)
- [x] Lock de fichero para que dos ejecuciones de cron no se solapen, con
      detección de lock obsoleto
- [x] Bucle: por cada búsqueda → scrape → filtrar → dedup → notificar →
      marcar como notificado
- [x] Aislar el fallo de una búsqueda para que las demás sigan
- [x] Resumen final en el log (encontrados / pasan filtro / nuevos / enviados)
- [x] Códigos de salida: 0 ok, 1 error de configuración, 2 todas las búsquedas fallaron
- [x] `--dry-run`: imprime lo que enviaría y no escribe en la BD

**Hecho cuando:** `python main.py --dry-run` recorre el ciclo entero sin
escribir nada y sin mandar nada a Telegram.

---

## Fase 6 — Tests (`pytest` + `respx`, sin red real)

- [x] `tests/conftest.py` — fixtures: config de prueba, BD temporal, respuestas
      JSON de ejemplo en `tests/fixtures/`
- [x] `test_config.py` — carga correcta y errores de validación
- [x] `test_filters.py` — límites exactos del precio y del rating, rating
      desconocido en ambos modos, `min_seller_reviews`
- [x] `test_storage.py` — dedup, idempotencia, `filter_new` en lote, purga
- [x] `test_wallapop.py` — parseo de las formas de respuesta soportadas,
      backoff ante 429, campos faltantes
- [x] `test_vinted.py` — bootstrap de cookies, parseo, escala del rating,
      re-bootstrap ante 401
- [x] `test_notifier.py` — `sendPhoto` vs `sendMessage`, escapado HTML,
      fallo de envío que no propaga excepción
- [x] `test_main.py` — ciclo completo con todo mockeado: notifica una vez y en
      la segunda pasada no notifica nada
- [x] Comprobar que ningún test hace peticiones reales (respx en modo estricto)

**Hecho cuando:** `pytest` pasa en verde y con red desconectada.

---

## Fase 7 — Despliegue y documentación

- [x] `README.md`: qué hace, instalación, cómo crear el bot con @BotFather,
      cómo sacar el `chat_id`, cómo rellenar `config.yaml`, cómo ejecutar
- [x] `deploy/snipebot.service` + `deploy/snipebot.timer` (Linux, cada 10 min
      con `RandomizedDelaySec` para no ser predecible)
- [x] `deploy/register-task.ps1` — Tarea Programada de Windows equivalente
      (el entorno de trabajo actual es Windows 11)
- [x] Sección de troubleshooting: qué mirar si un scraper deja de devolver nada
- [x] Actualizar el checklist de "Estado del proyecto" de `CLAUDE.md`
- [ ] Commit final en una rama, no directamente sobre `main`

---

## Criterios de aceptación del proyecto

1. Un ciclo completo funciona de punta a punta contra las dos plataformas.
2. Un anuncio se notifica **una sola vez**, aunque se ejecute cada 10 minutos.
3. Ningún secreto en el repositorio; `.env` y `config.yaml` ignorados.
4. Fallo de Telegram, de una plataforma o de un anuncio suelto → se loguea y el
   resto del ciclo continúa.
5. Rate limiting real: ≥ 3 s entre peticiones a la misma plataforma, ciclo no
   más frecuente que cada 5-10 min, backoff ante 429/403.
6. `pytest` verde sin tocar la red.

## Fuera de alcance (explícitamente)

Bypass de captchas, rotación de proxies, sesiones de usuario logueado, más
plataformas de las pedidas, polling por debajo de unos minutos, y guardar datos
del vendedor más allá de rating y número de reseñas.
