---
name: mem-close
description: Cierre de una sesión de trabajo. Escribe el episodio del día y propaga los hechos durables a la memoria cortex. Usar al terminar una sesión significativa o cuando el usuario dice "cerremos" o "guarda lo de hoy".
---

# mem-close

1. Crea `episodes/AAAA/AAAA-MM-DD--<tema-slug>.md` desde `_templates/episode.md`:
   - **Qué pasó**: relato corto y fechado de la sesión.
   - **Decisiones**: las tomadas; si una amerita nota propia, créala en `decisions/` y
     enlázala.
   - **Pendientes**: qué quedó abierto y quién lo destraba.
   - **Relaciones**: `sobre [[entidad]]` por cada entidad tocada.
2. Por CADA hecho durable que surgió en la sesión (estado nuevo, gotcha, regla, acceso),
   aplica los pasos de `mem-save` sobre su nota canónica. El episodio NO reemplaza esa
   propagación.
3. El episodio es append-only: no edites episodios de días anteriores; si algo cambió, va
   como hecho nuevo en la entidad.
