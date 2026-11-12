# Instalar cortex (instrucciones para Claude)

Este archivo es para **Claude Code**, no para la persona. Alguien te pegó un mensaje pidiéndote
que instales cortex en su computador siguiendo esto. Asume que la persona **no es técnica**:
nunca sabe qué es una terminal, Docker ni un archivo JSON, y no tiene que aprenderlo.

## Reglas

- Hazlo tú. Solo pídele algo a la persona cuando de verdad no puedas hacerlo tú: los tres
  momentos de abajo. Cuando llegue uno, **avísale antes** en una o dos frases simples qué va a
  ver y qué botón apretar, y espera a que te diga que listo.
- Habla en español simple, sin jerga. Cuéntale en una línea qué estás haciendo en cada paso
  («Ahora estoy instalando Docker, el programa donde va a vivir tu memoria»). Las esperas
  largas avísalas («esto tarda unos 5 minutos»).
- No muestres tokens ni contraseñas en tus mensajes. Van a archivos, no al chat.
- No toques nada fuera de: Docker Desktop, Python, `~/.claude/settings.json`, el contenedor
  `cortex` y la carpeta donde estás trabajando. No cambies otras configuraciones del sistema.
- Lleva el avance en `cortex-instalacion.md` en la carpeta de trabajo (qué pasos terminaste).
  Si la persona vuelve después de reiniciar y te dice «continúa», léelo y sigue desde ahí.
- Si algo falla dos veces seguidas, no improvises: dile en simple qué pasó y que le escriba a
  quien le pasó estas instrucciones, con el mensaje de error.

### Los tres momentos de la persona

Coinciden con la infografía que recibió:

1. **«¿Quieres permitir que esta aplicación haga cambios en el dispositivo?»** (Windows): que
   apriete **Sí**. En Mac, en cambio, le pide su contraseña del computador.
2. **Reiniciar** (solo Windows, si lo pide la instalación de WSL o Docker): que guarde lo que
   tenga abierto, reinicie, vuelva a abrir Claude, entre a **esta misma conversación** y
   escriba **continúa**.
3. **Docker Desktop la primera vez**: que acepte la licencia (**Accept**) y que se salte la
   cuenta («Skip» / «Continue without signing in»). Las encuestas también se saltan.

## Pasos

Antes de empezar, detecta el sistema operativo y qué shell tienes (en Windows puede ser Git
Bash o PowerShell). Los comandos de abajo son de referencia: adáptalos a tu shell.

### 1. Docker Desktop

Revisa si ya está: `docker version`. Si responde con «Server», salta al paso 2.

**Windows**

1. Revisa WSL con `wsl --status`. Si no está instalado, instálalo con
   `wsl --install --no-distribution` (momento 1, y casi seguro momento 2 después).
2. Instala Docker Desktop:
   `winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements`
   (momento 1). Si `winget` no existe, descarga el instalador oficial de
   https://www.docker.com/products/docker-desktop/ y ábrelo.
3. Si el instalador pide reiniciar: momento 2.

**Mac**: `brew install --cask docker` si hay Homebrew; si no, el `.dmg` oficial (Apple
Silicon o Intel según `uname -m`) y arrástralo a Aplicaciones (momento 1).

Abre Docker Desktop (Windows: `"C:\Program Files\Docker\Docker\Docker Desktop.exe"`; Mac:
`open -a Docker`) y avisa del momento 3. Espera hasta que `docker info` responda sin error
(puede tardar 1 a 3 minutos la primera vez; reintenta cada 10 segundos, hasta 5 minutos).

Deja Docker arrancando solo al prender el computador: en Docker Desktop es «Start Docker
Desktop when you sign in to your computer» (Settings → General), que viene activo por
defecto. Verifícalo; si no puedes, dile a la persona dónde está y que lo marque.

### 2. Levantar cortex

```
docker run -d --name cortex --restart unless-stopped -p 127.0.0.1:8700:8700 -v cortex-data:/data ghcr.io/paguzma/cortex:latest
```

- Si ya existe un contenedor `cortex` (por un intento anterior), no lo borres: si está
  detenido, `docker start cortex`; si está corriendo, sigue.
- Publica **solo** en `127.0.0.1`: así nadie más de la red llega a la memoria.
- Bajar la imagen tarda unos minutos.

Espera a que responda `http://127.0.0.1:8700/setup/login` (por ejemplo con
`curl -fsS -o /dev/null http://127.0.0.1:8700/setup/login`; en PowerShell usa `curl.exe`).
Reintenta cada 5 segundos, hasta 3 minutos. Después espera también a la memoria, que
arranca unos segundos más tarde: `docker exec cortex curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8766/mcp`
tiene que dar un código distinto de `000`.

### 3. Configurarlo

Averigua la zona horaria del computador y conviértela a formato IANA (en Windows,
`tzutil /g` da el nombre de Windows: «Pacific SA Standard Time» es `America/Santiago`; en
Mac, `readlink /etc/localtime`). Luego:

```
docker exec cortex cortex-quickstart --timezone <zona IANA> --base-url http://127.0.0.1:8700
```

Guarda la salida directamente en `cortex-quickstart.json` en la carpeta de trabajo, sin
mostrarla. Es un JSON con:

- `admin_password`: la contraseña de la página de administración. Escríbela en
  `cortex-admin.txt` en la carpeta de trabajo, junto con la dirección `setup_url`, en dos
  líneas claras para una persona. Es `null` si cortex ya estaba configurado: entonces ese
  archivo ya debería existir de antes.
- `settings`: el bloque para conectar Claude Code.

Si el comando sale con código 75, cortex todavía está arrancando: espera 10 segundos y
reintenta.

### 4. Conectar Claude

1. Lee `~/.claude/settings.json` (en Windows, `%USERPROFILE%\.claude\settings.json`). Si
   existe, cópialo antes a `settings.json.antes-de-cortex` en esa misma carpeta.
2. **Fusiona** el bloque `settings` sin borrar lo que ya haya: agrega
   `env.CORTEX_MCP_TOKEN`, la entrada `cortex` de `extraKnownMarketplaces` y las dos de
   `enabledPlugins`. Si el archivo no existe, créalo con ese bloque. Valida que quede un JSON
   correcto.
3. Instala los plugins. Primero registra el marketplace con la URL de
   `settings.extraKnownMarketplaces.cortex.source.url` (lleva un token: no la muestres):
   `claude plugin marketplace add "<esa URL>"`. Después
   `claude plugin install cortex@cortex` y `claude plugin install my-skills@cortex`.
   Si el comando `claude` no está disponible en tu shell, sigue: al final le pedirás a la
   persona que los instale (paso 6).
4. Borra `cortex-quickstart.json`: tiene los tokens y ya están en `settings.json`.

### 5. Python (solo Windows, para el mapa de la memoria)

Al abrir cada conversación, el plugin carga un mapa de la memoria con un script de Python.
Si `py -3 --version` falla, instala Python:
`winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements`
(momento 1). Si no se puede, sigue igual: la memoria funciona sin el mapa.

### 6. Terminar

1. Marca la instalación como terminada en `cortex-instalacion.md`.
2. Explícale a la persona, en simple:
   - Que ya está. Que abra una **conversación nueva** en Code (las conexiones se cargan al
     empezar una conversación).
   - Si no pudiste instalar los plugins en el paso 4.3: que en esa conversación nueva escriba
     `/plugin install cortex@cortex`, Enter, y luego `/plugin install my-skills@cortex`. Si
     dice que no encuentra el marketplace, que escriba `/plugin` y elija el marketplace
     `cortex` de la lista.
   - La prueba: en esa conversación, «Recuerda que mi comida favorita es la pizza»; luego, en
     otra conversación nueva, «¿Cuál es mi comida favorita?».
   - Que su memoria vive en su computador y funciona mientras Docker Desktop esté abierto.
   - Que en `cortex-admin.txt` (dile en qué carpeta) quedó la contraseña de la página de
     administración: que no la borre ni la comparta.
   - Que las rutinas de mantenimiento nocturno (ordenan la memoria con Claude) quedan
     apagadas; se activan después en la página de administración con un token de
     `claude setup-token`, si algún día quiere.

## Si algo sale mal

- **`docker info` nunca responde**: Docker Desktop sigue arrancando o le falta WSL. Abre
  Docker Desktop y mira qué muestra; si pide actualizar WSL, `wsl --update` (momento 1).
- **No baja la imagen** (`denied` o `not found`): sin internet, o la imagen todavía no es
  pública. No sigas: avísale a la persona que le escriba a quien le pasó esto.
- **El puerto 8700 está ocupado**: usa otro (`-p 127.0.0.1:8701:8700`) y pasa
  `--base-url http://127.0.0.1:8701` a `cortex-quickstart`.
- **Ver los logs**: `docker logs --tail 80 cortex`.
