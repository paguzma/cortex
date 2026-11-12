---
name: mem-search
description: Recupera contexto desde la memoria cortex con lectura progresiva. Usar al inicio de tareas que dependen de contexto del usuario (personas, proyectos, equipos, decisiones previas) o cuando falta información que el usuario asume conocida.
---

# mem-search

Disciplina de lectura, en orden, deteniéndote apenas tengas suficiente:

1. `INDEX.md` (vía `read_note`): el mapa de una línea por nota.
2. `search_notes` por palabra clave y sinónimos (los aliases existen para esto).
3. `read_note` de la o las notas canónicas encontradas.
4. `build_context` sobre la nota para traer su vecindario del grafo si la tarea cruza
   entidades.
5. Episodios (`episodes/`) SOLO si necesitas el detalle de qué pasó en una sesión.

Reglas: NUNCA cargues el vault completo al contexto. Si un dato tiene `(as of ...)` viejo
(más de 6 meses), trátalo como posiblemente desactualizado y verifícalo contra su fuente
antes de actuar. Si el dato es volátil (IP, estado), sigue el puntero a su fuente viva en
vez de confiar en la copia.

Si el MCP `cortex` no responde, dilo al usuario en vez de seguir sin memoria.
