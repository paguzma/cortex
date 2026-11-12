# cortex

Memoria compartida para tus agentes. Un vault de notas markdown versionado con git, servido
por MCP, con un marketplace de skills para Claude Code y una UI web para configurarlo todo.
Levantas un contenedor, entras a una URL y conectas tus máquinas.

## Qué trae

- **Memoria por MCP** (`/mcp`): [basic-memory](https://github.com/basicmachines-co/basic-memory)
  sobre un vault que nace con lo mínimo: `AGENTS.md`, `SCHEMA.md` (el contrato de escritura),
  `BOOTSTRAP.md`, un `INDEX.md` vacío y templates por tipo de nota. Cuando una imagen nueva
  trae otra versión del contrato, el arranque la aplica con un commit propio, salvo en los
  archivos que editaste a mano: esos se quedan como están y Health los marca. Un archivo
  de contrato que borres no se repone, y un vault importado sin el commit de siembra de
  cortex ve como editado todo lo que difiera de la imagen.
- **Skills para Claude Code** (`/plugins.git`): un marketplace con el plugin `cortex`
  (`/mem-search`, `/mem-save`, `/mem-close`) y `my-skills`, donde van las tuyas. Tus
  máquinas las reciben solas en la siguiente sesión.
- **UI de configuración** (`/setup`): reclamo de la instancia, secretos (archivo local o
  [Infisical](https://infisical.com)), credencial de Claude para las rutinas, respaldo git,
  carga de skills y una página **Connect** con todo lo que necesitas copiar.

## Arranque

¿No eres técnico? Abre Claude Code (o la pestaña **Code** de la app Claude) y pégale:
«Instala cortex en este computador siguiendo https://github.com/paguzma/cortex/blob/main/INSTALAR.md».
Claude hace todo y te avisa cuando necesite un clic tuyo.

A mano, con la imagen publicada:

```bash
docker run -d --name cortex --restart unless-stopped -p 127.0.0.1:8700:8700 \
  -v cortex-data:/data ghcr.io/paguzma/cortex:latest
docker exec cortex cortex-quickstart --timezone America/Santiago
```

`cortex-quickstart` reclama la instancia con una contraseña de admin generada y te imprime
la contraseña y el bloque para `~/.claude/settings.json`. Las rutinas y el respaldo se
configuran después en `/setup`.

O, para seguir el asistente web desde el código fuente:

```bash
git clone https://github.com/paguzma/cortex.git && cd cortex
docker compose up -d
docker compose logs cortex | grep "Setup code"
```

Abre `http://<tu-host>:8700/setup`, pega el código y elige la contraseña de admin. El
asistente te pide:

1. **Instancia**: nombre, zona horaria y la URL con que tus máquinas llegan al contenedor.
2. **Secretos**: archivo local (por defecto) o tu Infisical.
3. **Claude** (opcional): un token de `claude setup-token` (usa tu suscripción, dura un año)
   o una API key. Solo lo usan las rutinas de mantenimiento; sin él, todo lo demás funciona.
4. **Respaldo git** (opcional): un remoto SSH. cortex genera la deploy key.

Al terminar, la página **Connect** te da el bloque para `~/.claude/settings.json`.

## Rutinas

Corren dentro del contenedor, nunca dos a la vez, en la zona horaria de la instancia. Se
gestionan en **Routines** (`/setup/routines`): horario, modelo, historial de las últimas 30
corridas y un botón **Run now**.

| Rutina | Por defecto | Qué hace | Usa Claude |
|---|---|---|---|
| `consolidate` | 04:30 | fusiona duplicados, tacha hechos superados, archiva episodios viejos y arregla el INDEX; un validador determinista decide si pasó | sí |
| `clean` | 05:00 | ordena hasta dos notas por noche y mueve lo tachado al historial (`/data/archive`); **solo propone** hasta que actives «Apply changes» | sí |
| `snapshot` | cada hora | commitea el vault y lo empuja a tu remoto, si configuraste uno | no |

Sin credencial de Claude, las dos primeras quedan apagadas y todo lo demás sigue
funcionando. El consumo va contra tu propia cuenta: usa el modelo más capaz que tengas,
porque estas rutinas reescriben tu memoria.

Para enterarte de las fallas, pon una **Alert URL** (ntfy, n8n o cualquier webhook): cortex
le manda un POST JSON solo cuando una rutina falla.

## Dominios

Los dominios (las áreas grandes de tu memoria: casa, trabajo, finanzas…) nacen solos. El
vault parte con uno, `general`. Cada noche, después de consolidar, cortex busca en el grafo
grupos de al menos cinco notas enlazadas que todavía no tienen dominio, sin dejar que una
nota muy conectada los arrastre a todos. El modelo solo les pone nombre; cortex escribe el
campo `area:` en esas notas y verifica que no cambió nada más.

- Una nota que ya tiene `area:` nunca cambia de dominio sola.
- Los dominios nuevos aparecen en **Pending** de Agent OS y en **Domains**
  (`/setup/domains`), donde puedes renombrarlos o fusionarlos.
- Si hay más de siete, cortex **propone** fusionar los dos más chicos; nunca lo hace solo.
- Sin credencial de Claude, **Domains** muestra los grupos sin nombre para que los nombres tú.

## Agent OS

En `/os/`, con la misma sesión de admin, está el panel en vivo de tu memoria:

- **El grafo**: notas, reglas, skills, rutinas, el MCP y tus máquinas, en seis vistas
  (anillos, círculo, dominios, enlaces, línea de tiempo, órbita). Cada nota cae en el
  dominio de su campo `area:` o, si no lo tiene, en el de sus vecinas.
- **Pulsos**: cada lectura y escritura por MCP se ve unos segundos después, con la máquina
  que la hizo. Ponle nombre a cada IP en **Clients** (`/setup/clients`).
- **Routines** con sus logs, **Pending** (las observaciones `[pendiente]`, las rutinas que
  fallaron y el token de Claude por vencer) y una barra de estado.

El panel solo muestra: todo lo que dibuja se recalcula desde el vault y los reportes.

## Conectar Claude Code

Pega el bloque de **Connect** en `~/.claude/settings.json`, abre una sesión y corre:

```
/plugin install cortex@cortex
/plugin install my-skills@cortex
```

Prueba con «busca en la memoria qué es este vault».

Al abrir cada sesión, el plugin `cortex` inyecta el INDEX del vault como mapa. Ese hook
corre con el `python3` del PATH (3.9 o más nuevo, el de macOS sirve); en una máquina sin
`python3`, como Windows sin Python instalado, Claude Code avisa que el hook falló y la
sesión sigue sin el mapa.

Otros clientes MCP: `<url>/mcp` con el encabezado `Authorization: Bearer <token MCP>`.

## Seguridad

- Hay un solo puerto, `8700`, sin TLS. Para exponerlo fuera de tu red, ponlo detrás de tu
  propio proxy con HTTPS.
- **Docker se salta el firewall del host** al publicar puertos. Si no quieres que toda tu
  red lo vea, publica solo en una interfaz: `"127.0.0.1:8700:8700"` o la IP de tu VPN.
- **Detrás de un proxy** (`tailscale serve`, tu propio HTTPS) Agent OS vería todas las
  peticiones venir del gateway de Docker. Para que atribuya cada una a su máquina, define
  `CORTEX_TRUSTED_PROXIES` en un `.env` junto al compose con la IP o el rango desde el que llega el proxy (normalmente
  el gateway de la red del compose, p. ej. `172.18.0.1`; míralo con
  `docker network inspect <proyecto>_default`, donde `<proyecto>` es el nombre del proyecto
  compose, por defecto la carpeta del `compose.yml`) **y** publica el puerto solo en loopback,
  `"127.0.0.1:8700:8700"`. Tras editar `.env`, recrea el contenedor con `docker compose up -d`:
  `docker compose restart` no toma el valor nuevo. Confiar en un rango con el puerto abierto a la red deja que
  cualquier equipo de la LAN falsifique `X-Forwarded-For`: en Docker sus conexiones
  directas también llegan desde el gateway. Sin la variable no se confía en nadie.
- El token MCP lee y escribe toda tu memoria. El de plugins solo lee las skills, pero queda
  escrito en el `settings.json` de cada cliente. Puedes regenerarlos desde **Connect**.
- Los secretos del archivo local son de solo escritura en la UI: nunca se vuelven a mostrar.

## Datos

Todo vive en el volumen `/data`:

| Ruta | Qué es | ¿Respaldar? |
|---|---|---|
| `vault/` | la memoria (repo git) | sí |
| `plugins/` | el marketplace (repo git) | sí |
| `config/` | configuración, secretos locales, deploy key | sí |
| `archive/` | historial de lo que limpian las rutinas | sí |
| `state/` | índices, logs, caché | no, se reconstruye |

## Desarrollo

```bash
uv venv -p 3.12 && uv pip install -e '.[dev]'
uv run --extra dev pytest -q   # unitarias (pytest está en el extra dev)
tests/e2e/run.sh               # punta a punta contra un contenedor real
```
