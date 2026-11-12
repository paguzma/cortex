/* app.js — dibuja el panel a partir de data/state.json.
 *
 * No pide nada a ninguna API ni guarda nada: lee un archivo que el recolector ya
 * escribió. Si el archivo está viejo, se dice; si un bloque falló, se marca.
 * Nunca se inventa un cero para tapar una fuente caída.
 *
 * El grafo: UN solo conjunto de nodos y seis vistas de los mismos nodos. Al cambiar
 * de vista los puntos VIAJAN de una disposición a otra, no se redibujan.
 * Todas las URLs son relativas: el panel vive bajo /os/ detrás del proxy.
 */

/* ---------- todo lo que se afina está acá: un número por perilla ---------- */
const CONFIG = {
  morphMs: 1100,          // duración del viaje entre vistas
  stagger: 380,           // desfase máximo entre el primer y el último punto en salir
  spinRings: 0.000011,    // rad/ms del giro lento de los anillos (~9,5 min por vuelta)
  spinOrbit: 0.00011,     // rad/ms de la esfera de la vista órbita
  spinClients: 0.00016,   // rad/ms de los clientes orbitando
  particles: 320,         // granos de la nube del núcleo
  coreR: 0.07,            // radio de la nube, como fracción de R
  radii: { skills: 0.125, rules: 0.195, hubs: 0.268, memIn: 0.35, memOut: 0.65, routines: 0.755, mcps: 0.84, clients: 0.925 },
  step: 9.5,              // separación entre puntos en las gradas, en px
  sectorGap: 0.2,         // rad de aire entre un dominio y el siguiente
  memStepMax: 1.7,        // tope del paso de la memoria, en múltiplos de «step»
  sizeBase: 1.8, sizePerDegree: 0.75, sizeMax: 6.5,
  sizeClient: 4.6, sizeMcp: 3.5,  // fijos: lo que corre importa por sí mismo, no por sus enlaces
  sizePerByte: 0.034, sizeNoteMin: 1.2, sizeNoteMax: 7.5,  // memoria: radio ∝ √bytes
  forceIter: 320,
  dim: 0.1,               // opacidad de lo que no está en foco (hover o filtro)
  zoomMin: 0.4, zoomMax: 6, zoomFly: 2.4,
  pulseMs: 1600,          // ms que tarda un pulso real en ir de quien actuó a lo que tocó
  flashMs: 900,           // ms del anillo que crece en una nota escrita
  nebula: 0.085,          // opacidad del resplandor de cada dominio
};

const $ = (s) => document.querySelector(s);
// Los dominios son dinámicos: el color sale del orden de la lista, `general` siempre gris.
const PALETTE = ['#58a6ff', '#3fb950', '#e3b341', '#a371f7', '#f778ba', '#56d4dd', '#ffa657', '#7ee787', '#ff7b72', '#79c0ff'];
const GENERAL_COLOR = '#8b949e';
const CORE_COLOR = '#ff7a2f';
const AREAS = () => DATA?.graph?.domains || Object.keys(DATA?.graph?.areas || {});
function color(a) {
  if (!a || a === 'general') return GENERAL_COLOR;
  const i = AREAS().filter((x) => x !== 'general').indexOf(a);
  return i < 0 ? '#6e7681' : PALETTE[i % PALETTE.length];
}
const LAYERS = ['core', 'skills', 'rules', 'memory', 'routines', 'mcps', 'clients'];
const LAYER_NAME = { core: 'core', skills: 'skills', rules: 'rules', memory: 'memory', routines: 'routines', mcps: 'mcps', clients: 'clients' };
const LAYER_COLOR = { skills: '#ffa657', rules: '#ff7b72', memory: '#b392f0', routines: '#e3b341', mcps: '#7ee787', clients: '#79c0ff' };
// La forma de cada punto dice EN QUÉ ANILLO vive (una forma por capa, sin excepciones);
// el color dice DE QUÉ dominio. El detalle fino (persona, episodio…) va en el tooltip.
// El orden es el de los anillos de afuera hacia adentro: así se lee la leyenda.
const GROUP_NAME = { clients: 'client', mcps: 'mcp', routines: 'routine', memory: 'note', rules: 'rule',
                     skills: 'skill', core: 'core', area: 'domain' };
const KIND_NAME = { infra: 'infra', tool: 'tool', project: 'project', person: 'person', note: 'note',
                    episode: 'episode', decision: 'decision', reference: 'reference' };
// «note · project» para la memoria; el resto basta con su grupo
const typeName = (n) => {
  const g = GROUP_NAME[group(n)] || n.kind;
  return n.layer === 'memory' && KIND_NAME[n.kind] && n.kind !== 'note' ? `${g} · ${KIND_NAME[n.kind]}` : g;
};

let DATA = null, motion = true, labels = false;

/* ---------- utilidades de presentación ---------- */
const nf = (n, d = 0) => n == null ? '—' : Number(n).toLocaleString('en-US', { maximumFractionDigits: d });
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function age(iso) {
  if (!iso) return '—';
  const s = (Date.now() - new Date(iso)) / 1000;
  if (!isFinite(s)) return '—';
  if (s < 60) return `${Math.max(1, Math.round(s))}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
// Un bloque se considera rancio si su lectura quedó muy atrás del ciclo de 2 min.
const stale = (iso, limitMin = 6) => !iso || (Date.now() - new Date(iso)) / 60000 > limitMin;

function box(title, body, mark) {
  const cls = mark && stale(mark) ? 'age stale' : 'age';
  return `<div class="box"><h2>${title}<span class="${cls}">${mark ? age(mark) : ''}</span></h2>${body}</div>`;
}
const row = (k, v, cls = '') => `<div class="row"><span class="k">${k}</span><span class="v ${cls}">${v}</span></div>`;
const daysSince = (ymd) => Math.max(0, Math.round((Date.now() - new Date(ymd + 'T12:00:00')) / 86400000));

/* ---------- barra de estado ---------- */
function paintStatus(d) {
  const e = d.status || {}, c = e.core || {};
  const working = c.state === 'working';
  const bad = (e.routines_total || 0) - (e.routines_ok || 0);
  $('#status').innerHTML = [
    `<span class="pip ${working ? 'warn' : ''}"></span>Core · <b>${working ? 'working' : 'idle'}</b>${working ? ` <small>${esc((c.who || []).join(', '))}</small>` : ''}`,
    `<span class="pip ${bad ? 'bad' : ''}"></span>Routines · <b>${e.routines_total ? `${e.routines_ok}/${e.routines_total} healthy` : '—'}</b>`,
    `<span class="pip"></span>Vault · <b>${e.vault_commit ? age(e.vault_commit) : '—'}</b>`,
    `<span class="pip ${stale(d.generated) ? 'warn' : ''}"></span>Data · <b>${age(d.generated)}</b>`,
  ].map((x) => `<span class="item">${x}</span>`).join('');
}

function paintChips(d) {
  const failed = Object.entries(d).filter(([, v]) => v && typeof v === 'object' && v.ok === false).map(([k]) => k);
  $('#chips').innerHTML = failed.length
    ? `<span class="chip"><i class="dot bad"></i>source down: ${esc(failed.join(', '))}</span>`
    : `<span class="chip"><i class="dot ${stale(d.generated) ? 'stale' : 'fresh'}"></i>data <b>${age(d.generated)}</b></span>`;
}

/* ---------- columna izquierda: la memoria, el MCP y quién le habla ---------- */
function paintLeft(d) {
  const v = d.vault || {}, m = d.mcp || {}, cl = d.clients || {};
  let html = '';

  const folders = Object.entries(v.by_folder || {});
  html += box('Memory', v.ok === false ? `<div class="empty">no reading: ${esc(v.error || '')}</div>` : `
    <div class="figure">${nf(v.notes)}<small>notes</small></div>
    ${folders.length ? folders.map(([f, n]) => row(esc(f), nf(n))).join('') : '<div class="empty">the vault is empty: your agents fill it as they work</div>'}
    ${row('Structure', v.healthy == null ? '—' : v.healthy ? '<span class="ok">healthy</span>' : `<span class="warn">${nf(v.problems)} to review</span>`)}
    ${row('Commits', nf(v.commits))}
    ${row('Latest', `<span style="font-size:10px" title="${esc(v.last_commit?.subject)}">${esc((v.last_commit?.subject || '').slice(0, 34)) || '—'}</span>`)}
  `, v.read_at);

  const skills = (d.graph?.nodes || []).filter((n) => n.kind === 'skill');
  const plugins = {};
  for (const s of skills) plugins[s.plugin] = (plugins[s.plugin] || 0) + 1;
  const up = m.status === 'up';
  html += box('MCP', `
    <div class="figure ${m.status === 'down' ? 'bad' : ''}">${up ? 'up' : m.status === 'down' ? 'down' : '—'}<small>memory server</small></div>
    ${Object.entries(plugins).map(([p, n]) => row(`skills · ${esc(p)}`, nf(n))).join('') || row('skills', '0')}
  `, m.read_at);

  const items = cl.items || [];
  const named = items.filter((c) => c.ip);
  const generic = items.find((c) => !c.ip);
  const clientRow = (c) => `<div class="client"><i class="dot ${c.last_seen && !stale(c.last_seen, 10) ? 'fresh' : 'off'}"></i>
      <span class="name">${esc(c.label)}${c.ip ? `<small>${esc(c.ip)}</small>` : ''}</span>
      <span class="seen">${c.last_seen ? age(c.last_seen) : 'never'}</span></div>`;
  html += box(`Clients <span class="tag">${named.length}</span>`,
    (named.map(clientRow).join('') || '<div class="empty">no named clients yet</div>') +
    (generic ? clientRow(generic) : '') +
    (generic?.unnamed_ips?.length ? `<div class="hint">unnamed: ${esc(generic.unnamed_ips.join(', '))} — name them in setup</div>` : ''),
    cl.read_at);

  $('#left').innerHTML = html;
}

/* ---------- columna derecha: lo que espera y lo que corre solo ---------- */
const routineBad = (i) => i.healthy === false;

// Segunda línea de cada rutina: lo que dijo en su última corrida
function logLine(l) {
  if (!l || !l.summary) return '';
  return `<div class="result ${l.ok === false ? 'bad' : ''}" title="${esc(l.summary)}">${esc(l.summary)}</div>`;
}

/* ---------- PENDING: lo que espera al dueño, del más viejo al más nuevo ---------- */
let showAll = false;
const KINDS = ['vault', 'routine', 'token', 'review'];
function pendingBox(d) {
  const p = d.pending || {}, items = p.items || [], counts = p.counts || {};
  // «review» (hechos que quizá quedaron viejos) no cuenta como algo que te espera: va aparte en la leyenda
  const waiting = items.filter((i) => i.kind !== 'review').length;
  const visible = showAll ? items : items.slice(0, 8);
  const list = visible.map((i) => `<div class="pending ${i.path || i.routine ? 'open' : ''}"${i.path ? ` data-path="${esc(i.path)}"` : ''}${i.routine ? ` data-routine="${esc(i.routine)}"` : ''} title="${esc(i.text)}">
      <span class="kind ${esc(i.kind)}">${esc(i.kind)}</span><span class="text">${esc(i.text)}</span>
      <span class="days">${i.date ? daysSince(i.date) + 'd' : ''}</span></div>`).join('');
  const more = items.length > 8 ? `<div class="more" id="showAll">${showAll ? 'show less' : `+${items.length - 8} more`}</div>` : '';
  const failures = (p.failures || []).length ? `<div class="empty">source down: ${esc(p.failures.join(', '))}</div>` : '';
  // propuestas de dominios del consolidador: se resuelven en /setup/domains
  const proposals = (p.domain_proposals || []).map((x) => `<a class="pending open" href="/setup/domains" title="${esc(x.text)}"><span class="kind domain">domain</span>
      <span class="text">${esc(x.text || x.id)}</span><span class="days"></span></a>`).join('');
  // un conteo null = esa fuente cayó: se muestra «?» y no ocupa barra (nunca un cero inventado)
  return box('Pending', p.ok === false ? `<div class="empty">no reading: ${esc(p.error || '')}</div>` : `
    <div class="figure ${waiting ? 'warn' : ''}">${waiting}<small>waiting on you</small></div>
    ${list || '<div class="empty">nothing waiting</div>'}${more}${proposals}${failures}
    <div class="kindBar">${KINDS.map((t) => `<i class="${t}" style="flex:${counts[t] ?? 0}"></i>`).join('')}</div>
    <div class="kindLegend">${KINDS.map((t) => `${counts[t] ?? '?'} ${t.toUpperCase()}`).join(' · ')}</div>`, p.read_at);
}

const STATE_LABEL = { disabled: 'off', 'no-credential': 'no credential', 'invalid-schedule': 'invalid schedule', ready: '', unknown: '?' };
function paintRight(d) {
  const r = d.routines || {};
  let html = pendingBox(d);
  const items = r.items || [];
  const LOGS = new Map((d.logs?.items || []).map((l) => [l.routine, l]));
  html += box(`Routines <span class="tag">${items.length}</span>`, items.length ? items.map((i) => {
    const bad = routineBad(i), running = RUNNING.has('routine:' + i.name), off = i.state && i.state !== 'ready';
    const label = STATE_LABEL[i.state] ?? i.state;
    return `<div class="routine" data-routine="${esc(i.name)}">
      <i class="dot ${bad ? 'bad' : off ? 'off' : ''} ${running ? 'running' : ''}"></i>
      <span class="name" title="${esc(i.schedule || '')}">${esc(i.name)}${i.needs_claude ? ' <span class="tag">$</span>' : ''}${running ? ' <span class="tag warn">running</span>' : ''}${label ? ` <span class="tag">${esc(label)}</span>` : ''}</span>
      <span class="when ${bad ? 'bad' : ''}">${i.last_ts ? age(i.last_ts) : 'never ran'}</span>
      ${logLine(LOGS.get(i.name))}
    </div>`;
  }).join('') : '<div class="empty">no data</div>', r.read_at);

  $('#right').innerHTML = html;
  $('#right').querySelectorAll('.routine[data-routine]').forEach((el) => el.onclick = () => openLog(el.dataset.routine));
  $('#right').querySelectorAll('.pending[data-path]').forEach((el) => el.onclick = () => openNote({ path: el.dataset.path }));
  $('#right').querySelectorAll('.pending[data-routine]').forEach((el) => el.onclick = () => openLog(el.dataset.routine));
  const sa = $('#showAll');
  if (sa) sa.onclick = () => { showAll = !showAll; paintRight(DATA); };
}

/* =====================================================================
 *  EL GRAFO
 * ===================================================================== */
const canvas = $('#canvas'), ctx = canvas.getContext('2d');
let W = 0, H = 0, R = 0;
let NODES = [], BYID = new Map(), EDGES = [], NEIGHBOURS = new Map();
// Pulsos REALES: cada uno es una lectura, escritura, consulta o corrida que pasó de verdad.
let PULSES = [], SCHEDULED = [], FLASHES = [], SEEN = new Set(), LAST = {}, haloThreshold = 99;
const POS = new Map();          // id -> {x, y, a, s, z, from, t0, delay}
let view = 'rings', viewT0 = 0, LAY = null;
let clock = 0, lastFrame = performance.now(), dirty = true;
let cam = { k: 1, x: 0, y: 0 }, camT = { k: 1, x: 0, y: 0 }, follow = null;
let hover = null, chosen = null, filter = null;   // filter = {type:'area'|'group', value}
let cloudAlpha = 1;

const easeIO = (t) => t <= 0 ? 0 : t >= 1 ? 1 : t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const size = (n) => {
  if (n.kind === 'area') return 7;
  if (n.layer === 'clients') return CONFIG.sizeClient;
  if (n.layer === 'mcps') return CONFIG.sizeMcp;
  if (n.layer === 'memory' && n.bytes)
    return Math.min(CONFIG.sizeNoteMax, Math.max(CONFIG.sizeNoteMin, Math.sqrt(n.bytes) * CONFIG.sizePerByte));
  return Math.min(CONFIG.sizeMax, CONFIG.sizeBase + Math.sqrt(n.degree || 0) * CONFIG.sizePerDegree);
};
const group = (n) => n.kind === 'area' ? 'area' : n.layer;

// Generador determinista: el layout no baila entre recargas
function seeded(seed) {
  let s = 0;
  for (const c of String(seed)) s = (s * 31 + c.charCodeAt(0)) | 0;
  return () => { s = (s * 1664525 + 1013904223) | 0; return ((s >>> 0) % 100000) / 100000; };
}

/* ---------- preparar datos ---------- */
function prepareGraph(g) {
  NODES = (g.nodes || []).filter((n) => n.kind && n.layer);
  BYID = new Map(NODES.map((n) => [n.id, n]));
  EDGES = (g.edges || []).filter(([a, b]) => BYID.has(a) && BYID.has(b));
  // Un hub por dominio: no existe en el vault, se deriva acá para que el dominio se
  // pueda tocar, abrir y seguir como cualquier nodo.
  const root = BYID.get('AGENTS') || NODES.find((n) => n.layer === 'core');
  for (const a of AREAS()) {
    const id = 'area:' + a, members = NODES.filter((n) => n.area === a && n.kind !== 'area');
    const count = (layer) => members.filter((n) => n.layer === layer).length;
    const hub = { id, kind: 'area', layer: 'hub', area: a, label: a, degree: 0, changed: '',
                  path: `domain · ${(g.areas || {})[a] ?? members.length} nodes`,
                  summary: `${count('skills')} skills, ${count('rules')} rules, ${count('memory')} notes, ` +
                           `${count('routines')} routines, ${count('mcps')} mcps and ${count('clients')} clients.` };
    NODES.push(hub); BYID.set(id, hub);
    for (const m of members) if (m.layer !== 'core') EDGES.push([id, m.id, 'hub']);
    if (root) EDGES.push([root.id, id, 'hub']);
  }
  NEIGHBOURS = new Map(NODES.map((n) => [n.id, new Set()]));
  for (const [a, b] of EDGES) { NEIGHBOURS.get(a).add(b); NEIGHBOURS.get(b).add(a); }
  const routines = DATA?.routines?.items || [];
  // halo para los ~8 nodos más conectados, sean cuantos sean los nodos
  haloThreshold = NODES.filter((n) => n.kind !== 'area').map((n) => n.degree || 0).sort((a, b) => b - a)[7] ?? 99;
  for (const n of NODES) {
    if (n.kind === 'routine') {
      const it = routines.find((i) => 'routine:' + i.name === n.id);
      n.bad = it ? routineBad(it) : !!n.bad;
      n.info = it;
    }
    if (!POS.has(n.id)) POS.set(n.id, { x: 0, y: 0, a: 0, s: 1, z: 0, from: { x: 0, y: 0, a: 0, s: 1 }, t0: -1e9, delay: 0 });
  }
  for (const id of [...POS.keys()]) if (!BYID.has(id)) POS.delete(id);
}

/* ---------- las seis disposiciones ----------
 * Cada una devuelve {pos(n, t) -> {x,y,a,s,z}, decor(alpha, t), order(n) -> 0..1}.
 * pos depende del tiempo para las vistas que se mueven solas (anillos, órbita). */

function layoutRings() {
  const areas = AREAS(), rad = CONFIG.radii;
  // Un sector por dominio, del mismo ancho en todos los anillos para que se lea radial.
  // Los clientes no tienen dominio: reparten su anillo completo.
  const weight = areas.map((a) => Math.pow(NODES.filter((n) => n.area === a && n.layer !== 'core' && n.layer !== 'clients').length, 0.8) + 2);
  const total = weight.reduce((x, y) => x + y, 0), free = Math.PI * 2 - CONFIG.sectorGap * areas.length;
  const sector = {};
  let ang = -Math.PI / 2 + CONFIG.sectorGap / 2;
  areas.forEach((a, i) => { const w = free * weight[i] / total; sector[a] = [ang, ang + w]; ang += w + CONFIG.sectorGap; });

  const base = new Map();
  const step = CONFIG.step;
  // Banda: reparte la lista en filas que llenan TODO el ancho de [r0, r1].
  function band(list, a0, a1, r0, r1) {
    const c = list.length; if (!c) return;
    const m = 0.04, w = Math.max(0.01, a1 - a0 - 2 * m), rm = (r0 + r1) / 2;
    const d = Math.max(step * 0.8, Math.sqrt((w * rm * (r1 - r0)) / c));
    const rows = Math.max(1, Math.min(Math.round((r1 - r0) / d), c));
    const perRow = Math.ceil(c / rows);
    list.forEach((n, i) => {
      const f = Math.floor(i / perRow), j = i % perRow, inRow = Math.min(perRow, c - f * perRow);
      const r = rows === 1 ? rm : r0 + (f / (rows - 1)) * (r1 - r0);
      const an = inRow === 1 ? (a0 + a1) / 2 : a0 + m + (j / (inRow - 1)) * w;
      base.set(n.id, { ang: an + (f % 2 ? 0.5 : 0) * (w / Math.max(1, inRow - 1)) * (j < inRow - 1 ? 1 : 0), r });
    });
  }
  // Grilla: filas que son arcos concéntricos con el MISMO paso en todos los sectores,
  // llenando desde r0 hacia afuera. Solo si un dominio no cabe hasta r1 se aprieta el
  // paso. Devuelve el radio de la última fila.
  function grid(list, a0, a1, r0, r1, d0 = step, simulate = false) {
    const c = list.length; if (!c) return r0;
    const m = 0.04, w = Math.max(0.01, a1 - a0 - 2 * m), mid = (a0 + a1) / 2;
    const rowsFor = (d) => {
      const F = [];
      for (let i = 0, r = r0; i < c; r += d) {
        const n = Math.min(c - i, Math.floor((w * r) / d) + 1);
        F.push({ r, n }); i += n;
      }
      return F;
    };
    let d = d0, F = rowsFor(d);
    while (F[F.length - 1].r > r1 && d > step * 0.55) { d *= 0.92; F = rowsFor(d); }
    if (simulate) return F[F.length - 1].r;
    let i = 0;
    for (const f of F) {
      const start = mid - ((f.n - 1) * d) / f.r / 2;   // cada fila centrada en su sector
      for (let j = 0; j < f.n; j++, i++) base.set(list[i].id, { ang: start + (j * d) / f.r, r: f.r });
    }
    return F[F.length - 1].r;
  }
  // El paso de la memoria es UNO para todos los dominios: el mayor con el que el dominio
  // más profundo todavía cabe en la banda.
  const memOf = (a) => NODES.filter((n) => n.area === a && n.layer === 'memory');
  const deepest = (d) => Math.max(R * rad.memIn, ...areas.map((a) => grid(memOf(a), ...sector[a], R * rad.memIn, Infinity, d, true)));
  let memStep = step;
  for (let d = step * CONFIG.memStepMax; d > step; d *= 0.96) if (deepest(d) <= R * rad.memOut) { memStep = d; break; }
  // Con el paso apretado la memoria no llena su banda: se centra en ella.
  const memR0 = R * rad.memIn + Math.max(0, R * (rad.memOut - rad.memIn) - (deepest(memStep) - R * rad.memIn)) / 2;
  const sortList = (l) => l.sort((x, y) => (y.degree || 0) - (x.degree || 0) || x.id.localeCompare(y.id));
  let memEnd = memR0, skEnd = R * rad.skills, ruEnd = R * rad.rules;
  for (const a of areas) {
    const [a0, a1] = sector[a];
    const of = (layer) => sortList(NODES.filter((n) => n.area === a && n.layer === layer));
    skEnd = Math.max(skEnd, grid(of('skills'), a0, a1, R * rad.skills, R * (rad.rules - 0.035)));
    ruEnd = Math.max(ruEnd, grid(of('rules'), a0, a1, R * rad.rules, R * (rad.hubs - 0.03)));
    memEnd = Math.max(memEnd, grid(of('memory'), a0, a1, memR0, R * rad.memOut, memStep));
    // las órbitas sí reparten sus nodos a lo largo de la línea
    band(of('routines'), a0, a1, R * (rad.routines - 0.02), R * (rad.routines + 0.02));
    band(of('mcps'), a0, a1, R * (rad.mcps - 0.015), R * (rad.mcps + 0.015));
  }
  const clients = sortList(NODES.filter((n) => n.layer === 'clients'));
  clients.forEach((n, i) => base.set(n.id, { ang: -Math.PI / 2 + (i + 0.5) * (Math.PI * 2 / clients.length), r: R * rad.clients }));
  NODES.filter((n) => n.layer === 'core').forEach((n, i) => base.set(n.id, { ang: i * 2.1 - 1.2, r: R * CONFIG.coreR * 0.45 }));
  const rHub = (R * rad.hubs + memR0 - R * 0.03) / 2;
  for (const a of areas) base.set('area:' + a, { ang: (sector[a][0] + sector[a][1]) / 2, r: rHub });
  const memBand = [rHub - R * 0.04, memEnd + R * 0.045];
  const air = R * 0.016;
  const bands = [['skills', R * rad.skills - air, skEnd + air], ['rules', R * rad.rules - air, ruEnd + air],
                 ['memory', ...memBand]];

  const spin = () => clock * CONFIG.spinRings;   // clock solo avanza con motion encendido
  return {
    pos(n) {
      const b = base.get(n.id) || { ang: 0, r: 0 };
      const a = b.ang + spin();
      return { x: Math.cos(a) * b.r, y: Math.sin(a) * b.r, a: 1, s: 1, z: 0 };
    },
    order(n) { const b = base.get(n.id); return b ? ((b.ang + Math.PI / 2) / (Math.PI * 2) + 1) % 1 : 0; },
    decor(alpha) {
      const g = spin();
      ctx.save(); ctx.globalAlpha = alpha;
      // línea solo donde los nodos VIVEN sobre ella (las órbitas)
      ctx.strokeStyle = 'rgba(140,160,190,.10)'; ctx.lineWidth = 1 / cam.k;
      for (const [layer, r] of [['routines', rad.routines], ['mcps', rad.mcps], ['clients', rad.clients]]) {
        ctx.beginPath(); ctx.arc(0, 0, R * r, 0, Math.PI * 2); ctx.stroke();
        tag(LAYER_NAME[layer].toUpperCase(), 0, -R * r, LAYER_COLOR[layer]);
      }
      // bandas apenas insinuadas: relleno y bordes muy tenues, el nombre en el borde superior
      for (const [layer, b0, b1] of bands) {
        const rgb = hexRgb(LAYER_COLOR[layer]);
        ctx.beginPath(); ctx.arc(0, 0, b1, 0, Math.PI * 2); ctx.arc(0, 0, b0, 0, Math.PI * 2, true);
        ctx.fillStyle = `rgba(${rgb},.022)`; ctx.fill();
        ctx.strokeStyle = `rgba(${rgb},.06)`;
        ctx.beginPath(); ctx.arc(0, 0, b1, 0, Math.PI * 2); ctx.stroke();
        ctx.beginPath(); ctx.arc(0, 0, b0, 0, Math.PI * 2); ctx.stroke();
      }
      for (const [layer, , b1] of bands) tag(LAYER_NAME[layer].toUpperCase(), 0, -b1, LAYER_COLOR[layer]);
      ctx.strokeStyle = 'rgba(140,160,190,.10)';
      ctx.beginPath(); ctx.arc(0, 0, R * (rad.clients + 0.045), 0, Math.PI * 2);
      ctx.setLineDash([2 / cam.k, 6 / cam.k]); ctx.stroke(); ctx.setLineDash([]);
      for (const a of areas) {
        const [a0, a1] = sector[a];
        // arco de color bajo el sector, afuera del todo
        ctx.strokeStyle = color(a); ctx.globalAlpha = alpha * 0.5; ctx.lineWidth = 2 / cam.k;
        ctx.beginPath(); ctx.arc(0, 0, R * (rad.clients + 0.045), a0 + g, a1 + g); ctx.stroke();
        ctx.globalAlpha = alpha;
      }
      ctx.restore();
    },
  };
}

function layoutCircle() {
  const areas = AREAS(), seq = [];
  for (const a of areas) for (const layer of LAYERS) {
    seq.push(...NODES.filter((n) => n.area === a && n.layer === layer && layer !== 'core')
      .sort((x, y) => x.label.localeCompare(y.label)));
  }
  const gap = 0.05, free = Math.PI * 2 - gap * areas.length, step = free / Math.max(1, seq.length);
  const base = new Map(), arcs = {};
  let ang = -Math.PI / 2, prev = null;
  for (const n of seq) {
    if (prev && prev !== n.area) ang += gap;
    base.set(n.id, ang);
    (arcs[n.area] ||= [ang, ang]); arcs[n.area][1] = ang;
    ang += step; prev = n.area;
  }
  const r = R * 0.86;
  const cores = NODES.filter((n) => n.layer === 'core');
  const hubs = new Map(Object.entries(arcs).map(([a, [a0, a1]]) => [`area:${a}`, (a0 + a1) / 2]));
  return {
    pos(n) {
      if (hubs.has(n.id)) { const m = hubs.get(n.id); return { x: Math.cos(m) * r * 0.86, y: Math.sin(m) * r * 0.86, a: 1, s: 1, z: 0 }; }
      const b = base.get(n.id);
      if (b == null) { const i = Math.max(0, cores.indexOf(n)); return { x: Math.cos(i * 2.1) * 8, y: Math.sin(i * 2.1) * 8, a: 1, s: 1, z: 0 }; }
      return { x: Math.cos(b) * r, y: Math.sin(b) * r, a: 1, s: 1, z: 0 };
    },
    order(n) { const b = base.get(n.id); return b == null ? 0 : ((b + Math.PI / 2) / (Math.PI * 2)) % 1; },
    curved: true,
    decor(alpha) {
      ctx.save(); ctx.globalAlpha = alpha;
      for (const [a, [a0, a1]] of Object.entries(arcs)) {
        ctx.strokeStyle = color(a); ctx.lineWidth = 3 / cam.k; ctx.globalAlpha = alpha * 0.7;
        ctx.beginPath(); ctx.arc(0, 0, r + 9, a0 - 0.005, a1 + 0.005); ctx.stroke();
        const m = (a0 + a1) / 2, lr = r + 24;
        ctx.globalAlpha = alpha;
        text(a.toUpperCase(), Math.cos(m) * lr, Math.sin(m) * lr + 3,
             { size: 9, color: color(a), align: Math.cos(m) > 0.2 ? 'left' : Math.cos(m) < -0.2 ? 'right' : 'center', spacing: 2 });
      }
      ctx.restore();
    },
  };
}

function layoutDomains() {
  const areas = AREAS(), centres = {}, base = new Map();
  const count = (a) => NODES.filter((n) => n.area === a && n.layer !== 'core').length;
  const rc = areas.length > 1 ? R * 0.6 : R * 0.3;
  areas.forEach((a, i) => {
    const ang = (i / areas.length) * Math.PI * 2 - Math.PI / 2;
    centres[a] = { x: Math.cos(ang) * rc, y: Math.sin(ang) * rc, r: Math.sqrt(count(a)) * CONFIG.step * 0.62 };
  });
  for (const a of areas) {
    // girasol: lo más conectado al medio del racimo, rutinas y clientes afuera
    const list = NODES.filter((n) => n.area === a && n.layer !== 'core')
      .sort((x, y) => LAYERS.indexOf(x.layer) - LAYERS.indexOf(y.layer) || (y.degree || 0) - (x.degree || 0));
    list.forEach((n, i) => {
      const t = i * 2.399963, r = Math.sqrt(i + 0.5) * CONFIG.step * 0.62;
      base.set(n.id, { x: centres[a].x + Math.cos(t) * r, y: centres[a].y + Math.sin(t) * r });
    });
  }
  NODES.filter((n) => n.layer === 'core').forEach((n, i) => base.set(n.id, { x: Math.cos(i * 2.1) * 8, y: Math.sin(i * 2.1) * 8 }));
  return {
    base, centres,
    pos(n) { const b = base.get(n.id) || { x: 0, y: 0 }; return { x: b.x, y: b.y, a: 1, s: 1, z: 0 }; },
    order(n) { return (Math.max(0, areas.indexOf(n.area)) + 0.5) / Math.max(1, areas.length); },
    decor(alpha) {
      ctx.save(); ctx.globalAlpha = alpha;
      for (const a of areas) {
        const c = centres[a];
        ctx.strokeStyle = 'rgba(140,160,190,.07)'; ctx.lineWidth = 1 / cam.k;
        ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(c.x, c.y); ctx.stroke();
      }
      ctx.restore();
    },
  };
}

function layoutLinks() {
  // Fuerzas a mano (sin librerías): repulsión entre todos, resortes en los enlaces,
  // gravedad al centro y un tirón suave hacia el racimo de su dominio.
  const start = layoutDomains(), P = NODES.map((n) => {
    const b = start.base.get(n.id) || { x: 0, y: 0 }, rnd = seeded(n.id);
    return { n, x: b.x * 0.7 + (rnd() - 0.5) * 20, y: b.y * 0.7 + (rnd() - 0.5) * 20, vx: 0, vy: 0 };
  });
  const idx = new Map(P.map((p, i) => [p.n.id, i]));
  // [i, j, largo de reposo, rigidez]: los enlaces de hub son más largos y blandos
  const L = EDGES.map(([a, b, hub]) => [idx.get(a), idx.get(b), hub ? 46 : 34, hub ? 0.03 : 0.035]);
  for (let it = 0; it < CONFIG.forceIter; it++) {
    const alpha = 1 - it / CONFIG.forceIter;
    for (const p of P) { p.fx = 0; p.fy = 0; }
    for (let i = 0; i < P.length; i++) {
      for (let j = i + 1; j < P.length; j++) {
        let dx = P[j].x - P[i].x, dy = P[j].y - P[i].y, d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = 0.1; dy = 0.1; d2 = 0.02; }
        const d = Math.sqrt(d2), f = 1400 / Math.max(d2, 36);
        P[i].fx -= dx / d * f; P[i].fy -= dy / d * f; P[j].fx += dx / d * f; P[j].fy += dy / d * f;
      }
    }
    for (const [a, b, rest, k] of L) {
      const dx = P[b].x - P[a].x, dy = P[b].y - P[a].y, d = Math.sqrt(dx * dx + dy * dy) || 0.1;
      const f = (d - rest) * k;
      P[a].fx += dx / d * f; P[a].fy += dy / d * f; P[b].fx -= dx / d * f; P[b].fy -= dy / d * f;
    }
    for (const p of P) {
      const c = start.centres[p.n.area] || { x: 0, y: 0 };
      p.fx += (c.x * 0.75 - p.x) * 0.016 - p.x * 0.003;
      p.fy += (c.y * 0.75 - p.y) * 0.016 - p.y * 0.003;
      if (p.n.layer === 'core') { p.fx -= p.x * 0.3; p.fy -= p.y * 0.3; }
      p.vx = (p.vx + p.fx * alpha) * 0.55; p.vy = (p.vy + p.fy * alpha) * 0.55;
      const v = Math.hypot(p.vx, p.vy);
      if (v > 18) { p.vx *= 18 / v; p.vy *= 18 / v; }
      p.x += p.vx; p.y += p.vy;
    }
  }
  const ext = Math.max(1, ...P.map((p) => Math.max(Math.abs(p.x), Math.abs(p.y))));
  const k = (R * 0.95) / ext, base = new Map(P.map((p) => [p.n.id, { x: p.x * k, y: p.y * k, f: seeded(p.n.id)() * 6.28 }]));
  return {
    pos(n) {
      const b = base.get(n.id) || { x: 0, y: 0, f: 0 };
      const w = clock * 0.0011 + b.f;   // respiración mínima, para que se sienta vivo
      return { x: b.x + Math.cos(w) * 1.4, y: b.y + Math.sin(w * 1.3) * 1.4, a: 1, s: 1, z: 0 };
    },
    order(n) { const b = base.get(n.id); return b ? Math.min(1, Math.hypot(b.x, b.y) / R) : 0; },
    decor() {},
  };
}

function layoutTimeline() {
  // UN eje de tiempo para todo: arriba las corridas de cada rutina, una fila por
  // rutina; abajo las notas, colgando del eje en el día de su último cambio.
  // Los clientes no tienen fecha propia: se apilan a la izquierda.
  const left = -W / 2 + 150, right = W / 2 - 40, top = -H / 2 + 46, mid = -H * 0.02, bottom = H / 2 - 30;
  const DAY = 86400000, today = new Date(); today.setHours(0, 0, 0, 0);
  const dayMs = (iso) => new Date(iso.slice(0, 10) + 'T00:00:00').getTime();
  const routines = NODES.filter((n) => n.layer === 'routines').sort((a, b) => a.label.localeCompare(b.label));
  const runs = DATA?.graph?.runs || [];
  const withRuns = new Set(runs.map((c) => c.routine));
  const dated = NODES.filter((n) => n.changed && n.layer !== 'routines' && n.layer !== 'clients' && n.kind !== 'area');
  const t0 = Math.min(today.getTime() - 14 * DAY, ...dated.map((n) => dayMs(n.changed)), ...runs.map((c) => dayMs(c.day)));
  const t1 = today.getTime() + 2 * DAY;
  const xOf = (ms) => left + ((ms - t0) / (t1 - t0)) * (right - left);
  const rowH = Math.min(16, (mid - top - 10) / Math.max(1, routines.length));
  const rowY = (i) => top + 10 + (i + 0.5) * rowH;
  const base = new Map();
  routines.forEach((n, i) => base.set(n.id, { x: left - 14, y: rowY(i) }));

  dated.sort((a, b) => AREAS().indexOf(a.area) - AREAS().indexOf(b.area) || (b.degree || 0) - (a.degree || 0));
  const stacks = new Map();
  for (const n of dated) { const k = dayMs(n.changed); stacks.set(k, (stacks.get(k) || 0) + 1); }
  const step = Math.min(CONFIG.step, (bottom - mid - 16) / Math.max(1, ...stacks.values()));
  const used = new Map();
  for (const n of dated) {
    const k = dayMs(n.changed), i = used.get(k) || 0; used.set(k, i + 1);
    base.set(n.id, { x: xOf(k + DAY / 2), y: mid + 12 + i * step });
  }
  NODES.filter((n) => n.layer === 'clients').forEach((n, i) =>
    base.set(n.id, { x: -W / 2 + 22 + (i % 7) * 13, y: mid + 52 + Math.floor(i / 7) * 13 }));
  const xToday = xOf(today.getTime() + DAY / 2);
  return {
    pos(n) {
      const b = base.get(n.id);
      if (!b) return { x: left - 60, y: mid, a: 0, s: 0.5, z: 0 };
      return { x: b.x, y: b.y, a: n.layer === 'clients' ? 0.5 : 1, s: n.layer === 'clients' ? 0.75 : 1, z: 0 };
    },
    order(n) { const b = base.get(n.id); return b ? Math.max(0, (b.x - left) / (right - left)) : 1; },
    noCloud: true, noEdges: true,
    decor(alpha) {
      ctx.save(); ctx.globalAlpha = alpha;
      // semanas: líneas verticales tenues de arriba abajo, con su lunes rotulado
      ctx.lineWidth = 1 / cam.k;
      const monday = new Date(t0); monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
      for (let ms = monday.getTime(); ms <= t1; ms += 7 * DAY) {
        const x = xOf(ms);
        if (x < left) continue;
        ctx.strokeStyle = 'rgba(140,160,190,.07)';
        ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
        const d = new Date(ms);
        text(`mon ${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}`, x + 3 / cam.k, top - 4, { size: 9, color: 'rgba(160,175,200,.5)' });
      }
      // hoy: la única línea con color
      ctx.strokeStyle = 'rgba(255,122,47,.75)'; ctx.lineWidth = 1.2 / cam.k;
      ctx.beginPath(); ctx.moveTo(xToday, top - 14); ctx.lineTo(xToday, bottom); ctx.stroke();
      text('today', xToday, top - 18, { size: 9, color: CORE_COLOR, align: 'center' });
      // eje medio, que separa corridas de notas
      ctx.strokeStyle = 'rgba(140,160,190,.16)';
      ctx.beginPath(); ctx.moveTo(-W / 2 + 16, mid); ctx.lineTo(right, mid); ctx.stroke();
      const x0 = -W / 2 + 16;
      text('ROUTINES · runs per day', x0, top - 4, { size: 9, color: LAYER_COLOR.routines, spacing: 1 });
      text('NOTES · by last change', x0, mid + 16, { size: 9, color: LAYER_COLOR.memory, spacing: 1 });
      text('clients · undated', x0, mid + 36, { size: 8.5, color: 'rgba(121,192,255,.6)' });
      const maxC = Math.max(1, ...runs.map((c) => c.ok + c.fail));
      routines.forEach((n, i) => {
        const y = rowY(i), name = n.label;
        text(n.label, left - 26, y + 3 / cam.k, { size: 9.5, color: n.bad ? '#f85149' : 'rgba(220,226,232,.78)', align: 'right' });
        ctx.strokeStyle = 'rgba(255,255,255,.03)'; ctx.lineWidth = 1 / cam.k;
        ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
        if (!withRuns.has(name)) {
          text('no runs yet', right, y + 3 / cam.k, { size: 8, color: 'rgba(210,153,34,.55)', align: 'right' });
          return;
        }
        for (const c of runs) {
          if (c.routine !== name) continue;
          const x = xOf(dayMs(c.day) + DAY / 2), r = 1.4 + Math.sqrt((c.ok + c.fail) / maxC) * rowH * 0.36;
          ctx.fillStyle = color(n.area); ctx.globalAlpha = alpha * 0.8;
          ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
          if (c.fail) { ctx.strokeStyle = '#f85149'; ctx.globalAlpha = alpha; ctx.lineWidth = 1.4 / cam.k; ctx.stroke(); }
          ctx.globalAlpha = alpha;
        }
      });
      ctx.restore();
    },
  };
}

function layoutOrbit() {
  // Esfera de Fibonacci ordenada por dominio: cada dominio queda en una banda.
  const body = [];
  for (const a of AREAS()) for (const layer of ['skills', 'rules', 'memory', 'routines', 'mcps'])
    body.push(...NODES.filter((n) => n.area === a && n.layer === layer));
  const N = body.length, base = new Map(), Rs = R * 0.62;
  body.forEach((n, i) => {
    const y = 1 - (i + 0.5) / N * 2, rr = Math.sqrt(1 - y * y), th = i * 2.399963;
    base.set(n.id, { x: Math.cos(th) * rr, y, z: Math.sin(th) * rr });
  });
  for (const a of AREAS()) {
    const m = body.filter((n) => n.area === a).map((n) => base.get(n.id));
    if (!m.length) continue;
    const c = m.reduce((s, p) => ({ x: s.x + p.x, y: s.y + p.y, z: s.z + p.z }), { x: 0, y: 0, z: 0 });
    const l = Math.hypot(c.x, c.y, c.z) || 1;
    base.set('area:' + a, { x: c.x / l * 1.1, y: c.y / l * 1.1, z: c.z / l * 1.1 });
  }
  const clients = NODES.filter((n) => n.layer === 'clients');
  clients.forEach((n, i) => base.set(n.id, { orbit: (i / clients.length) * Math.PI * 2 }));
  const cores = NODES.filter((n) => n.layer === 'core');
  const tilt = 0.38, focus = 2.6;
  const project = (x, y, z) => {
    const yy = y * Math.cos(tilt) - z * Math.sin(tilt), zz = y * Math.sin(tilt) + z * Math.cos(tilt);
    const p = focus / (focus + zz);
    return { x: x * p, y: yy * p, z: zz, p };
  };
  return {
    pos(n) {
      const b = base.get(n.id);
      if (!b) { const i = Math.max(0, cores.indexOf(n)); return { x: Math.cos(i * 2.1) * 8, y: Math.sin(i * 2.1) * 8, a: 1, s: 1, z: -0.1 }; }
      if (b.orbit != null) {
        const a = b.orbit + clock * CONFIG.spinClients, rr = 1.48;
        const q = project(Math.cos(a) * rr, 0.18, Math.sin(a) * rr);
        return { x: q.x * Rs, y: q.y * Rs, a: 0.45 + 0.55 * (1 - (q.z + 1.5) / 3), s: q.p, z: q.z };
      }
      const g = clock * CONFIG.spinOrbit, c = Math.cos(g), s = Math.sin(g);
      const q = project(b.x * c - b.z * s, b.y, b.x * s + b.z * c);
      return { x: q.x * Rs, y: q.y * Rs, a: 0.25 + 0.75 * (1 - (q.z + 1) / 2), s: q.p, z: q.z };
    },
    order(n) { const b = base.get(n.id); return b && b.y != null ? (1 - b.y) / 2 : 1; },
    depth: true,
    decor(alpha) {
      ctx.save(); ctx.globalAlpha = alpha * 0.8;
      ctx.strokeStyle = 'rgba(140,160,190,.09)'; ctx.lineWidth = 1 / cam.k;
      ctx.beginPath(); ctx.arc(0, 0, Rs * 0.98, 0, Math.PI * 2); ctx.stroke();
      // meridianos y la órbita de los clientes, como elipses
      for (const k of [0.35, 0.75]) { ctx.beginPath(); ctx.ellipse(0, 0, Rs * k, Rs, 0, 0, Math.PI * 2); ctx.stroke(); }
      const o = project(1.48, 0.18, 0), o2 = project(0, 0.18, 1.48), o3 = project(0, 0.18, -1.48);
      ctx.strokeStyle = 'rgba(140,160,190,.16)';
      ctx.beginPath(); ctx.ellipse(0, (o2.y + o3.y) / 2 * Rs, o.x * Rs, Math.abs(o2.y - o3.y) / 2 * Rs, 0, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();
    },
  };
}

const LAYOUTS = { rings: layoutRings, circle: layoutCircle, domains: layoutDomains,
                  links: layoutLinks, timeline: layoutTimeline, orbit: layoutOrbit };

/* ---------- transición: cada punto parte de donde está ---------- */
function changeView(v, animate = true) {
  const now = performance.now();
  for (const n of NODES) {
    const e = POS.get(n.id);
    e.from = { x: e.x, y: e.y, a: e.a, s: e.s };
    e.t0 = animate ? now : -1e9;
  }
  view = v; viewT0 = animate ? now : -1e9;
  LAY = LAYOUTS[v]();
  for (const n of NODES) POS.get(n.id).delay = LAY.order(n) * CONFIG.stagger;
  dirty = true;
}

/* ---------- dibujo ---------- */
function text(t, x, y, { size: sz = 10, color: c = '#c9d1d9', align = 'left', spacing = 0, weight = 400 } = {}) {
  ctx.save();
  ctx.font = `${weight} ${sz / cam.k}px ui-monospace, "SF Mono", Menlo, monospace`;
  ctx.fillStyle = c; ctx.textAlign = align;
  if (spacing && 'letterSpacing' in ctx) ctx.letterSpacing = `${spacing / cam.k}px`;
  ctx.fillText(t, x, y);
  ctx.restore();
}

// Nombre de anillo: texto en color sobre una caja oscura, para que se lea encima de los puntos
function tag(t, x, y, c) {
  ctx.save();
  ctx.font = `600 ${9 / cam.k}px ui-monospace, "SF Mono", Menlo, monospace`;
  if ('letterSpacing' in ctx) ctx.letterSpacing = `${1.5 / cam.k}px`;
  const w = ctx.measureText(t).width + 10 / cam.k, h = 14 / cam.k;
  ctx.fillStyle = 'rgba(11,13,16,.88)'; ctx.fillRect(x - w / 2, y - h / 2, w, h);
  ctx.fillStyle = c; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(t, x, y + 0.5 / cam.k);
  ctx.restore();
}

// Única fuente de las formas: dibuja en el grafo y, con otro contexto, en la leyenda.
function shape(n, x, y, r, target = ctx) {
  const ctx = target, g = group(n), c = color(n.area);
  ctx.fillStyle = c; ctx.strokeStyle = c;
  ctx.lineWidth = Math.max(0.8, r * 0.3);
  ctx.beginPath();
  if (g === 'skills' || g === 'rules') {
    const k = g === 'skills' ? 1.35 : 1.2;
    ctx.moveTo(x, y - r * k); ctx.lineTo(x + r * k * 0.85, y); ctx.lineTo(x, y + r * k); ctx.lineTo(x - r * k * 0.85, y);
    ctx.closePath();
    if (g === 'rules') { ctx.lineWidth = Math.max(0.9, r * 0.45); ctx.stroke(); }
    else ctx.fill();
  } else if (g === 'routines') {
    ctx.arc(x, y, r * 1.5, 0, Math.PI * 2); ctx.lineWidth = 1.1; ctx.stroke();
    ctx.beginPath(); ctx.arc(x, y, r * 0.65, 0, Math.PI * 2); ctx.fill();
    if (n.bad) { ctx.strokeStyle = '#f85149'; ctx.lineWidth = 1.4; ctx.beginPath(); ctx.arc(x, y, r * 2.3, 0, Math.PI * 2); ctx.stroke(); }
  } else if (g === 'clients') {
    // quien actúa: cuadrado redondeado con su inicial
    const l = r * 1.25;
    ctx.fillStyle = '#0d1014'; ctx.strokeStyle = '#e6e9ed'; ctx.lineWidth = 1.2;
    if (ctx.roundRect) ctx.roundRect(x - l, y - l, 2 * l, 2 * l, l * 0.35); else ctx.rect(x - l, y - l, 2 * l, 2 * l);
    ctx.fill(); ctx.stroke();
    ctx.font = `700 ${l * 1.1}px ui-monospace, Menlo, monospace`; ctx.fillStyle = '#e6e9ed';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText((n.label || '?')[0].toUpperCase(), x, y + l * 0.06);
  } else if (g === 'area') {
    ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fillStyle = '#0d1014'; ctx.fill();
    ctx.lineWidth = 1.4; ctx.stroke();
    ctx.globalAlpha *= 0.18; ctx.fillStyle = c; ctx.fill(); ctx.globalAlpha /= 0.18;
    ctx.font = `700 ${r * 1.15}px ui-monospace, Menlo, monospace`; ctx.fillStyle = c;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText((n.area || '?')[0].toUpperCase(), x, y + r * 0.08);
  } else if (g === 'mcps') {
    // enchufe: anillo con una barra; rojo si está caído, punteado si no hay dato
    const down = n.status === 'down', noData = !n.status;
    ctx.strokeStyle = down ? '#f85149' : c; ctx.lineWidth = 1.3;
    if (noData) ctx.setLineDash([2, 2]);
    ctx.arc(x, y, r * 1.45, 0, Math.PI * 2); ctx.fillStyle = '#0d1014'; ctx.fill(); ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(x - r * 0.8, y); ctx.lineTo(x + r * 0.8, y); ctx.stroke();
  } else if (g === 'core') {
    ctx.fillStyle = '#ffe2c8'; ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
  } else {
    ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
  }
}

// La nube del núcleo: granos que orbitan lento, el corazón que late.
const GRAINS = (() => {
  const rnd = seeded('cortex'), g = [];
  for (let i = 0; i < CONFIG.particles; i++) {
    const u = rnd(), v = rnd();
    const r = Math.min(1.25, Math.sqrt(-2 * Math.log(u + 1e-6)) * 0.42);  // gaussiana: densa al centro
    g.push({ r, a: v * Math.PI * 2, w: (rnd() - 0.5) * 0.0009, alpha: 0.25 + rnd() * 0.75, t: rnd() });
  }
  return g;
})();

function cloud(alpha) {
  if (alpha < 0.01) return;
  const rn = R * CONFIG.coreR;
  const grad = ctx.createRadialGradient(0, 0, 0, 0, 0, rn * 2.2);
  grad.addColorStop(0, `rgba(255,122,47,${0.22 * alpha})`); grad.addColorStop(1, 'rgba(255,122,47,0)');
  ctx.fillStyle = grad; ctx.beginPath(); ctx.arc(0, 0, rn * 2.2, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = CORE_COLOR;
  for (const p of GRAINS) {
    const a = p.a + clock * p.w, tremble = 1 + Math.sin(clock * 0.002 + p.t * 40) * 0.06;
    ctx.globalAlpha = alpha * p.alpha;
    const s = 1.3 / cam.k;
    ctx.fillRect(Math.cos(a) * p.r * rn * tremble - s / 2, Math.sin(a) * p.r * rn * tremble * 0.92 - s / 2, s, s);
  }
  ctx.globalAlpha = alpha;
  const w = 52 / cam.k, h = 15 / cam.k;
  ctx.fillStyle = 'rgba(13,16,20,.85)'; ctx.strokeStyle = 'rgba(255,122,47,.6)'; ctx.lineWidth = 1 / cam.k;
  ctx.beginPath(); ctx.rect(-w / 2, -h / 2, w, h); ctx.fill(); ctx.stroke();
  text('CORTEX', 0, 3.4 / cam.k, { size: 9, color: '#ffd2b0', align: 'center', spacing: 1.5, weight: 600 });
  ctx.globalAlpha = 1;
}

// Resplandor de cada dominio detrás de sus puntos: se calcula de donde ESTÁN los
// puntos en este cuadro, así acompaña la transición en vez de saltar.
function nebulae(alpha) {
  if (view === 'timeline' || view === 'circle' || alpha < 0.02) return;
  for (const a of AREAS()) {
    let sx = 0, sy = 0, k = 0;
    for (const n of NODES) if (n.area === a && n.layer === 'memory') { const e = POS.get(n.id); sx += e.x; sy += e.y; k++; }
    if (k < 2) continue;
    const cx = sx / k, cy = sy / k;
    let d = 0;
    for (const n of NODES) if (n.area === a && n.layer === 'memory') { const e = POS.get(n.id); d += Math.hypot(e.x - cx, e.y - cy); }
    const r = Math.max(40, (d / k) * 2.1);
    const op = Math.min(1, CONFIG.nebula * alpha * (filter ? (filter.type === 'area' && filter.value === a ? 1.8 : 0.3) : 1));
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    const c = color(a);
    g.addColorStop(0, c + Math.round(op * 255).toString(16).padStart(2, '0')); g.addColorStop(1, c + '00');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
  }
}

// Pulsos REALES. El recolector entrega los eventos de los últimos 150 s (quién leyó o
// escribió qué nota, qué rutina corrió); acá se reproducen con su ritmo real,
// desfasados una ventana. Sin actividad no se mueve nada: lo que se mueve significa algo.
const PULSE_COLOR = { read: '255,255,255', write: '255,159,90', query: '255,255,255' };
function originNode(o) {
  return o && (o.startsWith('routine:') || o.startsWith('client:')) ? o : 'mcp:cortex';
}
// Un evento entra por dos caminos: en vivo (SSE, al instante) o en la reproducción cada
// 2 min (respaldo si el vivo se corta). La misma clave evita que se dibuje dos veces.
function registerEvent(e, when) {
  if (!e || !e.target || !e.ts) return;
  const key = `${e.ts}|${e.type}|${e.origin}|${e.target}`;
  if (e.type !== 'query') {
    const u = LAST[e.target];
    if (!u || u.ts < e.ts) LAST[e.target] = e;
  }
  if (SEEN.has(key)) return;
  SEEN.add(key);
  if (SEEN.size > 4000) SEEN = new Set([...SEEN].slice(-2000));
  const a = originNode(e.origin), b = e.target;
  if (!BYID.has(a) || !BYID.has(b) || a === b) return;
  SCHEDULED.push({ a, b, type: e.type, when });
  dirty = true;
}
function schedulePulses(p) {
  if (!p || !p.events) return;
  const since = new Date(p.since).getTime(), now = performance.now();
  for (const e of p.events) registerEvent(e, now + Math.max(0, new Date(e.ts).getTime() - since));
}

/* ---------- pulsos en vivo (SSE) ---------- */
function markLive(yes) {
  const el = $('#live');
  if (el) { el.className = yes ? 'live yes' : 'live no'; el.textContent = yes ? 'live' : 'delayed'; el.title = yes ? 'pulses 2-3 s after they happen' : 'no live channel: pulses ~2 min behind'; }
}
function connectLive() {
  if (!window.EventSource) return markLive(false);
  const es = new EventSource('events');           // el navegador reconecta solo (retry 3 s)
  es.onopen = () => markLive(true);
  es.onerror = () => {
    markLive(false);
    // Si el proxy respondió el login (sesión vencida) el navegador cierra el canal para
    // siempre: load() detecta la sesión vencida, y si no lo era, se reconecta.
    if (es.readyState === EventSource.CLOSED) setTimeout(() => load().then(connectLive), 5000);
  };
  es.onmessage = (m) => {
    let e;
    try { e = JSON.parse(m.data); } catch { return; /* evento malformado: se ignora */ }
    if (e.type === 'routine' && e.state !== 'running') scheduleReload();
    if (e.type === 'routine' || e.type === 'routines_running') return routineEvent(e);
    if (e.type === 'write') scheduleReload();
    registerEvent(e, performance.now());
  };
}
// Tras una escritura o el fin de una rutina el servidor recolecta a los ~3 s: se relee
// state.json un poco después, una sola vez por ráfaga.
let reloadTimer = null;
function scheduleReload() {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(load, 6000);
}

/* ---------- rutinas corriendo en vivo: brillan y laten mientras dura la corrida ---------- */
// El servidor manda «running» al empezar y «finished»/«failed» al terminar, más una foto
// de lo que ya corre al conectarse. Si un fin se pierde, a las 2 h deja de latir solo.
const RUNNING = new Map();               // "routine:<name>" -> desde (ms)
const MAX_RUN_MS = 2 * 3600 * 1000;
function routineEvent(e) {
  if (e.type === 'routines_running') {
    RUNNING.clear();
    for (const r of e.routines || []) RUNNING.set(r, Date.now());
  } else if (e.state === 'running') RUNNING.set(e.routine, Date.now());
  else RUNNING.delete(e.routine);
  dirty = true;
  if (DATA) paintRight(DATA);
}
function heartbeat(n, e, r, now) {
  const t = (Math.sin(now / 260) + 1) / 2;                 // ~1,6 s por ciclo
  const c = color(n.area);
  ctx.save();
  ctx.globalAlpha = 0.2 + 0.5 * t;
  const g = ctx.createRadialGradient(e.x, e.y, 0, e.x, e.y, r * 5);
  g.addColorStop(0, c); g.addColorStop(1, c + '00');
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(e.x, e.y, r * 5, 0, Math.PI * 2); ctx.fill();
  ctx.globalAlpha = 0.35 + 0.65 * t; ctx.strokeStyle = c; ctx.lineWidth = 1.6 / cam.k;
  ctx.beginPath(); ctx.arc(e.x, e.y, r * (2.2 + 0.7 * t), 0, Math.PI * 2); ctx.stroke();
  ctx.restore();
}
function pointAt(A, B, t) {
  if (!LAY.curved) return { x: A.x + (B.x - A.x) * t, y: A.y + (B.y - A.y) * t };
  const cx = (A.x + B.x) * 0.12, cy = (A.y + B.y) * 0.12, u = 1 - t;
  return { x: u * u * A.x + 2 * u * t * cx + t * t * B.x, y: u * u * A.y + 2 * u * t * cy + t * t * B.y };
}
function drawPulses(now, alpha) {
  // los que ya tocan salen de la cola
  for (let i = SCHEDULED.length - 1; i >= 0; i--) {
    if (SCHEDULED[i].when <= now) { PULSES.push({ ...SCHEDULED[i], t0: now }); SCHEDULED.splice(i, 1); }
  }
  for (const p of PULSES) {
    if (!p.arrived && now - p.t0 >= CONFIG.pulseMs) {
      p.arrived = true;
      if (p.type === 'write') FLASHES.push({ id: p.b, t0: now });
    }
  }
  PULSES = PULSES.filter((p) => now - p.t0 < CONFIG.pulseMs);
  FLASHES = FLASHES.filter((d) => now - d.t0 < CONFIG.flashMs);
  if (alpha < 0.05) return;
  ctx.lineCap = 'round';
  for (const p of PULSES) {
    const A = POS.get(p.a), B = POS.get(p.b);
    if (!A || !B || A.a < 0.3 || B.a < 0.3) continue;
    const t = easeIO((now - p.t0) / CONFIG.pulseMs);
    const head = pointAt(A, B, t), tail = pointAt(A, B, Math.max(0, t - 0.16));
    const rgb = p.type === 'run' ? hexRgb(color(BYID.get(p.a)?.area)) : (PULSE_COLOR[p.type] || PULSE_COLOR.read);
    const strength = (p.type === 'query' ? 0.4 : 0.95) * Math.sin(t * Math.PI) * alpha;
    const g = ctx.createLinearGradient(tail.x, tail.y, head.x, head.y);
    g.addColorStop(0, `rgba(${rgb},0)`); g.addColorStop(1, `rgba(${rgb},${strength})`);
    ctx.strokeStyle = g; ctx.lineWidth = (p.type === 'write' ? 2.4 : 1.7) / cam.k;
    ctx.beginPath(); ctx.moveTo(tail.x, tail.y); ctx.lineTo(head.x, head.y); ctx.stroke();
  }
  for (const d of FLASHES) {
    const e = POS.get(d.id); if (!e) continue;
    const k = (now - d.t0) / CONFIG.flashMs;
    ctx.strokeStyle = `rgba(255,159,90,${(1 - k) * 0.9})`; ctx.lineWidth = 2 / cam.k;
    ctx.beginPath(); ctx.arc(e.x, e.y, (6 + k * 22) / cam.k, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.lineCap = 'butt';
}
function hexRgb(h) {
  const v = parseInt((h || '#8b949e').slice(1), 16);
  return `${(v >> 16) & 255},${(v >> 8) & 255},${v & 255}`;
}

function inFocus(n) {
  if (filter) {
    const ok = filter.type === 'area' ? n.area === filter.value : group(n) === filter.value;
    if (!ok) return false;
  }
  const h = hover || chosen;
  if (h) return n.id === h || NEIGHBOURS.get(h)?.has(n.id);
  return true;
}

function frame(now) {
  const dt = Math.min(64, now - lastFrame); lastFrame = now;
  if (motion) clock += dt;

  // cámara: sigue al elegido si se pidió «volar», y se acerca con suavidad
  if (follow && POS.has(follow)) { const e = POS.get(follow); camT.x = -e.x * camT.k; camT.y = -e.y * camT.k; }
  const dc = Math.abs(camT.k - cam.k) + Math.abs(camT.x - cam.x) + Math.abs(camT.y - cam.y);
  const moving = dc > 0.01;
  if (moving) { const f = 1 - Math.pow(0.0025, dt / 1000); cam.k += (camT.k - cam.k) * f; cam.x += (camT.x - cam.x) * f; cam.y += (camT.y - cam.y) * f; }

  const transition = now - viewT0 < CONFIG.morphMs + CONFIG.stagger + 50;
  for (const [k, since] of RUNNING) if (Date.now() - since > MAX_RUN_MS) RUNNING.delete(k);
  if (!(motion || transition || moving || dirty || RUNNING.size || PULSES.length || SCHEDULED.length || FLASHES.length)) { requestAnimationFrame(frame); return; }
  dirty = false;
  if (!LAY || !NODES.length || !W || !H) { requestAnimationFrame(frame); return; }
  // Un nodo que desapareció al recargar los datos no puede quedar como foco: el error
  // cortaría este cuadro antes de pedir el siguiente y el panel se congelaría.
  if (hover && !BYID.has(hover)) hover = null;
  if (chosen && !BYID.has(chosen)) choose(null);   // cierra también la ficha del nodo que ya no está

  // 1. posiciones de este cuadro
  for (const n of NODES) {
    const e = POS.get(n.id), t = LAY.pos(n, now);
    const k = easeIO((now - e.t0 - e.delay) / CONFIG.morphMs);
    e.x = e.from.x + (t.x - e.from.x) * k; e.y = e.from.y + (t.y - e.from.y) * k;
    e.a = e.from.a + (t.a - e.from.a) * k; e.s = e.from.s + (t.s - e.from.s) * k; e.z = t.z;
  }
  const kDecor = easeIO((now - viewT0 - CONFIG.stagger * 0.5) / CONFIG.morphMs);
  cloudAlpha += ((LAY.noCloud ? 0 : 1) - cloudAlpha) * Math.min(1, dt / 180);

  // 2. pintar
  ctx.clearRect(0, 0, W, H);
  const backdrop = ctx.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, Math.max(W, H) * 0.7);
  backdrop.addColorStop(0, 'rgba(40,52,80,.22)'); backdrop.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = backdrop; ctx.fillRect(0, 0, W, H);
  ctx.save();
  ctx.translate(W / 2 + cam.x, H / 2 + cam.y); ctx.scale(cam.k, cam.k);

  nebulae(kDecor);
  LAY.decor(kDecor, now);

  // aristas: todas en un solo trazo tenue; las del foco, aparte y en color
  const focus = hover || chosen;
  const kEdges = LAY.noEdges ? 1 - kDecor : 1;
  if (kEdges > 0.01) {
    ctx.lineWidth = 0.6 / cam.k;
    ctx.strokeStyle = `rgba(170,190,220,${(focus || filter ? 0.025 : 0.06) * kEdges})`;
    ctx.beginPath();
    for (const [a, b, hub] of EDGES) {
      if (hub && view !== 'links') continue;
      const A = POS.get(a), B = POS.get(b);
      if (A.a < 0.05 || B.a < 0.05) continue;
      ctx.moveTo(A.x, A.y);
      if (LAY.curved && kDecor > 0.5) ctx.quadraticCurveTo((A.x + B.x) * 0.12, (A.y + B.y) * 0.12, B.x, B.y);
      else ctx.lineTo(B.x, B.y);
    }
    ctx.stroke();
    if (focus) {
      ctx.lineWidth = 1.1 / cam.k;
      for (const v of NEIGHBOURS.get(focus) || []) {
        const A = POS.get(focus), B = POS.get(v);
        ctx.strokeStyle = color(BYID.get(v).area); ctx.globalAlpha = 0.65 * kEdges;
        ctx.beginPath(); ctx.moveTo(A.x, A.y);
        if (LAY.curved) ctx.quadraticCurveTo((A.x + B.x) * 0.12, (A.y + B.y) * 0.12, B.x, B.y); else ctx.lineTo(B.x, B.y);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
  }

  drawPulses(now, kEdges);

  // nodos, de atrás hacia adelante cuando hay profundidad
  const ordered = LAY.depth ? NODES.slice().sort((a, b) => POS.get(b.id).z - POS.get(a.id).z) : NODES;
  let cloudDrawn = false;
  for (const n of ordered) {
    const e = POS.get(n.id);
    if (LAY.depth && !cloudDrawn && e.z < 0) { cloud(cloudAlpha); cloudDrawn = true; }
    const alpha = e.a * (inFocus(n) ? 1 : CONFIG.dim);
    if (alpha < 0.01) continue;
    const r = size(n) * Math.max(0.3, e.s) / Math.sqrt(cam.k);  // al acercarse crecen, pero menos que el zoom
    ctx.globalAlpha = alpha * 0.95;
    if ((n.degree || 0) >= haloThreshold && n.kind !== 'area' && alpha > 0.5) {   // halo solo para los ejes del grafo
      ctx.globalAlpha = 0.09 * alpha; ctx.fillStyle = color(n.area);
      ctx.beginPath(); ctx.arc(e.x, e.y, r * 2.4, 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = alpha * 0.95;
    }
    shape(n, e.x, e.y, r);
    if (RUNNING.has(n.id)) heartbeat(n, e, r, now);
    if (n.id === chosen) {
      ctx.globalAlpha = 1; ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.2 / cam.k;
      ctx.beginPath(); ctx.arc(e.x, e.y, r + 5 / cam.k, 0, Math.PI * 2); ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
  if (!cloudDrawn) cloud(cloudAlpha);

  // etiquetas: con «labels» todas; sin él, solo el foco y sus vecinos (y siempre los clientes)
  const labelled = (n) => labels ? inFocus(n) : (focus && (n.id === focus || (BYID.get(focus)?.kind !== 'area' && NEIGHBOURS.get(focus)?.has(n.id))));
  for (const n of NODES) {
    const e = POS.get(n.id);
    if (n.layer === 'clients' && e.a > 0.3) {
      text(n.label.slice(0, 24), e.x, e.y + (size(n) + 12) / cam.k, { size: 9, color: `rgba(230,235,240,${inFocus(n) ? 0.85 : 0.3})`, align: 'center', weight: 600 });
      continue;
    }
    if (n.kind === 'area' && e.a > 0.3) {
      const al = inFocus(n) ? 1 : 0.35;
      text(n.label.toUpperCase(), e.x + 11 / cam.k, e.y + 1 / cam.k, { size: 9.5, color: `rgba(230,235,240,${al})`, spacing: 1, weight: 600 });
      text(String(DATA?.graph?.areas?.[n.area] ?? ''), e.x + 11 / cam.k, e.y + 11 / cam.k, { size: 8.5, color: color(n.area), weight: 400 });
      continue;
    }
    if (e.a < 0.3 || !labelled(n)) continue;
    const main = n.id === focus;
    text(n.label.slice(0, 34), e.x + (size(n) + 4) / cam.k, e.y + 3 / cam.k,
         { size: main ? 11 : 9, color: main ? '#fff' : 'rgba(210,218,226,.8)', weight: main ? 600 : 400 });
  }
  ctx.restore();
  requestAnimationFrame(frame);
}

/* ---------- interacción ---------- */
function toWorld(px, py) { return { x: (px - W / 2 - cam.x) / cam.k, y: (py - H / 2 - cam.y) / cam.k }; }

function nodeAt(px, py) {
  const m = toWorld(px, py);
  let best = null, dMin = 12 / cam.k;
  for (const n of NODES) {
    const e = POS.get(n.id);
    if (e.a < 0.3) continue;
    const d = Math.hypot(e.x - m.x, e.y - m.y) - size(n) * 0.5;
    if (d < dMin) { dMin = d; best = n.id; }
  }
  return best;
}

let drag = null;
canvas.addEventListener('pointerdown', (ev) => {
  drag = { x: ev.offsetX, y: ev.offsetY, cx: camT.x, cy: camT.y, moved: false };
  canvas.setPointerCapture(ev.pointerId);
});
canvas.addEventListener('pointermove', (ev) => {
  if (drag) {
    const dx = ev.offsetX - drag.x, dy = ev.offsetY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; follow = null; }
    if (drag.moved) { camT.x = cam.x = drag.cx + dx; camT.y = cam.y = drag.cy + dy; dirty = true; }
    return;
  }
  const h = nodeAt(ev.offsetX, ev.offsetY);
  if (h !== hover) { hover = h; dirty = true; canvas.style.cursor = h ? 'pointer' : 'grab'; showTip(h, ev); }
  else if (h) showTip(h, ev);
});
canvas.addEventListener('pointerleave', () => { hover = null; dirty = true; showTip(null); });
canvas.addEventListener('pointerup', (ev) => {
  const was = drag; drag = null;
  if (was?.moved) return;
  choose(nodeAt(ev.offsetX, ev.offsetY));
});
// doble clic: abre el texto del nodo, sin pasar por la tarjeta
canvas.addEventListener('dblclick', (ev) => {
  const id = nodeAt(ev.offsetX, ev.offsetY), n = id && BYID.get(id);
  if (n && n.path && n.path.endsWith('.md')) openNote(n);
});
canvas.addEventListener('wheel', (ev) => {
  ev.preventDefault();
  const k = Math.min(CONFIG.zoomMax, Math.max(CONFIG.zoomMin, camT.k * Math.exp(-ev.deltaY * 0.0015)));
  const m = toWorld(ev.offsetX, ev.offsetY);
  camT.x = ev.offsetX - W / 2 - m.x * k; camT.y = ev.offsetY - H / 2 - m.y * k; camT.k = k;
  follow = null; dirty = true;
}, { passive: false });

function showTip(id, ev) {
  const tip = $('#tip');
  if (!id || id === chosen) { tip.hidden = true; return; }
  const n = BYID.get(id);
  const extra = n.kind === 'skill' ? ` · ${esc(n.plugin)}`
    : n.kind === 'client' && n.ip ? ` · ${esc(n.ip)}`
    : n.bytes ? ` · ${nf(n.bytes / 1024, 1)} KB` : '';
  tip.innerHTML = `<b>${esc(n.label)}</b><span>${esc(typeName(n))} · ${esc(n.area)}${extra}${n.kind === 'area' ? '' : ' · ' + (n.degree || 0) + ' links'}</span>${n.summary ? `<em>${esc(n.summary.slice(0, 110))}${n.summary.length > 110 ? '…' : ''}</em>` : ''}`;
  tip.hidden = false;
  const box_ = $('#graphBox').getBoundingClientRect();
  tip.style.left = `${ev.clientX - box_.left + 14}px`;
  tip.style.top = `${ev.clientY - box_.top + 12}px`;
}

const originLabel = (o) => BYID.get(o)?.label || (o === 'unknown' ? 'unknown client' : o);
function choose(id, fly = false) {
  chosen = id || null; dirty = true;
  const t = $('#card');
  if (!chosen || !BYID.has(chosen)) { chosen = null; t.hidden = true; follow = null; return; }
  const n = BYID.get(chosen);
  const isNote = n.path && n.path.endsWith('.md');
  const neigh = [...(NEIGHBOURS.get(chosen) || [])].map((v) => BYID.get(v))
    .sort((a, b) => (b.degree || 0) - (a.degree || 0)).slice(0, 8);
  let extra = '';

  const u = LAST[n.id];
  const EVENT = { read: 'read', write: 'write', query: 'query', run: 'run' };
  if (u) extra += `<div class="meta">last ${esc(EVENT[u.type] || u.type)}: ${age(u.ts)} (${esc(originLabel(u.origin))})</div>`;
  if (n.kind === 'mcp') {
    extra += `<div class="meta">status: ${n.status === 'up' ? '<span class="ok">up</span>' : n.status === 'down' ? '<span class="bad">down</span>' : 'no data'}</div>`;
  }
  if (n.kind === 'skill') extra = `<div class="meta">plugin: ${esc(n.plugin)}</div>` + extra;
  if (n.kind === 'client') {
    extra += `<div class="meta">${n.ip ? `ip ${esc(n.ip)}` : 'every IP without a name'} · last seen ${n.changed ? age(n.changed) : 'never'}</div>`;
    if (!n.ip) extra += '<p class="note-warn">Name your machines in setup to see who reads and writes.</p>';
  }
  if (n.kind === 'routine' && n.info) {
    const d = n.info, lg = (DATA?.logs?.items || []).find((l) => l.routine === d.name);
    extra = `<div class="meta">${esc(d.schedule || 'no schedule')} · ${d.state === 'ready' ? 'ready' : esc(STATE_LABEL[d.state] || d.state)} · ${d.last_ts ? `last run ${d.ok ? 'ok' : 'failed'} ${age(d.last_ts)}` : 'never ran'}${d.needs_claude ? ' · uses a model' : ''}</div>
             ${lg?.summary ? `<p class="${lg.ok === false ? 'note-warn' : ''}">${esc(lg.summary)}</p>` : ''}` + extra;
  }
  t.innerHTML = `
    <div class="head"><i style="background:${color(n.area)}"></i><span>${esc(typeName(n))} · ${esc(n.area)} · ${esc(LAYER_NAME[n.layer] || n.layer)}</span>
      <button class="x" title="close">×</button></div>
    <h3>${esc(n.label)}</h3>
    ${n.summary ? `<p>${esc(n.summary)}</p>` : ''}
    ${extra}
    <div class="meta">${esc(n.path)}${n.changed && n.kind !== 'client' ? ` · changed ${age(n.changed)}` : ''}${n.kind === 'area' ? '' : ` · ${NEIGHBOURS.get(n.id).size} links`}</div>
    ${neigh.length ? `<div class="neigh">${neigh.map((v) => `<span data-id="${esc(v.id)}"><i style="background:${color(v.area)}"></i>${esc(v.label.slice(0, 26))}</span>`).join('')}</div>` : ''}
    <div class="actions">${n.kind === 'routine' ? '<button data-a="log" class="primary">open log</button>' : ''}${isNote ? '<button data-a="open" class="primary">open</button>' : ''}<button data-a="copy">copy path</button><button data-a="fly">fly to</button><button data-a="only">only ${esc(n.area)}</button></div>`;
  t.hidden = false;
  t.querySelector('.x').onclick = () => choose(null);
  t.querySelector('[data-a=open]')?.addEventListener('click', () => openNote(n));
  t.querySelector('[data-a=log]')?.addEventListener('click', () => openLog(n.id.replace(/^routine:/, '')));
  t.querySelector('[data-a=only]').onclick = () => { filter = { type: 'area', value: n.area }; paintLegend(DATA); dirty = true; };
  t.querySelector('[data-a=fly]').onclick = () => flyTo(chosen);
  t.querySelector('[data-a=copy]').onclick = (e) => copy(n.path, e.target);
  t.querySelectorAll('.neigh span').forEach((s) => s.onclick = () => choose(s.dataset.id, true));
  showTip(null);
  if (fly) flyTo(chosen);
}

// «open»: el texto de la nota, leído de data/notes.json (lo escribe el recolector).
// Se pide recién al primer uso; el panel sigue sin tocar el vault.
let NOTES = null;
async function openNote(n) {
  const l = $('#reader');
  l.hidden = false;
  runSelector(null);
  l.querySelector('.path').textContent = n.path.startsWith('plugins/') ? n.path : 'vault/' + n.path;
  const pre = l.querySelector('pre');
  pre.textContent = 'loading…';
  try {
    NOTES ||= await (await fetch('data/notes.json?t=' + Date.now())).json();
    pre.textContent = NOTES[n.path] ?? 'this note is not in notes.json yet (the collector runs every 2 min)';
  } catch { NOTES = null; pre.textContent = 'could not read data/notes.json'; }
  pre.scrollTop = 0;
}
// «open log»: el detalle de la última corrida, de data/logs.json (lo escribe el recolector).
// Se pide fresco cada vez: es chico y cambia en cada corrida.
async function openLog(routine) {
  const l = $('#reader'), pre = l.querySelector('pre'), path = l.querySelector('.path');
  l.hidden = false;
  runSelector(null);
  path.textContent = routine;
  pre.textContent = 'loading…';
  try {
    const all = await (await fetch('data/logs.json?t=' + Date.now())).json();
    const r = all[routine];
    if (!r || !r.entry?.source) { pre.textContent = 'no log for this routine yet: it has not run'; return; }
    const e = r.entry;
    const show = (c) => {
      path.textContent = `${routine} · report · ${age(c.ts)}${c.ok === false ? ' · failed' : ''}`;
      pre.textContent = [c.summary, c.detail].filter(Boolean).join('\n\n') || '(no output)';
      pre.scrollTop = 0;
    };
    runSelector(r.history, show);
    show({ ts: e.ts, ok: e.ok, summary: e.summary, detail: r.detail });
  } catch { pre.textContent = 'could not read data/logs.json'; }
}

// Las últimas corridas (historial de reportes) en un selector de la cabecera del
// lector. Sin historial, o al abrir una nota, el selector se saca.
function runSelector(runs, onPick) {
  const head = $('#reader .head');
  head.querySelector('select.runs')?.remove();
  if (!runs || runs.length < 2) return;
  const when = (ts) => new Date(ts).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
  const sel = document.createElement('select');
  sel.className = 'runs';
  sel.title = `last ${runs.length} runs`;
  sel.innerHTML = runs.map((c, i) =>
    `<option value="${i}">${i === 0 ? 'latest · ' : ''}${esc(when(c.ts))}${c.ok === false ? ' · failed' : ''}</option>`).join('');
  sel.onchange = () => onPick(runs[+sel.value]);
  head.insertBefore(sel, head.querySelector('.x'));
}
$('#reader .x').onclick = () => ($('#reader').hidden = true);

function flyTo(id) { camT.k = Math.max(camT.k, CONFIG.zoomFly); follow = id; dirty = true; }

function copy(txt, button) {
  // fuera de https navigator.clipboard no existe: se cae al método viejo antes de rendirse
  const ok = () => { button.textContent = 'copied'; setTimeout(() => (button.textContent = 'copy path'), 1400); };
  if (navigator.clipboard && isSecureContext) { navigator.clipboard.writeText(txt).then(ok, () => {}); return; }
  const ta = document.createElement('textarea'); ta.value = txt; document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); ok(); } finally { ta.remove(); }
}

/* ---------- búsqueda ---------- */
const searchBox = $('#search'), results = $('#results');
let found = [];
searchBox.addEventListener('input', () => {
  const q = searchBox.value.trim().toLowerCase();
  if (q.length < 2) { results.hidden = true; return; }
  found = NODES.map((n) => {
    const l = n.label.toLowerCase(), i = n.id.toLowerCase();
    const p = l.startsWith(q) || i.startsWith(q) ? 3 : l.includes(q) || i.includes(q) ? 2
      : (n.path + ' ' + (n.summary || '')).toLowerCase().includes(q) ? 1 : 0;
    return { n, p };
  }).filter((x) => x.p).sort((a, b) => b.p - a.p || (b.n.degree || 0) - (a.n.degree || 0)).slice(0, 8).map((x) => x.n);
  results.innerHTML = found.map((n, i) =>
    `<div data-i="${i}"><i style="background:${color(n.area)}"></i>${esc(n.label.slice(0, 40))}<small>${esc(n.area)}</small></div>`).join('')
    || '<div class="none">no matches</div>';
  results.hidden = false;
  results.querySelectorAll('[data-i]').forEach((d) => d.onclick = () => goTo(found[+d.dataset.i]));
});
searchBox.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && found[0]) goTo(found[0]);
  if (e.key === 'Escape') { searchBox.value = ''; results.hidden = true; searchBox.blur(); }
});
function goTo(n) { results.hidden = true; searchBox.value = n.label; choose(n.id, true); }
addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement !== searchBox) { e.preventDefault(); searchBox.focus(); }
  if (e.key === 'Escape' && !$('#reader').hidden) { $('#reader').hidden = true; return; }
  if (e.key === 'Escape' && document.activeElement !== searchBox) { choose(null); filter = null; paintLegend(DATA); dirty = true; }
});

/* ---------- leyendas: aíslan un dominio o un tipo ---------- */
function paintLegend(d) {
  const g = d?.graph || {};
  const areas = AREAS().map((a) => [a, (g.areas || {})[a] ?? 0]);
  const isFilter = (type, v) => filter && filter.type === type && filter.value === v;
  $('#legend').innerHTML = areas.map(([a, n]) =>
    `<span data-type="area" data-v="${esc(a)}" class="${filter && !isFilter('area', a) ? 'off' : ''} ${isFilter('area', a) ? 'sel' : ''}"><i style="background:${color(a)}"></i>${esc(a)} ${n}</span>`
  ).join('');
  const groups = {};
  for (const n of NODES) groups[group(n)] = (groups[group(n)] || 0) + 1;
  $('#types').innerHTML = Object.keys(GROUP_NAME).filter((k) => groups[k]).map((k) =>
    `<span data-type="group" data-v="${k}" class="${filter && !isFilter('group', k) ? 'off' : ''} ${isFilter('group', k) ? 'sel' : ''}"><canvas class="shape" data-g="${k}"></canvas>${GROUP_NAME[k]} ${groups[k]}</span>`
  ).join('');
  // Cada ícono lo dibuja la MISMA shape() del grafo: la leyenda no puede desviarse.
  document.querySelectorAll('#types canvas.shape').forEach((cv) => {
    const side = 14, dpr = devicePixelRatio || 1, k = cv.dataset.g;
    cv.width = cv.height = side * dpr;
    const c2 = cv.getContext('2d'); c2.scale(dpr, dpr);
    const sample = { kind: k === 'area' ? 'area' : '', layer: k, area: k === 'area' ? 'domain' : 'general', label: 'C', status: 'up' };
    shape(sample, side / 2, side / 2, k === 'clients' ? 4 : k === 'area' ? 5.6 : 3.2, c2);
  });
  document.querySelectorAll('#legend span, #types span').forEach((s) => s.onclick = () => {
    filter = isFilter(s.dataset.type, s.dataset.v) ? null : { type: s.dataset.type, value: s.dataset.v };
    paintLegend(DATA); dirty = true;
  });
  const c = g.layers || {};
  $('#graphFooter').innerHTML =
    `<span>${c.memory || 0} notes · ${c.rules || 0} rules · ${c.skills || 0} skills · ${c.routines || 0} routines · ${c.clients || 0} clients · ${(g.edges || []).length} links</span>` +
    `<span class="help">wheel: zoom · drag: pan · click: card · double-click: open · / search · esc: clear</span>`;
}

/* ---------- tamaño ---------- */
function measure() {
  const r = canvas.getBoundingClientRect();
  W = r.width; H = r.height; R = Math.max(80, Math.min(W, H) / 2 - 40);
  canvas.width = Math.max(1, W * devicePixelRatio); canvas.height = Math.max(1, H * devicePixelRatio);
  ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  if (NODES.length) { LAY = LAYOUTS[view](); }
  dirty = true;
}

/* ---------- ciclo ---------- */
let first = true;
async function load() {
  try {
    const r = await fetch('data/state.json?t=' + Date.now());
    // Sesión vencida: el proxy redirige al login de /setup.
    if (r.redirected && new URL(r.url).pathname.startsWith('/setup/login')) { location.href = '/setup/login'; return; }
    if (!r.ok) throw new Error(String(r.status));
    DATA = await r.json();
  } catch (e) {
    $('#chips').innerHTML = `<span class="chip"><i class="dot bad"></i>no data yet${DATA ? ` · last ${age(DATA.generated)}` : ''}</span>`;
    return;
  }
  const name = DATA.instance?.name || 'cortex';
  $('#subtitle').textContent = `${name} · memory · skills · routines`;
  document.title = `Agent OS · ${name}`;
  paintChips(DATA); paintStatus(DATA); paintLeft(DATA); paintRight(DATA);
  prepareGraph(DATA.graph || {});
  schedulePulses(DATA.pulses);
  paintLegend(DATA);
  if (first) {
    // la primera vez los puntos nacen del núcleo hacia su lugar
    first = false; measure();
    for (const e of POS.values()) { e.x = e.y = 0; e.a = 0; e.s = 0.3; }
    changeView(view, true);
  } else {
    // en las recargas la vista se rehace sin animar: los nodos que siguen están donde estaban
    LAY = LAYOUTS[view]();
    for (const n of NODES) { const e = POS.get(n.id); e.delay = LAY.order(n) * CONFIG.stagger; }
  }
  if (chosen && !BYID.has(chosen)) choose(null);
  dirty = true;
}

document.querySelectorAll('[data-view]').forEach((b) => b.onclick = () => {
  document.querySelectorAll('[data-view]').forEach((o) => o.classList.remove('on'));
  b.classList.add('on'); if (NODES.length) changeView(b.dataset.view); else view = b.dataset.view;
});
$('#motion').onclick = () => { motion = !motion; $('#motion').classList.toggle('on', motion); dirty = true; };
$('#labels').onclick = () => { labels = !labels; $('#labels').classList.toggle('on', labels); dirty = true; };
$('#fit').onclick = () => { camT = { k: 1, x: 0, y: 0 }; follow = null; dirty = true; };
$('#fullscreen').onclick = () => {
  const g = $('#graphBox');
  document.fullscreenElement ? document.exitFullscreen() : g.requestFullscreen?.();
};
document.addEventListener('fullscreenchange', () => setTimeout(measure, 60));
$('#motion').classList.add('on');
new ResizeObserver(measure).observe(canvas);
setInterval(() => { if (DATA) { paintChips(DATA); paintStatus(DATA); } }, 15000);  // las edades avanzan aunque no lleguen datos
load().then(() => { requestAnimationFrame(frame); connectLive(); });
setInterval(load, 60000);  // red de seguridad: lo normal es la recarga que dispara el SSE
