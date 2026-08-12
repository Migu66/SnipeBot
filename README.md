# SnipeBot

Vigila Wallapop y/o Vinted en busca de un producto concreto (por ejemplo una
cámara) y avisa por Telegram **solo** cuando aparece un anuncio que cumple:

- precio ≤ `price_threshold`
- valoración del vendedor ≥ `seller_rating_threshold`
- el anuncio no se ha notificado ya antes (deduplicación)

No es una herramienta de scraping masivo ni de reventa automatizada: es un
asistente personal de bajo volumen para no tener que refrescar la app
constantemente. Ver `CLAUDE.md` para el detalle de arquitectura y las
restricciones de diseño (rate limiting, qué no hacer, etc.).

> **Ahora mismo solo funciona con Vinted.** El scraper de Wallapop está
> implementado pero su endpoint real todavía no responde correctamente (ver
> Troubleshooting más abajo), así que `config.example.yaml` solo trae una
> búsqueda de Vinted.

## Instalación

Requiere Python 3.11+.

```bash
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

Verifica que no hay ningún secreto listo para commitear:

```bash
git status
```

`.env` y `config.yaml` están en `.gitignore`; solo deben existir en tu
máquina, nunca en el repositorio.

## Configuración

### 1. Bot de Telegram

1. Habla con [@BotFather](https://t.me/BotFather) en Telegram y crea un bot
   con `/newbot`. Te dará un token con forma `123456789:AA...`.
2. Escríbele algo a tu bot recién creado (cualquier mensaje, para que
   Telegram registre la conversación).
3. Abre en el navegador, sustituyendo `<TOKEN>` por el token real:

   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

4. Busca `"chat":{"id":...}` en la respuesta JSON. Ese número es tu
   `chat_id`.

### 2. `.env`

```bash
cp .env.example .env
```

Rellena `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` con los valores del paso
anterior.

### 3. `config.yaml`

```bash
cp config.example.yaml config.yaml
```

Ajusta `searches` a lo que quieras vigilar (una entrada por plataforma y
búsqueda) y, si quieres, los parámetros de `scraping` (ver comentarios en el
propio fichero). Los valores por defecto ya respetan el rate limiting mínimo
exigido por `CLAUDE.md`.

## Uso

Ciclo normal (pensado para cron/timer, no para dejarlo corriendo):

```bash
python main.py
```

Ver qué notificaría sin tocar la base de datos ni enviar nada a Telegram:

```bash
python main.py --dry-run --verbose
```

Otras opciones (`python main.py --help`):

| Flag | Efecto |
|---|---|
| `--config RUTA` | usa otro `config.yaml` (por defecto `config.yaml`) |
| `--env-file RUTA` | usa otro `.env` (por defecto `.env`) |
| `--db RUTA` | sobrescribe `database_path` de la config |
| `--dry-run` | no escribe en la BD ni llama a Telegram |
| `--verbose` | log en `DEBUG` |

Códigos de salida: `0` ok, `1` configuración inválida, `2` fallo total del
ciclo (todas las búsquedas fallaron, o ya había otro ciclo en marcha).

## Tests

```bash
pip install -r requirements.txt   # incluye pytest y respx
pytest
```

Los tests no hacen ninguna petición de red real: todo el HTTP está mockeado
con `respx`. Puedes comprobarlo desconectando la red y volviendo a lanzar
`pytest`.

## Despliegue

El proceso es de un solo tiro: se lanza, hace un ciclo, y termina. No hay que
mantenerlo vivo; lo programa el sistema operativo.

### Linux (systemd timer)

```bash
sudo cp deploy/snipebot.service /etc/systemd/system/
sudo cp deploy/snipebot.timer /etc/systemd/system/
# Edita snipebot.service: WorkingDirectory, ExecStart y User a tu instalación real
sudo systemctl daemon-reload
sudo systemctl enable --now snipebot.timer
systemctl list-timers snipebot.timer   # próxima ejecución
journalctl -u snipebot.service -f      # logs en vivo
```

El timer corre cada 10 minutos con un retraso aleatorio (`RandomizedDelaySec`)
para no ser predecible.

### Windows (Tarea Programada)

```powershell
.\deploy\register-task.ps1
```

Registra una Tarea Programada que ejecuta `main.py` cada 10 minutos con el
Python del `.venv` del proyecto. Revísala/edítala desde `taskschd.msc` si
quieres cambiar la frecuencia. Para quitarla:

```powershell
Unregister-ScheduledTask -TaskName "SnipeBot" -Confirm:$false
```

## Troubleshooting

**El scraper de Wallapop no trae nada (estado conocido, sin resolver).**
Su endpoint de búsqueda actual (`api/v3/search/section`) devuelve `400` sin
detalle con los parámetros documentados en el propio frontend. Vinted sí
está validado y funciona. Ver la cabecera de `scrapers/wallapop.py` y la
sección "Estado del proyecto" de `CLAUDE.md` para el detalle de lo probado.
Si quieres ayudar a resolverlo: abre `es.wallapop.com/app/search?keywords=...`
en el navegador, DevTools → Network → XHR/Fetch, busca la petición a
`api.wallapop.com/api/v3/search/section` que sí trae anuncios, y compara sus
parámetros/cabeceras exactos contra `scrapers/wallapop.py`.

**Un scraper deja de devolver resultados (0 anuncios siempre) — caso general.**
Wallapop y Vinted no tienen API pública: sus endpoints internos cambian sin
aviso. Abre la web en el navegador, DevTools → Network → XHR/Fetch, busca el
producto y compara la petición real (URL, parámetros, forma de la respuesta)
con `scrapers/wallapop.py` / `scrapers/vinted.py`. El parseo está pensado
para ser defensivo (loguea en `DEBUG` la forma que no reconoce en vez de
reventar), así que primero mira los logs en `--verbose`.

**Errores 429/403 constantes.** El scraper ya hace backoff exponencial y
respeta `Retry-After`; si persiste, sube `request_delay_seconds` y
`backoff_base_seconds` en `config.yaml`. No bajes `request_delay_seconds` de
2 segundos ni el ciclo completo a menos de 5 minutos: es una limitación de
diseño explícita, no un valor arbitrario.

**Vinted devuelve 401 tras un rato.** El scraper re-hace la cookie de sesión
anónima una vez automáticamente. Si sigue fallando después de eso, es que el
endpoint o el mecanismo de sesión cambiaron: hay que re-inspeccionar en
DevTools.

**Un anuncio se notifica más de una vez.** No debería pasar: la
deduplicación es por `(platform, id)` en SQLite y se marca justo después de
un envío correcto. Si ocurre, revisa que `database_path` apunte siempre al
mismo fichero entre ejecuciones (por ejemplo, que el cron no lo esté
lanzando desde working directories distintos con rutas relativas).

**"ya hay un ciclo en marcha".** Dos ejecuciones se han solapado, o una
anterior murió sin limpiar su lock. El lock se considera obsoleto (y se
descarta solo) pasados 15 minutos; si necesitas desbloquear antes, borra a
mano el fichero `<database_path sin extensión>.lock`.

**Fallos de Telegram.** Se loguean como error pero no tumban el ciclo ni
marcan el anuncio como notificado: se reintentará notificarlo en el próximo
ciclo. Revisa que el bot token y el chat_id sean correctos y que hayas
escrito al menos un mensaje al bot antes de sacar el `chat_id`.

**Solo llega un anuncio por ciclo aunque haya varios nuevos.** Dos causas
posibles, ambas ya corregidas pero a vigilar si reaparecen:

- Telegram limita a ~1 mensaje/segundo por chat y responde 429 si se manda
  una ráfaga; `notifier.py` ahora espacia los envíos y reintenta respetando
  `retry_after`. Si un envío agota los reintentos, se loguea y se
  reintentará en el próximo ciclo (no queda marcado como notificado).
- En GitHub Actions, el paso "Guardar historico de deduplicacion" hacía
  `git add -f` de `data/snipebot.db` junto a `.db-wal`/`.db-shm`; como estos
  dos últimos no existen tras un cierre limpio de SQLite en modo WAL, `git
  add` abortaba sin añadir *nada* (ni siquiera el `.db`), así que el
  histórico nunca se comiteaba y cada ciclo arrancaba con la BD vacía. Ahora
  cada fichero se añade por separado. Si vuelve a pasar, comprueba en el log
  del workflow que el paso realmente hace `git commit`/`git push` (no solo
  "success" sin cambios) y que `git log -- data/snipebot.db` avanza entre
  ejecuciones.
