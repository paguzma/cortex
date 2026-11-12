---
title: BOOTSTRAP
type: note
---

# BOOTSTRAP — Cómo conectar una máquina a este cortex

Tres pasos y ese Claude Code (u otro cliente MCP) comparte la misma memoria y las mismas
skills que tus otras máquinas.

Los comandos exactos, con la URL y los tokens de esta instalación, están en la página
**Connect** de la UI de cortex (`/setup/connect`). Este archivo explica qué hace cada paso;
los tokens nunca se escriben en el vault.

## 1. Las skills y la memoria (Claude Code)

cortex publica un marketplace de plugins por git. Agregarlo a `~/.claude/settings.json`
instala dos plugins con actualización automática:

- `cortex`: las skills de memoria (`/mem-search`, `/mem-save`, `/mem-close`) y la conexión
  al MCP de esta instancia.
- `my-skills`: las skills que subas desde la UI.

El MCP necesita la variable `CORTEX_MCP_TOKEN` en el entorno de Claude Code; la página
Connect trae el bloque `env` listo para pegar.

## 2. Otros clientes MCP

Cualquier cliente con soporte de MCP por HTTP se conecta a `<url-de-cortex>/mcp` con el
encabezado `Authorization: Bearer <token>`. Sin las skills, el cliente debe leer
`SCHEMA.md` antes de escribir.

## 3. Comprobar

Abre una sesión y pide «busca en la memoria qué es este vault». Si responde con este
contenido, la conexión funciona.
