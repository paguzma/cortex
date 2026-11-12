---
name: mem-save
description: Guarda un hecho en la memoria cortex respetando el SCHEMA. Usar cuando surge un dato durable (estado de un proyecto, gotcha, preferencia, decisión, acceso) o el usuario dice "recuerda esto" o "anota esto".
---

# mem-save

1. Lee `SCHEMA.md` del vault (`read_note`) si no lo tienes en contexto. El SCHEMA manda
   sobre esta skill si difieren.
2. Clasifica el hecho: project | infra | tool | person | decision | feedback | reference.
3. BUSCA la nota canónica: `search_notes` por título Y por aliases probables.
   - Si existe, actualízala: una línea nueva en Observaciones, o un supersede fechado si
     contradice lo anterior (`~~viejo~~ ← superseded AAAA-MM-DD por <nuevo>`).
   - Si no existe, créala desde `_templates/<tipo>.md`.
4. El hecho lleva alcance y fecha DENTRO del texto:
   `- [estado] migración en fase 2 (as of 2026-08)`.
5. Verifica o agrega `## Relaciones` con al menos un wikilink a una entidad existente.
6. Actualiza la línea de esa nota en `INDEX.md` (créala si falta). Si el INDEX ya tiene 200
   líneas, NO agregues la línea ni recortes otras: déjala sin línea y avisa al usuario.
7. Actualiza `updated:` en el frontmatter.

PROHIBIDO: valores de secretos (solo nombres), duplicar notas existentes, borrar líneas
con historia, mover o renombrar archivos (eso es del consolidador), guardar lo derivable
de un repo de código.
