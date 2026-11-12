---
title: SCHEMA
type: note
---

# SCHEMA — Contrato de escritura del vault

> LECTURA OBLIGATORIA antes de escribir. Aplica a todo agente (Claude, Gemini u otro) y a
> toda vía (MCP, archivos, skills). Si tu cliente tiene las skills `/mem-*`, úsalas:
> implementan este contrato.

## Cómo se escribe aquí

**Si tienes las skills `/mem-*`, ÚSALAS. No escribas notas a mano.** `/mem-save` para un
hecho, `/mem-close` para cerrar una sesión, `/mem-search` para leer. Buscan la nota
canónica antes de crear otra, ponen las relaciones y actualizan el índice. Escribir «a
pulso» es como nacen los duplicados.

Si NO tienes las skills (otro cliente, otro modelo), lee este archivo completo antes de
escribir y aplica lo mismo a mano.

**El grafo no es decoración.** `build_context` recorre las relaciones para armar el
vecindario de una nota: las relaciones SON los caminos de recuperación. Una nota sin links
entrantes es una nota que nadie va a encontrar; un hecho copiado en dos notas son dos
caminos que se contradicen en cuanto uno se actualiza. Por eso:

1. **Antes de crear, busca.** `search_notes` por título y por aliases. Si la entidad
   existe, se actualiza; no se crea una nota «parecida».
2. **Un hecho vive en UN lugar.** El detalle de una herramienta va en su nota, no repetido
   en la del servidor que la corre; el servidor la enlaza.
3. **Toda nota nueva se enlaza desde algo existente.** Crear una nota que nadie menciona
   es enterrarla.

## Dónde va cada cosa

| Tipo | Carpeta | Regla de nombre |
|---|---|---|
| Persona o equipo | `entities/people/` | slug estable: `ana-perez.md` |
| Proyecto | `entities/projects/` | `sitio-web.md` |
| Infra, servicio o equipo | `entities/infra/` | `servidor-casa.md` |
| Herramienta | `entities/tools/` | `obsidian.md` |
| Decisión (ADR) | `decisions/` | `AAAA-MM-DD--tema.md` |
| Regla de trabajo | `feedback/` | `no-enviar-correos-sin-revisar.md` |
| Registro de sesión | `episodes/AAAA/` | `AAAA-MM-DD--tema.md` |
| Puntero a fuente externa | `references/` | `mapa-de-red.md` |

## Frontmatter obligatorio

```yaml
---
title: Nombre Legible
type: project|infra|tool|person|decision|feedback|episode|reference
status: active|superseded|archived
created: AAAA-MM-DD
updated: AAAA-MM-DD
aliases: [sinónimos, apodos, términos de búsqueda]
area: opcional; dominio de la nota (lo asigna el consolidador si falta)
---
```

## Cuerpo de la nota

1. Primer párrafo: qué es, en 1 a 3 frases, con los datos volátiles fechados `(as of AAAA-MM)`.
2. `## Observaciones`: hechos atómicos, `- [categoría] hecho concreto`. El alcance va
   DENTRO del hecho («prefiere async *para avisos de estado*»).
3. `## Relaciones`: wikilinks obligatorios, `- relación [[nota-destino]]`. Toda nota nueva
   enlaza al menos una entidad existente. Relaciones típicas: corre_en, depende_de,
   pertenece_a, decidido_en, documentado_en, sobre, aplica_a, describe_a.

## Reglas duras

- **Un hecho, un lugar canónico.** Antes de crear, buscar por título y aliases; si existe,
  actualizar.
- **Invalidar, no borrar.** Hecho superado: `~~texto viejo~~ ← superseded AAAA-MM-DD por
  <nuevo>`. Nunca eliminar líneas con historia. Solo la rutina nocturna de limpieza mueve
  lo tachado al historial (`/data/archive`), que también está en git y nunca se borra.
- **Atemporal, fechado o puntero.** Los valores que cambian (IPs, estados, contadores) se
  enlazan a su fuente, no se copian.
- **Secretos: solo nombres.** «la key vive en el secreto `MI_API_KEY`». JAMÁS valores, ni
  parciales.
- **`INDEX.md` ≤ 200 líneas.** Una línea por nota (salvo episodios): `- [Título](ruta) —
  gancho con aliases`. Con el índice lleno, la nota nueva queda sin línea: la nota sigue
  existiendo y se encuentra por búsqueda, y el validador lo reporta como aviso, no como
  violación. Nunca se pasa de 200 líneas para hacerle sitio.
- **El INDEX usa links markdown a propósito** (`[Título](ruta)`, sin wikilinks): es un mapa
  de entrada, no un hub del grafo. No convertirlos a wikilinks.
- **Qué NO guardar:** lo derivable de un repo de código, credenciales, contexto puntual sin
  valor futuro.
- **Episodios** son append-only; a los 30 días sin re-referencia los archiva el consolidador.
- **Reorganizar (mover, renombrar, fusionar) solo lo hace el consolidador nocturno.** Los
  demás agentes crean y actualizan.

## Gotchas de la herramienta (basic-memory MCP)

- **Escribe siempre por las tools del MCP** (`write_note`, `edit_note`), nunca copiando
  archivos al vault: el indexador reconcilia el archivo con su base y puede pisar lo
  copiado.
- `write_note` **deriva el nombre del archivo del título**. Para respetar la convención,
  pasa el título ya en formato slug.
- Los wikilinks apuntan al **nombre de archivo** (slug), no al título.
