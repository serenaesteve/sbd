'use strict';

/* SBD — el archivador. Un índice de bases y tablas a la izquierda y, a la derecha,
   la carpeta de lo que tienes abierto, con sus pestañas: examinar, estructura,
   código, buscar, insertar… Todo lo que se hace con el ratón es una orden de SBD,
   y el panel la enseña escrita al hacerla: se puede llevar a «Código» y retocar. */

const TIPOS = ['entero', 'decimal', 'dinero', 'texto', 'logico', 'fecha', 'fechahora'];
const TAMANOS = [25, 50, 100, 250];
const NUMERICOS = new Set(['INTEGER', 'REAL', 'MONEY']);

const estado = {
  palabras: new Set(),
  funciones: [],
  usuario: null,
  sinUsuarios: true,
  registro: false,
  enlaces: [],
  directorio: '',
  bases: [],
  infos: new Map(),
  abiertas: new Set(leer('sbd.abiertas', [])),
  ruta: { base: null, tabla: null, p: null },
  info: null,
  meta: null,
  aviso: null,
  examinar: null,
  codigo: null,
  historial: leer('sbd.historial', []),
};

/* ------------------------------------------------------------- utilidades */
function leer(clave, defecto) {
  try { return JSON.parse(localStorage.getItem(clave)) ?? defecto; } catch { return defecto; }
}
function guardar(clave, valor) {
  try { localStorage.setItem(clave, JSON.stringify(valor)); } catch { /* sin almacenamiento */ }
}

const $ = (sel, raiz = document) => raiz.querySelector(sel);

function h(tag, props, ...hijos) {
  const nodo = document.createElement(tag);
  let valor;
  for (const [clave, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (clave === 'class') nodo.className = v;
    else if (clave === 'value') valor = v;
    else if (clave.startsWith('on') && typeof v === 'function') nodo.addEventListener(clave.slice(2), v);
    else if (typeof v === 'boolean') nodo[clave] = v;
    else nodo.setAttribute(clave, v);
  }
  for (const hijo of hijos.flat(Infinity)) {
    if (hijo == null || hijo === false) continue;
    nodo.append(hijo instanceof Node ? hijo : String(hijo));
  }
  if (valor !== undefined) nodo.value = valor;
  return nodo;
}

/* Un número que ya viene escrito (el dinero): va tal cual a la orden, sin pasar por coma flotante. */
class Cifra {
  constructor(texto) { this.texto = texto; }
  toString() { return this.texto; }
}

/* Un nombre tal como se escribe en SBD: entre `` si choca con el lenguaje. */
function nombre(n) {
  if (/^[a-zñ_][a-z0-9ñ_]*$/.test(n) && !estado.palabras.has(n)) return n;
  return '`' + n.replaceAll('`', '``') + '`';
}

/* Un valor tal como se escribe en SBD. */
function literal(v) {
  if (v === null || v === undefined) return 'nulo';
  if (v === true) return 'verdadero';
  if (v === false) return 'falso';
  if (v instanceof Cifra) return v.texto;
  if (typeof v === 'number') return String(v);
  return '"' + String(v).replaceAll('"', '""') + '"';
}

const cifra = n => Number(n).toLocaleString('es-ES');
const plural = (n, uno, varios) => `${cifra(n)} ${n === 1 ? uno : varios}`;

function bytes(b) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1).replace('.', ',')} KB`;
  return `${(b / 1024 ** 2).toFixed(1).replace('.', ',')} MB`;
}

function retrasar(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/* Resaltado de SBD: palabras del lenguaje, textos, números y comentarios. */
function resaltar(texto) {
  const esc = s => s.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  const patron = /(--[^\n]*|#[^\n]*|\/\*[\s\S]*?(?:\*\/|$))|("(?:[^"]|"")*"?|'(?:[^']|'')*'?)|(`(?:[^`]|``)*`?)|(\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?|\b\d+(?:\.\d+)?\b)|([A-Za-zÀ-ÿñÑ_][\wÀ-ÿñÑ]*)/g;
  let html = '', ultimo = 0, m;
  while ((m = patron.exec(texto))) {
    html += esc(texto.slice(ultimo, m.index));
    const [todo, comentario, cadena, ident, numero, palabra] = m;
    if (comentario) html += `<span class="c">${esc(todo)}</span>`;
    else if (cadena) html += `<span class="s">${esc(todo)}</span>`;
    else if (ident) html += esc(todo);
    else if (numero) html += `<span class="n">${todo}</span>`;
    else if (palabra && estado.palabras.has(palabra.toLowerCase())) html += `<span class="k">${esc(todo)}</span>`;
    else html += esc(todo);
    ultimo = m.index + todo.length;
  }
  return html + esc(texto.slice(ultimo));
}

function preOrden(texto, clase = 'orden') {
  const pre = h('pre', { class: clase });
  pre.innerHTML = resaltar(texto);  // resaltar() escapa todo el texto
  return pre;
}

/* ------------------------------------------------------------------- API */
/* Cada pestaña del navegador es una sesión: sus transacciones son suyas. */
const SESION = (() => {
  const nueva = () => Array.from(crypto.getRandomValues(new Uint8Array(18)), b => b.toString(16).padStart(2, '0')).join('');
  try {
    let id = sessionStorage.getItem('sbd.sesion');
    if (!id) { id = nueva(); sessionStorage.setItem('sbd.sesion', id); }
    return id;
  } catch {
    return nueva();
  }
})();

let entradas = 0;  // cuántas veces se ha entrado: un «hay que entrar» de antes ya no vale

async function api(ruta, cuerpo) {
  const vez = entradas;
  const opciones = cuerpo === undefined ? { headers: { 'SBD-Sesion': SESION } } : {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'SBD-Sesion': SESION }, body: JSON.stringify(cuerpo),
  };
  let resp, datos;
  try {
    resp = await fetch(ruta, opciones);
    datos = await resp.json();
  } catch {
    throw new Error('No hay conexión con el servidor de SBD. ¿Sigue en marcha «sbd servidor»?');
  }
  if (resp.status === 401 && datos.entrar) {
    if (vez === entradas) mostrarEntrada(datos.error);
    throw new Error(datos.error);
  }
  if (!resp.ok || (datos.error && cuerpo === undefined)) throw new Error(datos.error || `Error ${resp.status}`);
  return datos;
}

/* Las claves de «crear usuario» y «cambiar clave» no se muestran ni se guardan. */
const PATRON_CLAVE = /(\bclave\b(?:\s+de\s+\S+\s+a)?\s+)("(?:[^"]|"")*"?|'(?:[^']|'')*'?)/gi;
const ocultarClaves = texto => texto.replace(PATRON_CLAVE, '$1"••••••••"');
const llevaClave = texto => new RegExp(PATRON_CLAVE.source, 'i').test(texto);

const puedeEscribir = () => !estado.usuario || estado.usuario.permiso !== 'leer';
const puedeTodo = () => !estado.usuario || estado.usuario.permiso === 'todo';
const esCuenta = () => estado.usuario?.permiso === 'cuenta';
const puedeCrearBases = () => puedeTodo() || esCuenta();

/* Ejecuta un texto con una o varias órdenes. Nunca lanza: los fallos vuelven en «error». */
async function correr(orden, base = estado.ruta.base) {
  let datos;
  try {
    datos = await api('/api/ejecutar', { base, orden });
  } catch (err) {
    datos = { resultados: [], error: err.message, orden_fallida: orden };
  }
  if ('transaccion' in datos) pintarSello(datos.transaccion, datos.ocupada_por);
  return datos;
}

/* Lo que hacen los botones: ejecuta, deja el aviso con la orden escrita y vuelve a pintar. */
async function hacer(orden, { base = estado.ruta.base, despues } = {}) {
  const datos = await correr(orden, base);
  estado.aviso = { orden: ocultarClaves(orden), datos };
  if (!datos.error && despues) despues(datos);
  await mostrar();
  return datos;
}

/* ------------------------------------------------------------------ rutas */
function leerRuta() {
  const q = new URLSearchParams(location.hash.replace(/^#\/?\??/, ''));
  return { base: q.get('base'), tabla: q.get('tabla'), p: q.get('p') };
}

function enlace({ base = null, tabla = null, p = null } = {}) {
  const q = new URLSearchParams();
  if (base) q.set('base', base);
  if (base && tabla) q.set('tabla', tabla);
  if (p) q.set('p', p);
  const s = q.toString();
  return '#/' + (s ? `?${s}` : '');
}

async function ir(ruta) {
  const destino = enlace(ruta);
  if (location.hash !== destino) history.pushState(null, '', destino);
  await mostrar();
}

const nivel = r => r.tabla ? 'tabla' : r.base ? 'base' : 'servidor';

const PESTANAS = {
  servidor: [
    ['bases', 'Bases'], ['codigo', 'Código'],
    ['usuarios', 'Usuarios', puedeTodo], ['cuenta', 'Mi cuenta', esCuenta], ['copias', 'Copias e historial', puedeCrearBases]],
  base: [
    ['tablas', 'Tablas'], ['codigo', 'Código'], ['nueva', 'Nueva tabla', puedeEscribir],
    ['importar', 'Importar', puedeEscribir], ['exportar', 'Exportar'], ['operaciones', 'Operaciones', puedeEscribir]],
  tabla: [
    ['examinar', 'Examinar'], ['estructura', 'Estructura'], ['codigo', 'Código'], ['buscar', 'Buscar'],
    ['insertar', 'Insertar', () => puedeEscribir() && estado.meta?.tipo === 'tabla'], ['operaciones', 'Operaciones', puedeEscribir]],
};

const pestanasDe = r => PESTANAS[nivel(r)].filter(([, , puede]) => !puede || puede());

/* ---------------------------------------------------------------- cargar */
async function cargarTodo() {
  const datos = await api('/api/bases');
  estado.palabras = new Set(datos.palabras);
  estado.funciones = datos.funciones;
  estado.directorio = datos.directorio;
  estado.bases = datos.bases;
  const r = estado.ruta;
  if (r.base && !estado.bases.some(b => b.nombre === r.base)) Object.assign(r, { base: null, tabla: null });
  const abiertas = [...new Set([...estado.abiertas, r.base])].filter(b => b && estado.bases.some(x => x.nombre === b));
  const infos = await Promise.all(abiertas.map(b => api(`/api/base?nombre=${encodeURIComponent(b)}`).catch(() => null)));
  estado.infos = new Map(abiertas.map((b, i) => [b, infos[i]]).filter(([, i]) => i));
  estado.info = r.base ? estado.infos.get(r.base) ?? null : null;
  if (r.tabla && !(estado.info?.tablas.some(t => t.nombre === r.tabla) || estado.info?.vistas.includes(r.tabla))) r.tabla = null;
  estado.meta = r.tabla
    ? await api(`/api/tabla?base=${encodeURIComponent(r.base)}&nombre=${encodeURIComponent(r.tabla)}`) : null;
  if (estado.info) pintarSello(estado.info.transaccion, estado.info.ocupada_por);
}

let turno = 0;  // si se pide pintar otra vez antes de acabar, gana la última petición

async function mostrar() {
  const mio = ++turno;
  const pedida = leerRuta();
  estado.ruta = { ...pedida };
  try {
    await cargarTodo();
  } catch (err) {
    if (mio === turno && !$('#ficha').open) $('#vista').replaceChildren(vacio('Sin conexión', err.message));
    return;
  }
  if (mio !== turno) return;
  const r = estado.ruta;
  const validas = pestanasDe(r).map(([id]) => id);
  if (!validas.includes(r.p)) r.p = validas[0];
  if (r.base !== pedida.base || r.tabla !== pedida.tabla) history.replaceState(null, '', enlace(r));
  if (r.p !== 'codigo') estado.codigo = null;

  cerrarSugerencias();
  pintarIndice();
  pintarCabecera();
  pintarPestanas();
  const aviso = estado.aviso;
  estado.aviso = null;
  const vista = $('#vista');
  let contenido;
  try {
    contenido = await VISTAS[r.p]();
  } catch (err) {
    contenido = vacio('No se ha podido abrir', err.message);
  }
  if (mio !== turno) return;
  vista.replaceChildren(...[aviso && pintarAviso(aviso), contenido].flat().filter(Boolean));
  vista.querySelector('[data-foco]')?.focus();
}

/* -------------------------------------------------------- índice (lateral) */
function pintarQuien() {
  const quien = $('#quien');
  quien.classList.toggle('abierto', estado.sinUsuarios);
  if (estado.sinUsuarios) {
    quien.replaceChildren('Sin usuarios: el panel solo se abre desde este equipo. ',
      h('a', { class: 'enlace', href: enlace({ p: 'usuarios' }) }, 'crear uno'));
  } else if (estado.usuario) {
    const permiso = { leer: 'puede leer', escribir: 'puede escribir', todo: 'puede todo', cuenta: 'cuenta propia' }[estado.usuario.permiso];
    quien.replaceChildren(h('b', {}, estado.usuario.nombre), ` · ${permiso}`,
      h('button', { class: 'enlace', type: 'button', onclick: salir }, 'salir'));
  }
}

function pintarIndice() {
  $('#directorio').textContent = estado.directorio;
  $('#nueva-base').hidden = !puedeCrearBases();
  $('#otras-paginas').replaceChildren(...estado.enlaces.map(e => h('a', { class: 'enlace', href: e.url }, `${e.texto} →`)));
  pintarQuien();
  const filtro = $('#filtro-indice').value.trim().toLowerCase();
  const r = estado.ruta;
  const arbol = $('#arbol');
  if (!estado.bases.length) {
    arbol.replaceChildren(h('li', { class: 'vacio-indice' }, 'Aún no hay ninguna base.'));
    return;
  }
  arbol.replaceChildren(...estado.bases.map(b => {
    const info = estado.infos.get(b.nombre);
    const abierta = Boolean(info) && (estado.abiertas.has(b.nombre) || b.nombre === r.base);
    const actual = b.nombre === r.base;
    const hojas = abierta ? [
      ...info.tablas.map(t => ({ n: t.nombre, cifra: cifra(t.filas), vista: false })),
      ...info.vistas.map(v => ({ n: v, cifra: 'vista', vista: true }))]
      .filter(t => !filtro || t.n.toLowerCase().includes(filtro)) : [];
    const plegar = h('button', { class: 'plegar', type: 'button', 'aria-expanded': String(abierta),
      'aria-label': `${abierta ? 'Plegar' : 'Desplegar'} ${b.nombre}`, onclick: () => plegarBase(b.nombre, !abierta) },
    h('span', { 'aria-hidden': 'true' }, abierta ? '▾' : '▸'));
    return h('li', { class: `rama${actual ? ' actual' : ''}` },
      h('div', { class: 'rama-cab' }, plegar,
        h('a', { class: 'base', href: enlace({ base: b.nombre }), 'aria-current': actual && !r.tabla ? 'page' : null },
          h('span', { class: 'nombre' }, b.nombre)),
        h('span', { class: 'cifra' }, plural(b.tablas, 'tabla', 'tablas'))),
      abierta && h('ul', { class: 'hojas' },
        hojas.length ? hojas.map(t => h('li', {},
          h('a', { class: `hoja${t.vista ? ' es-vista' : ''}`, href: enlace({ base: b.nombre, tabla: t.n }),
            'aria-current': actual && r.tabla === t.n ? 'page' : null },
          h('span', { class: 'nombre' }, t.n), h('span', { class: 'relleno', 'aria-hidden': 'true' }),
          h('span', { class: 'cifra' }, t.cifra))))
          : h('li', { class: 'vacio-indice' }, filtro ? 'Ninguna tabla coincide.' : 'Sin tablas todavía.'),
        actual && puedeEscribir() && h('li', { class: 'nueva-tabla' },
          h('a', { class: 'enlace', href: enlace({ base: b.nombre, p: 'nueva' }) }, '+ nueva tabla'))));
  }));
}

async function plegarBase(base, abrir) {
  if (abrir) estado.abiertas.add(base); else estado.abiertas.delete(base);
  guardar('sbd.abiertas', [...estado.abiertas]);
  if (abrir && !estado.infos.has(base)) {
    try { estado.infos.set(base, await api(`/api/base?nombre=${encodeURIComponent(base)}`)); } catch { /* se ve en el aviso al abrirla */ }
  }
  pintarIndice();
}

/* --------------------------------------------------------------- cabecera */
function pintarCabecera() {
  const r = estado.ruta;
  const migas = [h('a', { href: enlace() }, 'todas las bases')];
  if (r.base) migas.push(h('a', { href: enlace({ base: r.base }) }, r.base));
  if (r.tabla) migas.push(h('a', { href: enlace({ base: r.base, tabla: r.tabla }) }, r.tabla));
  $('#migas').replaceChildren(...migas.flatMap((m, i) => i ? [h('span', { 'aria-hidden': 'true' }, '›'), m] : [m]));
  migas.at(-1).setAttribute('aria-current', 'page');

  const titulo = $('#titulo');
  const meta = $('#meta');
  titulo.className = 'titulo';
  if (estado.meta) {
    const m = estado.meta;
    titulo.textContent = m.nombre;
    meta.textContent = m.tipo === 'vista' ? 'vista: una consulta guardada con nombre'
      : [plural(m.filas, 'fila', 'filas'), plural(m.columnas.length, 'columna', 'columnas'),
        m.clave.length ? `clave ${m.clave.join(', ')}` : 'sin clave'].join(' · ');
  } else if (estado.info) {
    const filas = estado.info.tablas.reduce((n, t) => n + t.filas, 0);
    titulo.textContent = estado.info.nombre;
    meta.textContent = `${plural(estado.info.tablas.length, 'tabla', 'tablas')} · ${plural(filas, 'fila', 'filas')} · ${bytes(estado.info.bytes)}`;
  } else {
    titulo.textContent = 'Todas las bases';
    titulo.classList.add('recto');
    meta.textContent = plural(estado.bases.length, 'base', 'bases') + (estado.directorio ? ` en ${estado.directorio}` : '');
  }
  document.title = [r.tabla, r.base, 'SBD'].filter(Boolean).join(' · ');
}

function pintarPestanas() {
  const r = estado.ruta;
  $('#pestanas').replaceChildren(...pestanasDe(r).map(([id, texto]) => h('a', {
    class: `pestana${id === 'codigo' ? ' de-codigo' : ''}`, href: enlace({ ...r, p: id }),
    'aria-current': id === r.p ? 'page' : null }, texto)));
}

function pintarSello(abierta, ocupadaPor) {
  const sello = $('#sello');
  sello.hidden = !abierta && !ocupadaPor;
  sello.classList.toggle('ajeno', !abierta && Boolean(ocupadaPor));
  sello.querySelector('span').textContent = abierta ? 'transacción abierta' : `ocupada por ${ocupadaPor}`;
  sello.querySelectorAll('button').forEach(b => { b.hidden = !abierta; });
}

/* ------------------------------------------------------------- piezas */
function vacio(titulo, texto, ...acciones) {
  return h('div', { class: 'vacio' }, h('h2', {}, titulo), texto && h('p', {}, texto),
    acciones.length > 0 && h('div', { class: 'vacio-acciones' }, acciones));
}

function seccion(titulo, ...contenido) {
  return h('section', { class: 'seccion' }, h('h2', {}, titulo), contenido);
}

function boton(texto, onclick, clase = 'boton-linea') {
  return h('button', { class: clase, type: 'button', onclick }, texto);
}

function accion(texto, onclick, clase = '') {
  return h('button', { class: `accion ${clase}`.trim(), type: 'button', onclick }, texto);
}

function valorCelda(v, tipoBase) {
  if (v === null) return h('span', { class: 'nulo' }, 'nulo');
  if (v === true) return 'verdadero';
  if (v === false) return 'falso';
  if (typeof v === 'number' && tipoBase === 'REAL' && Number.isInteger(v)) return v.toFixed(1).replace('.', ',');
  if (typeof v === 'number' || tipoBase === 'MONEY') return String(v).replace('.', ',');
  return String(v);
}

const BASE_DE_TIPO = { entero: 'INTEGER', decimal: 'REAL', dinero: 'MONEY', logico: 'BOOLEAN' };

/* Una tabla de solo lectura con el resultado de una consulta. */
function tablaEstatica(r) {
  const tipos = (r.tipos || []).map(t => BASE_DE_TIPO[t] || null);
  const esNumero = j => NUMERICOS.has(tipos[j]) || r.filas.some(f => typeof f[j] === 'number');
  return h('div', { class: 'hoja-datos' }, h('table', { class: 'registro' },
    h('thead', {}, h('tr', {}, r.columnas.map((c, j) => h('th', { class: esNumero(j) ? 'num' : null }, c)))),
    h('tbody', {}, r.filas.map(f => h('tr', {}, f.map((v, j) => h('td', {
      class: esNumero(j) ? 'num' : null, title: v === null ? 'nulo' : String(v) }, valorCelda(v, tipos[j]))))))));
}

function pieResultado(r) {
  return h('p', { class: 'mensaje' }, r.columnas ? plural(r.total, 'fila', 'filas') : r.mensaje,
    r.recortado ? ` (se muestran ${cifra(r.filas.length)})` : '', h('span', { class: 'ms' }, ` · ${r.ms} ms`));
}

/* Lo que acaba de pasar al pulsar un botón, con la orden escrita en SBD. */
function pintarAviso({ orden, datos }) {
  const bien = !datos.error;
  const ultimo = datos.resultados.at(-1);
  const soloCuenta = m => !m || /^\d+ fila/.test(m);  // «3 fila(s)»: mejor decir cuántas con plural()
  const titulo = bien ? (ultimo?.columnas && soloCuenta(ultimo.mensaje) ? plural(ultimo.total, 'fila', 'filas') : ultimo?.mensaje || 'hecho') : datos.error;
  const tablas = datos.resultados.filter(r => r.columnas && r.filas.length);
  const clave = tablas.flatMap(r => r.columnas[0] === 'clave' ? [r.filas[0][0]] : [])[0];  // «crear clave api»
  return h('div', { class: `aviso ${bien ? 'bien' : 'mal'}`, role: bien ? 'status' : 'alert' },
    h('div', { class: 'aviso-cab' },
      h('b', {}, bien ? 'hecho' : 'no se pudo'),
      h('span', { class: 'aviso-texto' }, titulo),
      !llevaClave(orden) && !orden.includes('••••') && h('button', { class: 'enlace', type: 'button', onclick: () => llevarACodigo(orden) }, 'editar en Código')),
    preOrden(orden),
    tablas.map(tablaEstatica),
    clave && h('div', { class: 'barra' }, h('button', { class: 'boton-tinta', type: 'button', onclick: async ev => {
      try { await navigator.clipboard.writeText(clave); ev.target.textContent = 'copiada'; }
      catch { ev.target.textContent = 'selecciónala y cópiala a mano'; }
    } }, 'copiar la clave'), h('span', { class: 'pista' }, 'Guárdala ya: no se volverá a mostrar.')));
}

function claveBorrador(r = estado.ruta) {
  return `sbd.borrador:${r.base || ''}:${r.tabla || ''}`;
}

function llevarACodigo(texto, r = estado.ruta) {
  guardar(claveBorrador(r), texto);
  estado.codigo = null;
  ir({ ...r, p: 'codigo' });
}

/* -------------------------------------------------------------- diálogos */
function abrirDialogo({ titulo, folio, cuerpo, pie, clase = '', alCerrar }) {
  const dialogo = $('#ficha');
  if (dialogo.open) {
    const previo = dialogo.alCerrar;  // la ficha anterior se da por cerrada (cancelada) ya
    dialogo.close();
    previo?.();
  }
  dialogo.alCerrar = alCerrar;
  dialogo.className = `ficha ${clase}`.trim();
  dialogo.replaceChildren(h('div', { class: 'ficha-cab' }, h('h2', {}, titulo), folio && h('span', { class: 'folio' }, folio)),
    cuerpo, pie);
  // El «close» de una ficha anterior llega después: si para entonces ya hay otra abierta, no es suyo.
  dialogo.onclose = () => {
    if (dialogo.open) return;
    dialogo.replaceChildren();
    dialogo.className = 'ficha';
    delete dialogo.dataset.tipo;
    dialogo.alCerrar = null;
    alCerrar?.();
  };
  dialogo.oncancel = null;
  dialogo.showModal();
  return dialogo;
}

/* Lo que destruye datos se confirma viendo la orden exacta que se va a ejecutar. */
function confirmar({ titulo, orden, texto, boton: etiqueta = 'borrar' }) {
  return new Promise(resolver => {
    let respuesta = false;
    const si = h('button', { class: 'boton-tinta peligro', type: 'submit' }, etiqueta);
    const dialogo = abrirDialogo({
      titulo, folio: 'confirmar', clase: 'confirmar',
      cuerpo: h('div', { class: 'ficha-cuerpo' }, texto && h('p', { class: 'explicacion' }, texto), preOrden(orden, 'orden grande')),
      pie: h('form', { class: 'ficha-pie', method: 'dialog', onsubmit: ev => { ev.preventDefault(); respuesta = true; dialogo.close(); } },
        h('div', { class: 'botones' },
          h('button', { class: 'enlace', type: 'button', onclick: () => dialogo.close() }, 'cancelar'), si)),
      alCerrar: () => resolver(respuesta),
    });
    si.focus();
  });
}

async function hacerSiConfirma(opciones) {
  if (await confirmar(opciones)) return hacer(opciones.orden, opciones);
  return null;
}

/* Pide un nombre (o un texto) enseñando la orden que se va a ejecutar con él. */
function pedir({ titulo, etiqueta, valor = '', tipo = 'text', construir, boton: textoBoton = 'hecho', despues }) {
  const input = h('input', { type: tipo, id: 'pedir-valor', value: valor, spellcheck: 'false', autocomplete: tipo === 'password' ? 'new-password' : 'off' });
  const vista = h('pre');
  const fallo = h('div', { class: 'fallo', hidden: true });
  const actualizar = () => { vista.innerHTML = resaltar(ocultarClaves(construir(input.value.trim() || '…'))); };
  input.addEventListener('input', actualizar);
  actualizar();
  const enviarBoton = h('button', { class: 'boton-tinta', type: 'submit' }, textoBoton);
  const dialogo = abrirDialogo({
    titulo, folio: 'cambiar',
    cuerpo: h('div', { class: 'ficha-cuerpo' }, h('label', { class: 'campo-grande', for: 'pedir-valor' }, h('span', {}, etiqueta), input)),
    pie: h('form', { class: 'ficha-pie', method: 'dialog', onsubmit: async ev => {
      ev.preventDefault();
      const v = tipo === 'password' ? input.value : input.value.trim();
      if (!v) { input.focus(); return; }
      enviarBoton.disabled = true;
      const orden = construir(v);
      const datos = await correr(orden);
      enviarBoton.disabled = false;
      if (datos.error) { fallo.hidden = false; fallo.replaceChildren(h('b', {}, 'no se pudo'), datos.error); return; }
      estado.aviso = { orden: ocultarClaves(orden), datos };
      despues?.(v, datos);
      dialogo.close();
      await mostrar();
    } }, fallo, vista, h('div', { class: 'botones' },
      h('button', { class: 'enlace', type: 'button', onclick: () => dialogo.close() }, 'cancelar'), enviarBoton)),
  });
  input.select();
}

/* ================================================================ VISTAS */

/* ---------------------------------------------------- servidor: bases */
function vistaBases() {
  const partes = [];
  if (puedeCrearBases()) {
    const input = h('input', { type: 'text', id: 'nueva-base-nombre', placeholder: 'tienda', spellcheck: 'false', autocomplete: 'off',
      'data-foco': estado.bases.length ? null : '' });
    const vista = h('pre', { class: 'orden-viva' });
    const actualizar = () => { vista.innerHTML = resaltar(`crear base ${nombre(input.value.trim() || 'nombre')}`); };
    input.addEventListener('input', actualizar);
    actualizar();
    partes.push(seccion('Crear una base',
      h('form', { class: 'en-linea', onsubmit: ev => {
        ev.preventDefault();
        const n = input.value.trim();
        if (!n) { input.focus(); return; }
        hacer(`crear base ${nombre(n)}`, { base: null, despues: () => history.pushState(null, '', enlace({ base: n })) });
      } },
      h('label', { class: 'campo', for: 'nueva-base-nombre' }, h('span', {}, 'nombre'), input),
      h('button', { class: 'boton-tinta', type: 'submit' }, 'crear base')), vista));
  }
  if (!estado.bases.length) {
    partes.unshift(esCuenta()
      ? vacio(`Bienvenida, bienvenido: ${estado.usuario.nombre}.`, 'Empieza creando tu primera base de datos. Dentro podrás crear tablas con formularios o escribiéndolas en la pestaña «Código».')
      : vacio('El archivador está vacío.', 'Cada base es un fichero propio dentro de la carpeta de datos. Crea la primera.'));
    return partes;
  }
  partes.unshift(seccion('Bases de datos', h('div', { class: 'hoja-datos' }, h('table', { class: 'registro' },
    h('thead', {}, h('tr', {}, h('th', {}, 'base'), h('th', { class: 'num' }, 'tablas'), h('th', { class: 'num' }, 'tamaño'),
      h('th', {}, h('span', { class: 'visualmente-oculto' }, 'acciones')))),
    h('tbody', {}, estado.bases.map(b => h('tr', {},
      h('td', {}, h('a', { class: 'fuerte', href: enlace({ base: b.nombre }) }, b.nombre),
        b.transaccion && h('span', { class: 'etiqueta' }, 'transacción abierta')),
      h('td', { class: 'num' }, cifra(b.tablas)),
      h('td', { class: 'num' }, bytes(b.bytes)),
      h('td', { class: 'acciones' },
        h('a', { class: 'accion', href: enlace({ base: b.nombre }) }, 'abrir'),
        h('a', { class: 'accion', href: enlace({ base: b.nombre, p: 'codigo' }) }, 'código'),
        h('a', { class: 'accion', href: `/api/volcar?base=${encodeURIComponent(b.nombre)}&formato=sbd` }, 'exportar'),
        puedeCrearBases() && accion('borrar', () => hacerSiConfirma({
          titulo: `Borrar la base ${b.nombre}`, orden: `borrar base ${nombre(b.nombre)}`, base: null,
          texto: 'La base va a la papelera: se puede recuperar desde «Copias e historial».' }), 'peligro')))))))));
  return partes;
}

/* ------------------------------------------------------- base: tablas */
function vistaTablas() {
  const info = estado.info;
  const base = info.nombre;
  if (!info.tablas.length && !info.vistas.length && !info.procedimientos.length) {
    return vacio(`«${base}» todavía no tiene tablas.`,
      'Diséñala con el formulario o escríbela en código: las dos cosas crean lo mismo.',
      puedeEscribir() && h('a', { class: 'boton-tinta', href: enlace({ base, p: 'nueva' }) }, 'crear la primera tabla'),
      h('a', { class: 'boton-linea', href: enlace({ base, p: 'codigo' }) }, 'escribir código'),
      puedeEscribir() && h('a', { class: 'boton-linea', href: enlace({ base, p: 'importar' }) }, 'importar un CSV o Excel'));
  }
  const filasTotales = info.tablas.reduce((n, t) => n + t.filas, 0);
  const fila = t => h('tr', {},
    h('td', {}, h('a', { class: 'fuerte', href: enlace({ base, tabla: t.nombre }) }, t.nombre)),
    h('td', { class: 'num' }, cifra(t.filas)),
    h('td', { class: 'num' }, cifra(t.columnas.length)),
    h('td', { class: 'tenue' }, t.clave.join(', ') || '—'),
    h('td', { class: 'acciones' },
      h('a', { class: 'accion', href: enlace({ base, tabla: t.nombre, p: 'examinar' }) }, 'examinar'),
      h('a', { class: 'accion', href: enlace({ base, tabla: t.nombre, p: 'estructura' }) }, 'estructura'),
      puedeEscribir() && h('a', { class: 'accion', href: enlace({ base, tabla: t.nombre, p: 'insertar' }) }, 'insertar'),
      puedeEscribir() && accion('vaciar', () => hacerSiConfirma({ titulo: `Vaciar ${t.nombre}`, orden: `vaciar ${nombre(t.nombre)}`,
        boton: 'vaciar', texto: `Se borran sus ${plural(t.filas, 'fila', 'filas')}; la tabla y su estructura se quedan.` }), 'peligro'),
      puedeEscribir() && accion('borrar', () => hacerSiConfirma({ titulo: `Borrar la tabla ${t.nombre}`,
        orden: `borrar tabla ${nombre(t.nombre)}`, texto: 'Se borran la tabla, su estructura y todas sus filas.' }), 'peligro')));
  return [seccion('Tablas', h('div', { class: 'hoja-datos' }, h('table', { class: 'registro' },
    h('thead', {}, h('tr', {}, h('th', {}, 'tabla'), h('th', { class: 'num' }, 'filas'), h('th', { class: 'num' }, 'columnas'),
      h('th', {}, 'clave'), h('th', {}, h('span', { class: 'visualmente-oculto' }, 'acciones')))),
    h('tbody', {}, info.tablas.map(fila),
      info.vistas.map(v => h('tr', { class: 'es-vista' },
        h('td', {}, h('a', { class: 'fuerte', href: enlace({ base, tabla: v }) }, v)),
        h('td', { class: 'num tenue' }, 'vista'), h('td'), h('td'),
        h('td', { class: 'acciones' },
          h('a', { class: 'accion', href: enlace({ base, tabla: v }) }, 'examinar'),
          puedeEscribir() && accion('borrar', () => hacerSiConfirma({ titulo: `Borrar la vista ${v}`, orden: `borrar vista ${nombre(v)}`,
            texto: 'Solo se borra la consulta guardada; los datos de las tablas no se tocan.' }), 'peligro'))))),
    h('tfoot', {}, h('tr', {}, h('td', {}, plural(info.tablas.length, 'tabla', 'tablas')), h('td', { class: 'num' }, cifra(filasTotales)),
      h('td', { colspan: '3', class: 'tenue' }, bytes(info.bytes))))))), seccionProcedimientos(info)];
}

/* Los procedimientos de la base: órdenes guardadas que se ejecutan juntas, todo o nada. */
function seccionProcedimientos(info) {
  return seccion('Procedimientos',
    info.procedimientos.length ? h('ul', { class: 'lista-simple' }, info.procedimientos.map(p => h('li', {},
      h('details', { class: 'procedimiento' },
        h('summary', {}, h('b', {}, p.nombre), `(${p.parametros.join(', ')})`), preOrden(p.definicion, 'definicion')),
      h('span', { class: 'empuje' }),
      accion('ejecutar', () => llevarACodigo(`ejecutar ${nombre(p.nombre)}(${p.parametros.join(', ')})`)),
      puedeEscribir() && accion('borrar', () => hacerSiConfirma({ titulo: `Borrar el procedimiento ${p.nombre}`,
        orden: `borrar procedimiento ${nombre(p.nombre)}`, texto: 'Solo se borra el procedimiento; las tablas y sus datos no se tocan.' }), 'peligro'))))
      : h('p', { class: 'mensaje' }, 'Todavía no hay ninguno. Guardan varias órdenes que se ejecutan juntas: si una falla, no se aplica ninguna.'),
    puedeEscribir() && h('button', { class: 'boton-linea', type: 'button', onclick: () => llevarACodigo(PLANTILLA_PROCEDIMIENTO) }, 'crear uno en Código'));
}

/* ----------------------------------------------------- tabla: examinar */
function claveDeFila(meta, columnas, fila) {
  const cols = meta.clave.length ? meta.clave : columnas;
  return Object.fromEntries(cols.map(c => [c, fila[columnas.indexOf(c)]]));
}

function condicion(clave) {
  return Object.entries(clave).map(([c, v]) => v === null ? `${nombre(c)} es nulo` : `${nombre(c)} = ${literal(v)}`).join(' y ');
}

function ordenExaminar(v, meta) {
  const t = nombre(meta.nombre);
  const donde = v.filtro
    ? ' donde ' + meta.columnas.map(c => `texto(${nombre(c.nombre)}) contiene ${literal(v.filtro)}`).join(' o ')
    : '';
  const ordenar = v.orden ? ` ordenar ${nombre(v.orden)}${v.desc ? ' desc' : ''}` : '';
  const saltar = v.pagina > 1 ? ` saltar ${(v.pagina - 1) * v.tam}` : '';
  return { principal: `buscar ${t}${donde}${ordenar} limite ${v.tam}${saltar}`, cuenta: `buscar ${t}${donde} mostrar contar() como total` };
}

function vistaExaminar() {
  const meta = estado.meta;
  const base = estado.ruta.base;
  let v = estado.examinar;
  if (!v || v.tabla !== meta.nombre || v.base !== base) {
    v = estado.examinar = { base, tabla: meta.nombre, orden: null, desc: false, pagina: 1, tam: leer('sbd.tam', 50), filtro: '', marcadas: new Set() };
  }
  const editable = meta.tipo === 'tabla' && puedeEscribir();
  const zona = h('div', { class: 'zona-datos' }, h('p', { class: 'trabajando' }, 'cargando'));
  const linea = h('div', { class: 'orden-ejecutada' });
  const filtro = h('input', { type: 'search', id: 'filtro-filas', value: v.filtro, placeholder: 'palabra o número', spellcheck: 'false',
    oninput: retrasar(() => { v.filtro = filtro.value.trim(); v.pagina = 1; cargar(); }, 300) });
  const borrarMarcadas = h('button', { class: 'boton-linea peligro', type: 'button', hidden: true, onclick: () => {
    const claves = [...v.marcadas].map(k => JSON.parse(k));
    hacerSiConfirma({ titulo: `Borrar ${plural(claves.length, 'fila', 'filas')}`, orden: ordenBorrar(meta.nombre, claves),
      texto: 'Las filas marcadas se borran de la tabla.' }).then(d => { if (d && !d.error) v.marcadas.clear(); });
  } });
  const pintarMarcadas = () => {
    borrarMarcadas.hidden = v.marcadas.size === 0;
    borrarMarcadas.textContent = `borrar ${plural(v.marcadas.size, 'marcada', 'marcadas')}`;
  };

  async function cargar() {
    const { principal, cuenta } = ordenExaminar(v, meta);
    const datos = await correr(`${cuenta}\n${principal}`, base);
    linea.replaceChildren(preOrden(principal), h('button', { class: 'enlace', type: 'button', onclick: () => llevarACodigo(principal) }, 'editar en Código'));
    if (datos.error) { zona.replaceChildren(h('div', { class: 'fallo' }, h('b', {}, 'no se pudo'), datos.error)); return; }
    v.total = datos.resultados[0].filas[0][0];
    const r = datos.resultados[1];
    zona.replaceChildren(
      r.filas.length ? h('div', { class: 'hoja-datos alta' }, rejilla(r)) : h('p', { class: 'mensaje' },
        v.filtro ? `Ninguna fila contiene «${v.filtro}».` : 'La tabla está vacía.',
        editable && !v.filtro && h('a', { class: 'enlace', href: enlace({ ...estado.ruta, p: 'insertar' }) }, ' insertar la primera')),
      paginas(r));
    pintarMarcadas();
  }

  function ordenar(col) {
    if (v.orden === col && v.desc) { v.orden = null; v.desc = false; }
    else if (v.orden === col) v.desc = true;
    else { v.orden = col; v.desc = false; }
    cargar();
  }

  function rejilla(r) {
    const colMeta = Object.fromEntries(meta.columnas.map(c => [c.nombre, c]));
    const esNumero = j => NUMERICOS.has(colMeta[r.columnas[j]]?.tipo_base) || r.filas.some(f => typeof f[j] === 'number');
    const cabecera = h('tr', {},
      editable && h('th', { class: 'sel' }, h('span', { class: 'visualmente-oculto' }, 'Marcar')),
      editable && h('th', { class: 'ops' }, h('span', { class: 'visualmente-oculto' }, 'Acciones')),
      r.columnas.map((col, j) => {
        const c = colMeta[col];
        const ordenada = v.orden === col;
        return h('th', { class: esNumero(j) ? 'num' : null, 'aria-sort': ordenada ? (v.desc ? 'descending' : 'ascending') : null },
          h('button', { type: 'button', title: `ordenar por ${col}`, onclick: () => ordenar(col) },
            col, c?.clave && h('span', { class: 'marca-clave', title: 'clave' }, '●'),
            ordenada && h('span', { class: 'flecha', 'aria-hidden': 'true' }, v.desc ? ' ↓' : ' ↑'),
            c?.tipo && h('small', {}, c.tipo + (c.ref ? ` → ${c.ref.tabla}` : ''))));
      }));
    const cuerpo = r.filas.map(fila => {
      const clave = editable ? claveDeFila(meta, r.columnas, fila) : null;
      const id = clave && JSON.stringify(clave);
      const tr = h('tr', { class: id && v.marcadas.has(id) ? 'marcada' : null });
      if (editable) {
        tr.append(h('td', { class: 'sel' }, h('input', { type: 'checkbox', checked: v.marcadas.has(id), 'aria-label': 'Marcar fila',
          onchange: ev => {
            ev.target.checked ? v.marcadas.add(id) : v.marcadas.delete(id);
            tr.classList.toggle('marcada', ev.target.checked);
            pintarMarcadas();
          } })));
        tr.append(h('td', { class: 'ops' },
          accion('editar', () => abrirFicha(meta, { columnas: r.columnas, fila, clave })),
          accion('copiar', () => abrirFicha(meta, { columnas: r.columnas, fila, copia: true })),
          accion('borrar', () => hacerSiConfirma({ titulo: 'Borrar una fila', orden: ordenBorrar(meta.nombre, [clave]) }), 'peligro')));
      }
      fila.forEach((valor, j) => {
        const c = colMeta[r.columnas[j]];
        const td = h('td', { class: `${esNumero(j) ? 'num' : ''}${editable ? ' editable' : ''}`,
          title: valor === null ? 'nulo' : editable ? `${valor} · doble clic para cambiarlo` : String(valor) }, valorCelda(valor, c?.tipo_base));
        if (editable) td.addEventListener('dblclick', () => editarCelda(td, meta, c, valor, clave));
        tr.append(td);
      });
      return tr;
    });
    return h('table', { class: 'registro' }, h('thead', {}, cabecera), h('tbody', {}, cuerpo));
  }

  function paginas(r) {
    const total = v.total ?? 0;
    const ultima = Math.max(1, Math.ceil(total / v.tam));
    const desde = total ? (v.pagina - 1) * v.tam + 1 : 0;
    const hasta = Math.min(v.pagina * v.tam, total);
    const irA = p => { v.pagina = p; cargar(); };
    return h('div', { class: 'paginas' },
      h('button', { type: 'button', disabled: v.pagina <= 1, onclick: () => irA(1) }, '« primera'),
      h('button', { type: 'button', disabled: v.pagina <= 1, onclick: () => irA(v.pagina - 1) }, '‹ anterior'),
      h('span', {}, total ? `filas ${cifra(desde)}–${cifra(hasta)} de ${cifra(total)}` : '0 filas'),
      h('button', { type: 'button', disabled: v.pagina >= ultima, onclick: () => irA(v.pagina + 1) }, 'siguiente ›'),
      h('button', { type: 'button', disabled: v.pagina >= ultima, onclick: () => irA(ultima) }, 'última »'),
      h('label', {}, h('select', { value: String(v.tam), 'aria-label': 'Filas por página',
        onchange: ev => { v.tam = Number(ev.target.value); v.pagina = 1; guardar('sbd.tam', v.tam); cargar(); } },
      TAMANOS.map(n => h('option', { value: String(n) }, n))), ' por página'),
      h('span', { class: 'ms' }, `${r.ms} ms`));
  }

  cargar();
  return [
    h('div', { class: 'barra' },
      h('label', { class: 'filtro', for: 'filtro-filas' }, h('span', {}, 'filtrar'), filtro),
      editable && h('a', { class: 'boton-linea', href: enlace({ ...estado.ruta, p: 'insertar' }) }, '+ insertar fila'),
      borrarMarcadas,
      h('span', { class: 'empuje' }),
      h('a', { class: 'enlace', href: `/api/volcar?base=${encodeURIComponent(base)}&tabla=${encodeURIComponent(meta.nombre)}&formato=csv` }, 'descargar CSV')),
    linea, zona,
    editable && h('p', { class: 'pista' }, 'Doble clic en una celda para cambiarla. ⏎ guarda, Esc deja como estaba.')];
}

function ordenBorrar(tabla, claves) {
  const cols = Object.keys(claves[0]);
  let donde;
  if (cols.length === 1 && claves.every(k => k[cols[0]] !== null)) {
    donde = claves.length === 1 ? `${nombre(cols[0])} = ${literal(claves[0][cols[0]])}`
      : `${nombre(cols[0])} en [${claves.map(k => literal(k[cols[0]])).join(', ')}]`;
  } else {
    donde = claves.map(k => (claves.length > 1 ? `(${condicion(k)})` : condicion(k))).join(' o ');
  }
  return `borrar de ${nombre(tabla)} donde ${donde}`;
}

function convertir(col, texto, { vacioEsNulo }) {
  if (texto === '') return vacioEsNulo ? null : '';
  const t = texto.trim();
  if (col.tipo_base === 'BOOLEAN') return /^(verdadero|v|si|sí|1|true)$/i.test(t);
  if (col.tipo_base === 'MONEY' && /^-?\d+([.,]\d+)?$/.test(t)) return new Cifra(t.replace(',', '.'));
  if ((col.tipo_base === 'INTEGER' || col.tipo_base === 'REAL') && t && !Number.isNaN(Number(t.replace(',', '.')))) {
    return Number(t.replace(',', '.'));
  }
  if (col.tipo_base === 'TIMESTAMP') return texto.replace('T', ' ');
  return texto;
}

function igual(a, b) {
  if (a === null || b === null) return a === b;
  return String(a) === String(b);
}

function editarCelda(td, meta, col, valor, clave) {
  if (!col || td.querySelector('input')) return;
  const input = h('input', { type: 'text', value: valor === null ? '' : String(valor), 'aria-label': `Nuevo valor de ${col.nombre}` });
  const original = [...td.childNodes];
  td.replaceChildren(input);
  input.focus();
  input.select();
  let hecho = false;
  const terminar = guardarlo => {
    if (hecho) return;
    hecho = true;
    const nuevo = convertir(col, input.value, { vacioEsNulo: col.tipo_base !== 'TEXT' });
    if (!guardarlo || igual(nuevo, valor)) { td.replaceChildren(...original); return; }
    hacer(`actualizar ${nombre(meta.nombre)} poner ${nombre(col.nombre)} = ${literal(nuevo)} donde ${condicion(clave)}`);
  };
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); terminar(true); }
    if (ev.key === 'Escape') { ev.preventDefault(); terminar(false); }
  });
  input.addEventListener('blur', () => terminar(true));
}

/* --------------------------------------------- campos de una fila (ficha) */
function campoFila(col, valor, modo) {
  const id = `campo-${col.nombre}`;
  let control;
  if (col.tipo_base === 'BOOLEAN') {
    control = h('select', { id },
      h('option', { value: '' }, modo === 'nueva' ? 'por defecto' : '—'),
      h('option', { value: 'verdadero' }, 'verdadero'), h('option', { value: 'falso' }, 'falso'));
    if (valor === true) control.value = 'verdadero';
    if (valor === false) control.value = 'falso';
  } else {
    const tipoInput = { DATE: 'date', TIMESTAMP: 'datetime-local' }[col.tipo_base] || 'text';
    control = h('input', { id, type: tipoInput, step: tipoInput === 'datetime-local' ? '1' : null,
      inputmode: col.tipo_base === 'INTEGER' ? 'numeric' : NUMERICOS.has(col.tipo_base) ? 'decimal' : null,
      maxlength: col.longitud || null, spellcheck: 'false', autocomplete: 'off' });
    if (valor !== null && valor !== undefined) control.value = col.tipo_base === 'TIMESTAMP' ? String(valor).replace(' ', 'T') : String(valor);
    if (modo === 'nueva') control.placeholder = col.auto ? 'se numera sola' : col.defecto != null ? `= ${col.defecto}` : '';
  }
  let esNulo = modo !== 'nueva' && valor === null;
  const vaciar = !col.obligatorio && !col.clave && h('button', { type: 'button', class: 'vaciar', 'aria-pressed': String(esNulo),
    title: 'Dejar sin valor (nulo)', onclick: () => {
      esNulo = !esNulo;
      vaciar.setAttribute('aria-pressed', String(esNulo));
      control.disabled = esNulo;
      control.dispatchEvent(new Event('input', { bubbles: true }));
    } }, 'nulo');
  control.disabled = esNulo;
  const marcas = [col.tipo, col.clave && 'clave', col.obligatorio && !col.clave && 'obligatorio', col.ref && `→ ${col.ref.tabla}`].filter(Boolean);
  const nodo = h('div', { class: 'renglon' },
    h('label', { for: id }, col.nombre, h('small', {}, marcas.join(' · '))), control, vaciar || h('span'));
  const leerValor = () => {
    if (esNulo) return { valor: null };
    const crudo = control.value;
    if (crudo === '') return modo === 'nueva' ? { omitir: true } : { valor: col.tipo_base === 'TEXT' || !col.tipo_base ? '' : null };
    if (col.tipo_base === 'BOOLEAN') return { valor: crudo === 'verdadero' };
    return { valor: convertir(col, crudo, { vacioEsNulo: false }) };
  };
  const limpiar = () => { control.value = ''; esNulo = false; control.disabled = false; vaciar?.setAttribute('aria-pressed', 'false'); };
  return { nodo, control, leerValor, limpiar };
}

function ordenInsertar(tabla, campos) {
  const pares = campos.map(f => [f, f.leerValor()]).filter(([, r]) => !r.omitir);
  return `insertar en ${nombre(tabla)} { ${pares.map(([f, r]) => `${nombre(f.col.nombre)}: ${literal(r.valor)}`).join(', ')} }`;
}

/* Editar o copiar una fila, en una ficha encima de la tabla. */
function abrirFicha(meta, origen) {
  const editar = origen && !origen.copia;
  const modo = editar ? 'editar' : 'nueva';
  const inicial = c => origen ? origen.fila[origen.columnas.indexOf(c.nombre)] : undefined;
  const campos = meta.columnas.map(c => ({ col: c, inicial: inicial(c),
    ...campoFila(c, origen?.copia && c.auto ? undefined : inicial(c), modo) }));
  const vista = h('pre');
  const fallo = h('div', { class: 'fallo', hidden: true });
  const construir = () => {
    if (!editar) return ordenInsertar(meta.nombre, campos);
    const cambios = campos.map(f => [f, f.leerValor()]).filter(([f, r]) => !igual(r.valor, f.inicial));
    if (!cambios.length) return null;
    return `actualizar ${nombre(meta.nombre)} poner ${cambios.map(([f, r]) => `${nombre(f.col.nombre)} = ${literal(r.valor)}`).join(', ')}`
      + ` donde ${condicion(origen.clave)}`;
  };
  const actualizarVista = () => { vista.innerHTML = resaltar(construir() ?? '— sin cambios —'); };
  const guardarBoton = h('button', { class: 'boton-tinta', type: 'submit' }, editar ? 'guardar cambios' : 'insertar copia');
  const dialogo = abrirDialogo({
    titulo: meta.nombre, folio: editar ? `nº ${Object.values(origen.clave).join(' · ')}` : 'copia',
    cuerpo: h('div', { class: 'ficha-cuerpo', oninput: actualizarVista, onchange: actualizarVista }, campos.map(f => f.nodo)),
    pie: h('form', { class: 'ficha-pie', method: 'dialog', onsubmit: async ev => {
      ev.preventDefault();
      const orden = construir();
      if (!orden) { dialogo.close(); return; }
      guardarBoton.disabled = true;
      const datos = await correr(orden);
      guardarBoton.disabled = false;
      if (datos.error) { fallo.hidden = false; fallo.replaceChildren(h('b', {}, 'no se pudo'), datos.error); return; }
      estado.aviso = { orden, datos };
      dialogo.close();
      await mostrar();
    } }, fallo, vista, h('div', { class: 'botones' },
      h('button', { class: 'enlace', type: 'button', onclick: () => dialogo.close() }, 'cancelar'), guardarBoton)),
  });
  actualizarVista();
  (campos.find(f => !f.col.auto && !f.control.disabled)?.control)?.focus();
}

/* ----------------------------------------------------- tabla: insertar */
function vistaInsertar() {
  const meta = estado.meta;
  const campos = meta.columnas.map(c => ({ col: c, ...campoFila(c, undefined, 'nueva') }));
  const vista = h('pre', { class: 'orden-viva' });
  const actualizar = () => { vista.innerHTML = resaltar(ordenInsertar(meta.nombre, campos)); };
  const otra = h('input', { type: 'checkbox', id: 'insertar-otra', checked: leer('sbd.insertar-otra', false),
    onchange: ev => guardar('sbd.insertar-otra', ev.target.checked) });
  const fallo = h('div', { class: 'fallo', hidden: true });
  actualizar();
  const primero = campos.find(f => !f.col.auto);
  if (primero) primero.control.setAttribute('data-foco', '');
  return seccion('Nueva fila',
    h('form', { class: 'formulario-fila', oninput: actualizar, onchange: actualizar, onsubmit: async ev => {
      ev.preventDefault();
      const orden = ordenInsertar(meta.nombre, campos);
      const datos = await correr(orden);
      if (datos.error) { fallo.hidden = false; fallo.replaceChildren(h('b', {}, 'no se pudo'), datos.error); return; }
      estado.aviso = { orden, datos };
      if (!otra.checked) history.pushState(null, '', enlace({ ...estado.ruta, p: 'examinar' }));
      await mostrar();
    } },
    h('div', { class: 'renglones' }, campos.map(f => f.nodo)),
    fallo,
    vista,
    h('div', { class: 'botones-form' },
      h('button', { class: 'boton-tinta', type: 'submit' }, 'insertar'),
      h('label', { class: 'casilla', for: 'insertar-otra' }, otra, ' después, seguir insertando'))),
    h('p', { class: 'pista' }, 'Lo que dejes vacío toma su valor por defecto. «nulo» deja el campo sin valor.'));
}

/* --------------------------------------------------- tabla: estructura */
function vistaEstructura() {
  const m = estado.meta;
  const t = nombre(m.nombre);
  const escribe = puedeEscribir() && m.tipo === 'tabla';
  const definicion = h('div', { class: 'definicion-caja' }, preOrden(m.definicion, 'definicion'),
    h('button', { class: 'enlace', type: 'button', onclick: () => llevarACodigo(m.definicion) }, 'llevar a Código'));
  if (m.tipo === 'vista') {
    return [seccion('Columnas', h('ul', { class: 'lista-simple' }, m.columnas.map(c => h('li', {}, c.nombre)))),
      seccion('Definición', definicion)];
  }
  const indexadas = new Set(m.indices.filter(i => i.columnas.length === 1).map(i => i.columnas[0]));
  const columnas = h('div', { class: 'hoja-datos' }, h('table', { class: 'registro' },
    h('thead', {}, h('tr', {}, ['#', 'columna', 'tipo', 'obligatorio', 'por defecto', 'marcas'].map(x => h('th', {}, x)),
      escribe && h('th', {}, h('span', { class: 'visualmente-oculto' }, 'acciones')))),
    h('tbody', {}, m.columnas.map((c, i) => h('tr', {},
      h('td', { class: 'tenue' }, i + 1),
      h('td', { class: 'fuerte' }, c.nombre),
      h('td', {}, c.tipo ?? h('span', { class: 'nulo' }, 'sin tipo')),
      h('td', {}, c.obligatorio ? 'sí' : h('span', { class: 'tenue' }, 'no')),
      h('td', {}, c.defecto ?? h('span', { class: 'tenue' }, '—')),
      h('td', {}, h('span', { class: 'marcas' },
        c.clave && h('span', { class: 'clave' }, c.auto ? '● clave, se numera sola' : '● clave'),
        c.unico && h('span', {}, 'único'),
        indexadas.has(c.nombre) && !c.clave && h('span', {}, 'indexada'),
        c.ref && h('span', {}, `→ ${c.ref.tabla}(${c.ref.columna})${c.ref.al_borrar === 'cascade' ? ' · al borrar cascada' : c.ref.al_borrar === 'set null' ? ' · al borrar anular' : ''}`))),
      escribe && h('td', { class: 'acciones' },
        accion('renombrar', () => pedir({ titulo: `Renombrar ${c.nombre}`, etiqueta: 'nombre nuevo', valor: c.nombre, boton: 'renombrar',
          construir: v => `renombrar columna ${t}.${nombre(c.nombre)} a ${nombre(v)}` })),
        !indexadas.has(c.nombre) && accion('indexar', () => hacer(`indexar ${t} (${nombre(c.nombre)})`)),
        !c.clave && accion('quitar', () => hacerSiConfirma({ titulo: `Quitar la columna ${c.nombre}`,
          orden: `quitar de ${t} ${nombre(c.nombre)}`, boton: 'quitar', texto: 'Se pierden los valores de esa columna en todas las filas.' }), 'peligro')))))));

  const partes = [seccion('Columnas', columnas, escribe && formularioColumna(m))];
  const propios = m.indices.filter(i => !i.auto);
  partes.push(seccion('Índices',
    propios.length ? h('ul', { class: 'lista-simple' }, propios.map(i => h('li', {},
      h('b', {}, i.nombre), ` (${i.columnas.join(', ')})${i.unico ? ' · único' : ''}`,
      escribe && accion('borrar', () => hacerSiConfirma({ titulo: `Borrar el índice ${i.nombre}`, orden: `borrar indice ${nombre(i.nombre)}`,
        texto: 'Los datos no cambian; solo las búsquedas por esa columna dejan de ir por el atajo.' }), 'peligro'))))
      : h('p', { class: 'mensaje' }, 'Solo el de la clave. Indexa las columnas por las que buscas a menudo.'),
    escribe && formularioIndice(m)));
  if (m.apuntan_aqui.length || m.comprobaciones.length) {
    partes.push(seccion('Relaciones y reglas', h('ul', { class: 'lista-simple' },
      m.apuntan_aqui.map(a => h('li', {}, 'apunta aquí: ', h('a', { href: enlace({ base: estado.ruta.base, tabla: a.tabla, p: 'estructura' }) }, a.tabla), `.${a.columnas.join(', ')}`)),
      m.comprobaciones.map(c => h('li', {}, 'comprobar ', preOrden(c, 'en-linea'))))));
  }
  partes.push(seccion('Definición en SBD', definicion));
  return partes;
}

function opcionesTipo(seleccion = 'texto') {
  return TIPOS.map(t => h('option', { value: t, selected: t === seleccion }, t));
}

function formularioColumna(m) {
  const t = nombre(m.nombre);
  const nombreCol = h('input', { type: 'text', id: 'col-nombre', placeholder: 'telefono', spellcheck: 'false', autocomplete: 'off' });
  const tipo = h('select', { id: 'col-tipo' }, opcionesTipo());
  const largo = h('input', { type: 'text', id: 'col-largo', inputmode: 'numeric', placeholder: '—', size: '4' });
  const obligatorio = h('input', { type: 'checkbox', id: 'col-oblig' });
  const defecto = h('input', { type: 'text', id: 'col-defecto', placeholder: '—', spellcheck: 'false' });
  const tablasRef = estado.info.tablas.filter(x => x.clave.length === 1).map(x => x.nombre);
  const ref = h('select', { id: 'col-ref' }, h('option', { value: '' }, '—'), tablasRef.map(x => h('option', { value: x }, `→ ${x}`)));
  const vista = h('pre', { class: 'orden-viva' });
  const construir = () => {
    const partes = [`añadir a ${t} ${nombre(nombreCol.value.trim() || 'nueva_columna')} ${tipo.value}${tipo.value === 'texto' && /^\d+$/.test(largo.value.trim()) ? `(${largo.value.trim()})` : ''}`];
    if (obligatorio.checked) partes.push('obligatorio');
    const d = valorPorDefecto(defecto.value);
    if (d) partes.push(`= ${d}`);
    if (ref.value) partes.push(`-> ${nombre(ref.value)}`);
    return partes.join(' ');
  };
  const actualizar = () => { largo.disabled = tipo.value !== 'texto'; vista.innerHTML = resaltar(construir()); };
  actualizar();
  return h('form', { class: 'subformulario', oninput: actualizar, onchange: actualizar, onsubmit: ev => {
    ev.preventDefault();
    if (!nombreCol.value.trim()) { nombreCol.focus(); return; }
    hacer(construir());
  } },
  h('h3', {}, 'Añadir una columna'),
  h('div', { class: 'en-linea' },
    h('label', { class: 'campo', for: 'col-nombre' }, h('span', {}, 'nombre'), nombreCol),
    h('label', { class: 'campo', for: 'col-tipo' }, h('span', {}, 'tipo'), tipo),
    h('label', { class: 'campo corto', for: 'col-largo' }, h('span', {}, 'largo'), largo),
    h('label', { class: 'campo', for: 'col-defecto' }, h('span', {}, 'por defecto'), defecto),
    h('label', { class: 'campo', for: 'col-ref' }, h('span', {}, 'apunta a'), ref),
    h('label', { class: 'casilla', for: 'col-oblig' }, obligatorio, ' obligatorio'),
    h('button', { class: 'boton-tinta', type: 'submit' }, 'añadir')),
  vista,
  h('p', { class: 'pista' }, 'Una columna obligatoria en una tabla con filas necesita un valor por defecto.'));
}

function formularioIndice(m) {
  const t = nombre(m.nombre);
  const marcas = m.columnas.map(c => ({ c, input: h('input', { type: 'checkbox', id: `idx-${c.nombre}` }) }));
  const unico = h('input', { type: 'checkbox', id: 'idx-unico' });
  const vista = h('pre', { class: 'orden-viva' });
  const elegidas = () => marcas.filter(x => x.input.checked).map(x => nombre(x.c.nombre));
  const construir = () => `indexar ${unico.checked ? 'unico ' : ''}${t} (${elegidas().join(', ') || '…'})`;
  const actualizar = () => { vista.innerHTML = resaltar(construir()); };
  actualizar();
  return h('form', { class: 'subformulario', onchange: actualizar, onsubmit: ev => {
    ev.preventDefault();
    if (elegidas().length) hacer(construir());
  } },
  h('h3', {}, 'Crear un índice'),
  h('div', { class: 'en-linea' },
    marcas.map(x => h('label', { class: 'casilla', for: x.input.id }, x.input, ` ${x.c.nombre}`)),
    h('label', { class: 'casilla separada', for: 'idx-unico' }, unico, ' único'),
    h('button', { class: 'boton-linea', type: 'submit' }, 'indexar')),
  vista);
}

function valorPorDefecto(texto) {
  const v = texto.trim();
  if (!v) return '';
  const yaEsOrden = /^".*"$|^'.*'$/s.test(v) || /^(nulo|verdadero|falso)$/i.test(v) || v.includes('(')
    || /^-?\d+([.,]\d+)?$/.test(v) || /^\d{4}-\d{2}-\d{2}/.test(v);
  return yaEsOrden ? v.replace(/^(-?\d+),(\d+)$/, '$1.$2') : literal(v);
}

/* ------------------------------------------------------- tabla: buscar */
const OPERADORES = [
  ['', '—'], ['=', '='], ['!=', '≠'], ['>', '>'], ['>=', '≥'], ['<', '<'], ['<=', '≤'],
  ['contiene', 'contiene'], ['empieza por', 'empieza por'], ['termina en', 'termina en'],
  ['es nulo', 'está vacío (nulo)'], ['no es nulo', 'tiene valor'],
];

function vistaBuscar() {
  const m = estado.meta;
  const t = nombre(m.nombre);
  const filas = m.columnas.map(c => {
    const op = h('select', { 'aria-label': `Condición para ${c.nombre}` }, OPERADORES.map(([v, txt]) => h('option', { value: v }, txt)));
    const valor = h('input', { type: 'text', 'aria-label': `Valor para ${c.nombre}`, spellcheck: 'false', autocomplete: 'off',
      placeholder: c.tipo_base === 'DATE' ? 'aaaa-mm-dd' : '' });
    const mostrarla = h('input', { type: 'checkbox', checked: true, 'aria-label': `Mostrar ${c.nombre}` });
    valor.addEventListener('input', () => { if (valor.value && !op.value) op.value = c.tipo_base === 'TEXT' ? 'contiene' : '='; });
    return { c, op, valor, mostrarla };
  });
  const ordenar = h('select', { id: 'buscar-ordenar' }, h('option', { value: '' }, '—'), m.columnas.map(c => h('option', { value: c.nombre }, c.nombre)));
  const desc = h('input', { type: 'checkbox', id: 'buscar-desc' });
  const limite = h('input', { type: 'text', id: 'buscar-limite', value: '100', inputmode: 'numeric', size: '5' });
  const vista = h('pre', { class: 'orden-viva' });
  const resultados = h('div', { class: 'resultados' });

  const valorPara = (c, texto) => {
    if (c.tipo_base === 'BOOLEAN') return /^(verdadero|v|si|sí|1|true)$/i.test(texto.trim()) ? 'verdadero' : 'falso';
    if (NUMERICOS.has(c.tipo_base) && /^-?\d+([.,]\d+)?$/.test(texto.trim())) return texto.trim().replace(',', '.');
    return literal(texto);
  };
  const construir = () => {
    const condiciones = filas.filter(f => f.op.value && (f.valor.value !== '' || f.op.value.includes('nulo'))).map(f => {
      const col = nombre(f.c.nombre);
      const op = f.op.value;
      if (op.includes('nulo')) return `${col} ${op}`;
      if (['contiene', 'empieza por', 'termina en'].includes(op)) {
        return `${f.c.tipo_base === 'TEXT' ? col : `texto(${col})`} ${op} ${literal(f.valor.value)}`;
      }
      return `${col} ${op} ${valorPara(f.c, f.valor.value)}`;
    });
    const todas = filas.every(f => f.mostrarla.checked);
    const columnas = filas.filter(f => f.mostrarla.checked).map(f => nombre(f.c.nombre));
    const partes = [`buscar ${t}`];
    if (condiciones.length) partes.push(`donde ${condiciones.join(' y ')}`);
    if (ordenar.value) partes.push(`ordenar ${nombre(ordenar.value)}${desc.checked ? ' desc' : ''}`);
    if (/^\d+$/.test(limite.value.trim())) partes.push(`limite ${limite.value.trim()}`);
    if (!todas && columnas.length) partes.push(`mostrar ${columnas.join(', ')}`);
    return partes.join(' ');
  };
  const actualizar = () => { vista.innerHTML = resaltar(construir()); };
  actualizar();
  return [seccion('Buscar filas',
    h('form', { class: 'formulario-buscar', oninput: actualizar, onchange: actualizar, onsubmit: async ev => {
      ev.preventDefault();
      const orden = construir();
      resultados.replaceChildren(h('p', { class: 'trabajando' }, 'buscando'));
      const datos = await correr(orden);
      resultados.replaceChildren(...(datos.error
        ? [h('div', { class: 'fallo' }, h('b', {}, 'no se pudo'), datos.error)]
        : [datos.resultados[0].filas.length ? tablaEstatica(datos.resultados[0]) : h('p', { class: 'mensaje' }, 'Ninguna fila cumple esas condiciones.'),
          pieResultado(datos.resultados[0])]));
      resultados.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } },
    h('table', { class: 'tabla-form' },
      h('thead', {}, h('tr', {}, h('th', {}, 'mostrar'), h('th', {}, 'columna'), h('th', {}, 'condición'), h('th', {}, 'valor'))),
      h('tbody', {}, filas.map(f => h('tr', {},
        h('td', { class: 'c' }, f.mostrarla),
        h('td', {}, h('b', {}, f.c.nombre), h('small', {}, f.c.tipo || '')),
        h('td', {}, f.op), h('td', {}, f.valor))))),
    h('div', { class: 'en-linea' },
      h('label', { class: 'campo', for: 'buscar-ordenar' }, h('span', {}, 'ordenar por'), ordenar),
      h('label', { class: 'casilla', for: 'buscar-desc' }, desc, ' de mayor a menor'),
      h('label', { class: 'campo corto', for: 'buscar-limite' }, h('span', {}, 'como mucho'), limite),
      h('button', { class: 'boton-tinta', type: 'submit' }, 'buscar'),
      h('button', { class: 'enlace', type: 'button', onclick: () => llevarACodigo(construir()) }, 'editar en Código')),
    vista)),
  resultados];
}

/* --------------------------------------------------- tabla: operaciones */
function vistaOperacionesTabla() {
  const m = estado.meta;
  const t = nombre(m.nombre);
  const base = estado.ruta.base;
  const descargas = h('div', { class: 'descargas' },
    ['sbd', 'csv', 'json'].map(f => h('a', { class: 'boton-linea', href: `/api/volcar?base=${encodeURIComponent(base)}&tabla=${encodeURIComponent(m.nombre)}&formato=${f}` },
      { sbd: 'script .sbd', csv: 'CSV (Excel)', json: 'JSON' }[f])));
  if (m.tipo === 'vista') {
    return [seccion('Exportar', descargas),
      seccion('Borrar la vista', h('p', { class: 'mensaje' }, 'Solo se borra la consulta guardada.'),
        boton('borrar vista', () => hacerSiConfirma({ titulo: `Borrar la vista ${m.nombre}`, orden: `borrar vista ${t}` }), 'boton-linea peligro'))];
  }
  const formNombre = (id, etiqueta, valor, construir, textoBoton, despues) => {
    const input = h('input', { type: 'text', id, value: valor, spellcheck: 'false', autocomplete: 'off' });
    const vista = h('pre', { class: 'orden-viva' });
    const actualizar = () => { vista.innerHTML = resaltar(construir(input.value.trim() || '…')); };
    actualizar();
    return h('form', { class: 'subformulario', oninput: actualizar, onsubmit: ev => {
      ev.preventDefault();
      const v = input.value.trim();
      if (!v) { input.focus(); return; }
      hacer(construir(v), { despues: () => despues(v) });
    } }, h('div', { class: 'en-linea' }, h('label', { class: 'campo', for: id }, h('span', {}, etiqueta), input),
      h('button', { class: 'boton-linea', type: 'submit' }, textoBoton)), vista);
  };
  return [
    seccion('Renombrar la tabla', formNombre('op-renombrar', 'nombre nuevo', m.nombre, v => `renombrar tabla ${t} a ${nombre(v)}`, 'renombrar',
      v => history.pushState(null, '', enlace({ base, tabla: v, p: 'operaciones' }))),
    h('p', { class: 'pista' }, 'Las vistas y las referencias que la usan se actualizan solas.')),
    seccion('Copiar la tabla', formNombre('op-copiar', 'nombre de la copia', `${m.nombre}_copia`, v => `crear tabla ${nombre(v)} = buscar ${t}`, 'copiar',
      v => history.pushState(null, '', enlace({ base, tabla: v }))),
    h('p', { class: 'pista' }, 'Copia las columnas y las filas. La clave, los índices y las referencias no se copian.')),
    seccion('Exportar', descargas),
    seccion('Vaciar o borrar',
      h('div', { class: 'en-linea' },
        boton('vaciar tabla', () => hacerSiConfirma({ titulo: `Vaciar ${m.nombre}`, orden: `vaciar ${t}`, boton: 'vaciar',
          texto: `Se borran sus ${plural(m.filas, 'fila', 'filas')}; la estructura se queda.` }), 'boton-linea peligro'),
        boton('borrar tabla', () => hacerSiConfirma({ titulo: `Borrar la tabla ${m.nombre}`, orden: `borrar tabla ${t}`,
          texto: 'Se borran la tabla, su estructura y todas sus filas.',
          despues: () => history.pushState(null, '', enlace({ base })) }), 'boton-linea peligro'))),
  ];
}

/* ---------------------------------------------------- base: nueva tabla */
function vistaNuevaTabla() {
  const columnas = [
    { nombre: 'id', tipo: 'entero', largo: '', clave: true, obligatorio: false, unico: false, defecto: '', ref: '' },
    { nombre: 'nombre', tipo: 'texto', largo: '', clave: false, obligatorio: true, unico: false, defecto: '', ref: '' },
  ];
  const nombreTabla = h('input', { type: 'text', id: 'nombre-tabla', placeholder: 'clientes', spellcheck: 'false', autocomplete: 'off', 'data-foco': '' });
  const cuerpo = h('tbody');
  const vista = h('pre', { class: 'orden-viva grande' });
  const fallo = h('div', { class: 'fallo', hidden: true });
  const tablas = estado.info.tablas.filter(t => t.clave.length === 1).map(t => t.nombre);

  const construir = () => {
    const claves = columnas.filter(c => c.clave && c.nombre.trim());
    const ancho = Math.max(...columnas.map(c => nombre(c.nombre.trim() || 'x').length));
    const lineas = columnas.filter(c => c.nombre.trim()).map(c => {
      const tipo = c.tipo === 'texto' && /^\d+$/.test(c.largo.trim()) ? `texto(${c.largo.trim()})` : c.tipo;
      const partes = [nombre(c.nombre.trim()).padEnd(ancho), tipo];
      if (c.clave && claves.length === 1) partes.push('clave');
      if (c.obligatorio && !c.clave) partes.push('obligatorio');
      if (c.unico && !c.clave) partes.push('unico');
      const d = valorPorDefecto(c.defecto);
      if (d) partes.push(`= ${d}`);
      if (c.ref) partes.push(`-> ${nombre(c.ref)}`);
      return '  ' + partes.join(' ');
    });
    if (claves.length > 1) lineas.push(`  clave (${claves.map(c => nombre(c.nombre.trim())).join(', ')})`);
    return `crear tabla ${nombre(nombreTabla.value.trim() || 'nueva_tabla')} {\n${lineas.join('\n')}\n}`;
  };
  const actualizar = () => { vista.innerHTML = resaltar(construir()); };
  const fila = (c, i) => {
    const enlazar = (nodo, prop, tipo = 'value') => {
      nodo.addEventListener(tipo === 'checked' ? 'change' : 'input', () => {
        c[prop] = nodo[tipo];
        if (prop === 'tipo') largo.disabled = c.tipo !== 'texto';
        actualizar();
      });
      return nodo;
    };
    const largo = enlazar(h('input', { type: 'text', value: c.largo, inputmode: 'numeric', placeholder: '—', 'aria-label': 'Largo máximo del texto', disabled: c.tipo !== 'texto' }), 'largo');
    return h('tr', {},
      h('td', {}, enlazar(h('input', { type: 'text', value: c.nombre, 'aria-label': 'Nombre de la columna', spellcheck: 'false', placeholder: 'columna' }), 'nombre')),
      h('td', {}, enlazar(h('select', { 'aria-label': 'Tipo' }, opcionesTipo(c.tipo)), 'tipo')),
      h('td', { class: 'corto' }, largo),
      h('td', { class: 'c' }, enlazar(h('input', { type: 'checkbox', checked: c.clave, 'aria-label': 'clave' }), 'clave', 'checked')),
      h('td', { class: 'c' }, enlazar(h('input', { type: 'checkbox', checked: c.obligatorio, 'aria-label': 'obligatorio' }), 'obligatorio', 'checked')),
      h('td', { class: 'c' }, enlazar(h('input', { type: 'checkbox', checked: c.unico, 'aria-label': 'único' }), 'unico', 'checked')),
      h('td', {}, enlazar(h('input', { type: 'text', value: c.defecto, placeholder: '—', 'aria-label': 'Valor por defecto', spellcheck: 'false' }), 'defecto')),
      h('td', {}, enlazar(h('select', { 'aria-label': 'Apunta a la tabla' },
        h('option', { value: '' }, '—'), tablas.map(t => h('option', { value: t, selected: t === c.ref }, `→ ${t}`))), 'ref')),
      h('td', {}, h('button', { type: 'button', class: 'accion peligro', 'aria-label': `Quitar la columna ${c.nombre || i + 1}`,
        disabled: columnas.length === 1, onclick: () => { columnas.splice(i, 1); pintarFilas(); actualizar(); } }, 'quitar')));
  };
  const pintarFilas = () => cuerpo.replaceChildren(...columnas.map(fila));
  pintarFilas();
  nombreTabla.addEventListener('input', actualizar);
  actualizar();
  const crear = h('button', { class: 'boton-tinta', type: 'submit' }, 'crear tabla');
  return seccion('Diseñar una tabla',
    h('form', { class: 'diseno-form', onsubmit: async ev => {
      ev.preventDefault();
      const n = nombreTabla.value.trim();
      if (!n) { nombreTabla.focus(); return; }
      crear.disabled = true;
      const orden = construir();
      const datos = await correr(orden);
      crear.disabled = false;
      if (datos.error) { fallo.hidden = false; fallo.replaceChildren(h('b', {}, 'no se pudo'), datos.error); return; }
      estado.aviso = { orden, datos };
      history.pushState(null, '', enlace({ base: estado.ruta.base, tabla: n, p: 'estructura' }));
      await mostrar();
    } },
    h('label', { class: 'campo-grande', for: 'nombre-tabla' }, h('span', {}, 'nombre de la tabla'), nombreTabla),
    h('div', { class: 'hoja-datos' }, h('table', { class: 'diseno' },
      h('thead', {}, h('tr', {}, ['columna', 'tipo', 'largo', 'clave', 'oblig.', 'único', 'por defecto', 'apunta a', ''].map(x => h('th', {}, x)))),
      cuerpo)),
    h('p', {}, h('button', { type: 'button', class: 'boton-linea', onclick: () => {
      columnas.push({ nombre: '', tipo: 'texto', largo: '', clave: false, obligatorio: false, unico: false, defecto: '', ref: '' });
      pintarFilas(); actualizar(); cuerpo.lastChild.querySelector('input').focus();
    } }, '+ otra columna')),
    fallo,
    h('div', { class: 'definicion-caja' }, vista,
      h('button', { class: 'enlace', type: 'button', onclick: () => llevarACodigo(construir()) }, 'seguir en Código')),
    h('div', { class: 'botones-form' }, crear)),
  h('p', { class: 'pista' }, 'Una columna «entero clave» se numera sola: 1, 2, 3… Por defecto admite valores como 0, "Madrid", hoy() o verdadero.'));
}

/* ------------------------------------------------------- base: importar */
function nombreDeTabla(fichero) {
  const base = fichero.replace(/\.[^.]+$/, '').normalize('NFD').replace(/[̀-ͯ]/g, '').normalize('NFC');
  const limpio = base.toLowerCase().replace(/[^a-z0-9ñ_]+/g, '_').replace(/^_+|_+$/g, '');
  return /^[a-zñ_]/.test(limpio) ? limpio : `t_${limpio || 'importada'}`;
}

function leerComoBase64(fichero) {
  return new Promise((resolver, fallar) => {
    const lector = new FileReader();
    lector.onload = () => resolver(String(lector.result).split(',', 2)[1] || '');
    lector.onerror = () => fallar(new Error('no se ha podido leer el fichero'));
    lector.readAsDataURL(fichero);
  });
}

function vistaImportar() {
  const tablas = estado.info.tablas.map(t => t.nombre);
  let fichero = null;
  const elegido = h('p', { class: 'mensaje' }, 'Ningún fichero elegido.');
  const tabla = h('input', { type: 'text', id: 'importar-tabla', list: 'tablas-importar', spellcheck: 'false', autocomplete: 'off', value: estado.ruta.tabla || '' });
  const vista = h('pre', { class: 'orden-viva' });
  const explicacion = h('p', { class: 'pista' });
  const fallo = h('div', { class: 'fallo', hidden: true });
  const boton = h('button', { class: 'boton-tinta', type: 'submit', disabled: true }, 'importar');
  const actualizar = () => {
    const destino = tabla.value.trim();
    vista.innerHTML = resaltar(`importar ${literal(fichero?.name || 'fichero.csv')} en ${nombre(destino || 'tabla')}`);
    explicacion.textContent = tablas.includes(destino)
      ? `«${destino}» ya existe: las filas se añaden a las que tiene, emparejando las columnas por su nombre.`
      : `«${destino || 'la tabla'}» no existe: se creará con las columnas del fichero, adivinando el tipo de cada una.`;
    boton.disabled = !fichero || !destino;
  };
  const input = $('#fichero-importar');
  input.value = '';
  input.onchange = () => {
    fichero = input.files[0] || null;
    if (!fichero) return;
    if (fichero.size > 45 * 1024 * 1024) { fallo.hidden = false; fallo.replaceChildren(h('b', {}, 'no se pudo'), 'el fichero es demasiado grande (máximo 45 MB)'); fichero = null; return; }
    elegido.replaceChildren(h('b', {}, fichero.name), ` · ${bytes(fichero.size)}`);
    if (!tabla.value.trim()) tabla.value = nombreDeTabla(fichero.name);
    actualizar();
  };
  tabla.addEventListener('input', actualizar);
  actualizar();
  return seccion('Importar un CSV o un Excel',
    h('form', { class: 'subformulario', onsubmit: async ev => {
      ev.preventDefault();
      const destino = tabla.value.trim();
      if (!fichero || !destino) return;
      boton.disabled = true;
      boton.textContent = 'importando…';
      let datos;
      try {
        datos = await api('/api/importar', { base: estado.ruta.base, tabla: destino, fichero: fichero.name, contenido: await leerComoBase64(fichero) });
      } catch (err) {
        datos = { resultados: [], error: err.message };
      }
      boton.textContent = 'importar';
      if (datos.error) { boton.disabled = false; fallo.hidden = false; fallo.replaceChildren(h('b', {}, 'no se pudo'), datos.error); return; }
      estado.aviso = { orden: `importar ${literal(fichero.name)} en ${nombre(destino)}`, datos };
      history.pushState(null, '', enlace({ base: estado.ruta.base, tabla: destino }));
      await mostrar();
    } },
    h('div', { class: 'en-linea' }, h('button', { class: 'boton-linea', type: 'button', onclick: () => input.click(), 'data-foco': '' }, 'elegir fichero…'), elegido),
    h('label', { class: 'campo-grande', for: 'importar-tabla' }, h('span', {}, 'en la tabla'), tabla,
      h('datalist', { id: 'tablas-importar' }, tablas.map(t => h('option', { value: t })))),
    explicacion, fallo, vista, h('div', { class: 'botones-form' }, boton)),
  h('p', { class: 'pista' }, 'Entiende el separador «;», decimales con coma (1.234,50), fechas dd/mm/aaaa y los CSV que guarda Excel. Es todo o nada: si una fila no cabe, no entra ninguna.'));
}

/* ------------------------------------------------------- base: exportar */
function vistaExportar() {
  const base = estado.ruta.base;
  const info = estado.info;
  const url = (tabla, f) => `/api/volcar?base=${encodeURIComponent(base)}${tabla ? `&tabla=${encodeURIComponent(tabla)}` : ''}&formato=${f}`;
  return [
    seccion('La base entera',
      h('p', { class: 'mensaje' }, 'Un script .sbd con la estructura y todos los datos. Para recrearla en otro sitio: ', h('code', {}, `sbd -f ${base}.sbd`)),
      h('div', { class: 'descargas' }, h('a', { class: 'boton-tinta', href: url(null, 'sbd') }, `descargar ${base}.sbd`))),
    seccion('Tabla a tabla', h('div', { class: 'hoja-datos' }, h('table', { class: 'registro' },
      h('thead', {}, h('tr', {}, h('th', {}, 'tabla'), h('th', { class: 'num' }, 'filas'), h('th', {}, 'descargar'))),
      h('tbody', {}, [...info.tablas.map(t => [t.nombre, t.filas]), ...info.vistas.map(v => [v, null])].map(([n, filas]) => h('tr', {},
        h('td', { class: 'fuerte' }, n), h('td', { class: 'num' }, filas === null ? 'vista' : cifra(filas)),
        h('td', { class: 'acciones' }, ['sbd', 'csv', 'json'].map(f => h('a', { class: 'accion', href: url(n, f) }, f))))))))),
  ];
}

/* ---------------------------------------------------- base: operaciones */
async function vistaOperacionesBase() {
  const base = estado.ruta.base;
  const b = nombre(base);
  const partes = [];
  if (puedeCrearBases()) {
    const copia = h('input', { type: 'text', id: 'op-copia-base', value: `${base}_copia`, spellcheck: 'false', autocomplete: 'off' });
    partes.push(seccion('Copiar la base',
      h('form', { class: 'en-linea', onsubmit: ev => {
        ev.preventDefault();
        const n = copia.value.trim();
        if (n) hacer(`copiar base ${b} a ${nombre(n)}`, { despues: () => history.pushState(null, '', enlace({ base: n })) });
      } }, h('label', { class: 'campo', for: 'op-copia-base' }, h('span', {}, 'nombre de la copia'), copia),
      h('button', { class: 'boton-linea', type: 'submit' }, 'copiar')),
      h('p', { class: 'pista' }, 'Una copia exacta, con todo lo confirmado: para probar cambios sin miedo.')));
  }
  const historial = await correr(`historial de ${b} limite 50`, null);
  partes.push(seccion('Últimos cambios',
    historial.error ? h('p', { class: 'mensaje' }, historial.error)
      : historial.resultados[0].filas.length ? tablaEstatica(historial.resultados[0]) : h('p', { class: 'mensaje' }, 'Todavía no se ha cambiado nada.')));
  if (puedeCrearBases()) {
    partes.push(seccion('Borrar la base', h('p', { class: 'mensaje' }, 'Va a la papelera y se puede recuperar desde «Copias e historial».'),
      boton(`borrar ${base}`, () => hacerSiConfirma({ titulo: `Borrar la base ${base}`, orden: `borrar base ${b}`, base: null,
        despues: () => history.pushState(null, '', enlace()) }), 'boton-linea peligro')));
  }
  return partes;
}

/* ------------------------------------------------------ servidor: usuarios */
async function vistaUsuarios() {
  const datos = await correr('usuarios\nclaves api', null);
  if (datos.error) return vacio('No se pueden ver los usuarios', datos.error);
  const [usuarios, claves] = datos.resultados;
  const bases = estado.bases.map(b => b.nombre);

  const nombreU = h('input', { type: 'text', id: 'u-nombre', placeholder: 'ana', spellcheck: 'false', autocomplete: 'off',
    'data-foco': usuarios.filas.length ? null : '' });
  const claveU = h('input', { type: 'password', id: 'u-clave', autocomplete: 'new-password', placeholder: '8 caracteres o más' });
  const permiso = h('select', { id: 'u-permiso' }, ['leer', 'escribir', 'todo'].map(p => h('option', { value: p, selected: p === 'escribir' }, p)));
  const marcas = bases.map(b => ({ b, input: h('input', { type: 'checkbox', id: `u-base-${b}` }) }));
  const vista = h('pre', { class: 'orden-viva' });
  const construir = (clave = '••••••••') => {
    const en = marcas.filter(x => x.input.checked).map(x => nombre(x.b));
    return `crear usuario ${nombre(nombreU.value.trim() || 'nombre')} clave ${literal(clave)} puede ${permiso.value}${en.length ? ` en ${en.join(', ')}` : ''}`;
  };
  const actualizar = () => { vista.innerHTML = resaltar(construir()); };
  actualizar();

  const usuarioU = h('select', { id: 'k-usuario' }, usuarios.filas.map(([u]) => h('option', { value: u }, u)));
  const registro = await seccionRegistro();
  const nombreK = h('input', { type: 'text', id: 'k-nombre', placeholder: 'web de la tienda', autocomplete: 'off' });

  return [
    registro,
    estado.sinUsuarios && h('div', { class: 'aviso nota' }, h('div', { class: 'aviso-cab' }, h('b', {}, 'ojo'),
      h('span', { class: 'aviso-texto' }, 'En cuanto exista el primer usuario, el panel pedirá entrar. Crea uno con permiso «todo» para ti.'))),
    seccion('Usuarios', usuarios.filas.length ? h('div', { class: 'hoja-datos' }, h('table', { class: 'registro' },
      h('thead', {}, h('tr', {}, h('th', {}, 'usuario'), h('th', {}, 'permiso'), h('th', {}, 'bases'), h('th', {}, h('span', { class: 'visualmente-oculto' }, 'acciones')))),
      h('tbody', {}, usuarios.filas.map(([u, p, bs]) => h('tr', {},
        h('td', { class: 'fuerte' }, u), h('td', {}, p === 'cuenta' ? 'cuenta propia' : p), h('td', { class: 'tenue' }, bs || (p === 'cuenta' ? 'ninguna aún' : 'todas')),
        h('td', { class: 'acciones' },
          accion('permiso', () => pedir({ titulo: `Permiso de ${u}`, etiqueta: 'leer, escribir o todo (y, si quieres, «en base1, base2»)',
            valor: `${p}${bs && bs !== '*' && bs !== 'todas' ? ` en ${bs}` : ''}`, boton: 'cambiar',
            construir: v => `cambiar permiso de ${nombre(u)} a ${v}` })),
          accion('clave', () => pedir({ titulo: `Clave de ${u}`, etiqueta: 'clave nueva (8 caracteres o más)', tipo: 'password', boton: 'cambiar',
            construir: v => `cambiar clave de ${nombre(u)} a ${literal(v)}` })),
          accion('borrar', () => hacerSiConfirma({ titulo: `Borrar el usuario ${u}`, orden: `borrar usuario ${nombre(u)}`, base: null,
            texto: 'Sus claves de API dejan de valer en el acto.' }), 'peligro'))))))) : h('p', { class: 'mensaje' }, 'Todavía no hay usuarios.'),
    h('form', { class: 'subformulario', oninput: actualizar, onchange: actualizar, onsubmit: ev => {
      ev.preventDefault();
      if (!nombreU.value.trim()) { nombreU.focus(); return; }
      if (claveU.value.length < 8) { claveU.focus(); claveU.setCustomValidity('La clave necesita 8 caracteres o más'); claveU.reportValidity(); return; }
      hacer(construir(claveU.value), { base: null });
    } },
    h('h3', {}, 'Crear un usuario'),
    h('div', { class: 'en-linea' },
      h('label', { class: 'campo', for: 'u-nombre' }, h('span', {}, 'nombre'), nombreU),
      h('label', { class: 'campo', for: 'u-clave' }, h('span', {}, 'clave'), claveU),
      h('label', { class: 'campo', for: 'u-permiso' }, h('span', {}, 'puede'), permiso)),
    bases.length > 0 && h('div', { class: 'en-linea' }, h('span', { class: 'tenue' }, 'solo en:'),
      marcas.map(x => h('label', { class: 'casilla', for: x.input.id }, x.input, ` ${x.b}`)),
      h('span', { class: 'tenue' }, '(sin marcar ninguna: todas)')),
    vista,
    h('div', { class: 'botones-form' }, h('button', { class: 'boton-tinta', type: 'submit' }, 'crear usuario')))),
    seccion('Claves de API',
      h('p', { class: 'mensaje' }, 'Con ellas se conectan tus aplicaciones. Cada clave actúa con los permisos de su usuario y se enseña una sola vez.'),
      claves.filas.length > 0 && tablaClaves(claves),
      usuarios.filas.length ? h('form', { class: 'subformulario', onsubmit: ev => {
        ev.preventDefault();
        hacer(`crear clave api para ${nombre(usuarioU.value)}${nombreK.value.trim() ? ` llamada ${literal(nombreK.value.trim())}` : ''}`, { base: null });
      } }, h('h3', {}, 'Crear una clave'), h('div', { class: 'en-linea' },
        h('label', { class: 'campo', for: 'k-usuario' }, h('span', {}, 'para'), usuarioU),
        h('label', { class: 'campo', for: 'k-nombre' }, h('span', {}, 'llamada'), nombreK),
        h('button', { class: 'boton-linea', type: 'submit' }, 'crear clave')))
        : h('p', { class: 'pista' }, 'Primero crea un usuario para la aplicación.')),
  ];
}

/* Para quien administra: abrir o cerrar el registro y los límites de cada cuenta. */
async function seccionRegistro() {
  let ajustes;
  try { ajustes = await api('/api/ajustes'); } catch { return null; }
  const abierto = h('input', { type: 'checkbox', id: 'aj-registro', checked: ajustes.registro });
  const numero = (id, valor) => h('input', { type: 'text', id, value: String(valor), inputmode: 'numeric', size: '8' });
  const bases = numero('aj-bases', ajustes.max_bases);
  const mb = numero('aj-mb', ajustes.max_mb);
  const filas = numero('aj-filas', ajustes.max_filas);
  const memoria = numero('aj-memoria', ajustes.max_memoria_mb);
  const fallo = h('div', { class: 'fallo', hidden: true });
  return seccion('Registro público',
    h('p', { class: 'mensaje' }, ajustes.registro
      ? `Abierto: cualquiera puede crearse una cuenta en ${location.host}. Hay ${plural(ajustes.cuentas, 'cuenta', 'cuentas')}.`
      : `Cerrado: solo entra quien tenga un usuario creado aquí. Hay ${plural(ajustes.cuentas, 'cuenta', 'cuentas')}.`,
    ` El servidor usa ahora ${cifra(ajustes.memoria_mb)} MB de memoria.`),
    h('form', { class: 'subformulario', onsubmit: async ev => {
      ev.preventDefault();
      const entero = i => Number.parseInt(i.value.replace(/\D/g, ''), 10);
      try {
        await api('/api/ajustes', { registro: abierto.checked, max_bases: entero(bases), max_mb: entero(mb),
          max_filas: entero(filas), max_memoria_mb: entero(memoria) });
        estado.aviso = { orden: '-- ajustes del registro guardados', datos: { resultados: [{ mensaje: abierto.checked ? 'Registro abierto' : 'Registro cerrado' }] } };
        await mostrar();
      } catch (err) { fallo.hidden = false; fallo.replaceChildren(h('b', {}, 'no se pudo'), err.message); }
    } },
    h('h3', {}, 'Cuentas nuevas'),
    h('label', { class: 'casilla', for: 'aj-registro' }, abierto, ' cualquiera puede crearse una cuenta desde la pantalla de entrada'),
    h('div', { class: 'en-linea' },
      h('label', { class: 'campo corto', for: 'aj-bases' }, h('span', {}, 'bases por cuenta'), bases),
      h('label', { class: 'campo corto', for: 'aj-mb' }, h('span', {}, 'MB por cuenta'), mb),
      h('label', { class: 'campo', for: 'aj-filas' }, h('span', {}, 'filas por cuenta'), filas),
      h('label', { class: 'campo', for: 'aj-memoria' }, h('span', {}, 'memoria máx. del servidor (MB)'), memoria),
      h('button', { class: 'boton-linea', type: 'submit' }, 'guardar')),
    fallo,
    h('p', { class: 'pista' }, 'Cada cuenta solo ve sus bases. Las filas son lo que ocupa memoria: unos 1,8 KB cada una. Si el servidor llega a la memoria máxima, las cuentas no pueden añadir datos hasta que baje; tú sí.')));
}

/* ------------------------------------------------------- servidor: mi cuenta */
async function vistaCuenta() {
  const [uso, datos] = await Promise.all([api('/api/cuenta'), correr('claves api', null)]);
  const yo = estado.usuario.nombre;
  const barra = (texto, valor, maximo, formato = cifra) => {
    const parte = Math.min(1, maximo ? valor / maximo : 0);
    return h('div', { class: `consumo${parte >= 0.9 ? ' casi' : ''}` },
      h('div', { class: 'consumo-cab' }, h('span', {}, texto), h('span', { class: 'tenue' }, `${formato(valor)} de ${formato(maximo)}`)),
      h('div', { class: 'consumo-raya', role: 'meter', 'aria-label': texto, 'aria-valuemin': '0', 'aria-valuemax': String(maximo), 'aria-valuenow': String(valor) },
        h('span', { style: `width: ${(parte * 100).toFixed(1)}%` })));
  };
  const mb = b => `${(b / 1024 / 1024).toFixed(1).replace('.', ',')} MB`;
  const nombreK = h('input', { type: 'text', id: 'k-nombre', placeholder: 'mi web', autocomplete: 'off' });
  const claves = datos.resultados?.[0];
  const ejemplo = `import sbd\n\ndb = sbd.conectar_servidor('${location.origin}', clave='sbd_…', base='${estado.bases[0]?.nombre || 'mi_base'}')\nfor fila in db.consultar('buscar tabla limite 10'):\n    print(fila)`;
  return [
    seccion(`La cuenta de ${yo}`,
      h('div', { class: 'consumos' },
        barra('bases', uso.bases, uso.max_bases),
        barra('filas', uso.filas, uso.max_filas),
        barra('espacio', uso.bytes, uso.max_mb * 1024 * 1024, mb)),
      h('p', { class: 'pista' }, 'Si llegas a un límite, puedes seguir consultando y borrando; para añadir más, haz sitio.'),
      h('div', { class: 'en-linea' }, boton('cambiar mi clave', () => pedir({ titulo: 'Tu clave nueva', etiqueta: '8 caracteres o más', tipo: 'password',
        boton: 'cambiar', construir: v => `cambiar clave de ${nombre(yo)} a ${literal(v)}` })))),
    seccion('Conectar tus aplicaciones',
      h('p', { class: 'mensaje' }, 'Crea una clave de API y úsala desde tu programa (Python, JavaScript o PHP). Actúa como tú: llega a tus bases y a ninguna más. Se enseña una sola vez.'),
      claves?.filas.length > 0 && tablaClaves(claves),
      h('form', { class: 'subformulario', onsubmit: ev => {
        ev.preventDefault();
        hacer(`crear clave api${nombreK.value.trim() ? ` llamada ${literal(nombreK.value.trim())}` : ''}`, { base: null });
      } }, h('h3', {}, 'Crear una clave'), h('div', { class: 'en-linea' },
        h('label', { class: 'campo', for: 'k-nombre' }, h('span', {}, 'para qué es'), nombreK),
        h('button', { class: 'boton-linea', type: 'submit' }, 'crear clave'))),
      h('div', { class: 'definicion-caja' }, h('pre', { class: 'definicion' }, ejemplo))),
  ];
}

function tablaClaves(claves) {
  return h('div', { class: 'hoja-datos' }, h('table', { class: 'registro' },
    h('thead', {}, h('tr', {}, claves.columnas.map(c => h('th', {}, c)), h('th', {}, h('span', { class: 'visualmente-oculto' }, 'acciones')))),
    h('tbody', {}, claves.filas.map(f => h('tr', {}, f.map(v => h('td', {}, valorCelda(v))),
      h('td', { class: 'acciones' }, accion('borrar', () => hacerSiConfirma({ titulo: `Borrar la clave ${f[0]}`, orden: `borrar clave api ${f[0]}`, base: null,
        texto: 'La aplicación que la use deja de poder conectarse en el acto.' }), 'peligro')))))));
}

/* ----------------------------------------------- servidor: copias e historial */
async function vistaCopias() {
  const datos = await correr('copias\npapelera\nhistorial limite 100', null);
  if (datos.error) return vacio('No se pueden ver las copias', datos.error);
  const [copias, papelera, historial] = datos.resultados;
  const sinGuiones = d => d.replaceAll('-', '');
  return [
    seccion('Copias diarias',
      h('p', { class: 'mensaje' }, 'El servidor copia cada base una vez al día y guarda las de los últimos días.'),
      copias.filas.length ? h('div', { class: 'hoja-datos' }, h('table', { class: 'registro' },
        h('thead', {}, h('tr', {}, h('th', {}, 'día'), h('th', {}, 'base'), h('th', { class: 'num' }, 'tamaño'), h('th', {}, h('span', { class: 'visualmente-oculto' }, 'acciones')))),
        h('tbody', {}, copias.filas.map(([dia, base, tam]) => h('tr', {},
          h('td', {}, dia), h('td', { class: 'fuerte' }, base), h('td', { class: 'num' }, bytes(tam)),
          h('td', { class: 'acciones' }, accion('restaurar como…', () => pedir({ titulo: `Restaurar ${base} del ${dia}`,
            etiqueta: 'como una base nueva llamada', valor: `${base}_${sinGuiones(dia)}`, boton: 'restaurar',
            construir: v => `restaurar base ${nombre(base)} del ${dia} como ${nombre(v)}`,
            despues: v => history.pushState(null, '', enlace({ base: v })) })))))))) : h('p', { class: 'mensaje' }, 'Aún no hay copias: se hace la primera al arrancar el servidor.')),
    seccion('Papelera',
      papelera.filas.length ? h('div', { class: 'hoja-datos' }, h('table', { class: 'registro' },
        h('thead', {}, h('tr', {}, h('th', {}, 'base'), h('th', {}, 'borrada'), h('th', { class: 'num' }, 'tamaño'), h('th', {}, h('span', { class: 'visualmente-oculto' }, 'acciones')))),
        h('tbody', {}, papelera.filas.map(([base, cuando, tam]) => h('tr', {},
          h('td', { class: 'fuerte' }, base), h('td', {}, cuando), h('td', { class: 'num' }, bytes(tam)),
          h('td', { class: 'acciones' },
            !estado.bases.some(b => b.nombre === base) && accion('recuperar', () => hacer(`recuperar base ${nombre(base)}`, { base: null })),
            accion('recuperar como…', () => pedir({ titulo: `Recuperar ${base}`, etiqueta: 'con el nombre', valor: `${base}_recuperada`, boton: 'recuperar',
              construir: v => `recuperar base ${nombre(base)} como ${nombre(v)}` })))))))) : h('p', { class: 'mensaje' }, 'La papelera está vacía.')),
    seccion('Historial', h('p', { class: 'mensaje' }, 'Las últimas órdenes que cambiaron algo: quién, cuándo y qué. Las claves nunca aparecen.'),
      historial.filas.length ? tablaEstatica(historial) : h('p', { class: 'mensaje' }, 'Todavía no se ha cambiado nada.')),
  ];
}

/* ================================================================ CÓDIGO */
const PLANTILLA_PROCEDIMIENTO = `crear procedimiento transferir(origen, destino, cantidad) {
  actualizar cuentas poner saldo = saldo - :cantidad donde id = :origen
  actualizar cuentas poner saldo = saldo + :cantidad donde id = :destino
}`;

const AYUDA = [
  ['Tablas', 'Tipos: entero, decimal, dinero, texto, texto(n), logico, fecha y fechahora. Marcas: clave, obligatorio, unico, = por defecto, comprobar y -> otra_tabla.',
    ['crear tabla clientes {\n  id      entero clave\n  nombre  texto(60) obligatorio\n  email   texto unico\n  saldo   dinero = 0\n  alta    fecha = hoy()\n}',
      'añadir a clientes telefono texto', 'quitar de clientes telefono', 'renombrar columna clientes.alta a desde', 'indexar clientes (email)']],
  ['Datos', 'Lo que no pongas toma su valor por defecto. Para cambiar o borrar hay que decir qué filas con «donde».',
    ['insertar en clientes { nombre: "Ana", email: "ana@ejemplo.es" }', 'insertar en clientes (nombre) ("Luis") ("Sara")',
      'actualizar clientes poner email = nulo donde id = 2', 'borrar de clientes donde id = 2', 'vaciar clientes']],
  ['Buscar', 'Las cláusulas van en cualquier orden, en una línea o entre llaves.',
    ['buscar clientes donde nombre contiene "an" ordenar nombre', 'buscar clientes mostrar nombre, email limite 10',
      'buscar pedidos como p {\n  unir clientes como c en c.id = p.cliente_id\n  agrupar c.nombre\n  mostrar c.nombre, contar() como pedidos\n  ordenar pedidos desc\n}']],
  ['Condiciones', 'y · o · no · es nulo · en [lista] · entre a y b · contiene · empieza por · termina en · parece "patrón%".',
    ['buscar clientes donde alta entre 2024-01-01 y 2024-06-30', 'buscar clientes donde email es nulo o nombre empieza por "S"']],
  ['Resumir', 'contar, sumar, media, minimo, maximo y juntar. calcular evalúa sin tabla.',
    ['calcular hoy(), 2 + 2, mayusculas("sbd")', 'buscar clientes agrupar ciudad mostrar ciudad, contar() como n teniendo contar() > 1']],
  ['Fechas', 'Los intervalos se escriben como se dicen: 3 dias, 2 semanas, 1 mes, 90 minutos.',
    ['buscar pedidos donde fecha > hace 30 dias', 'calcular hoy() + 2 semanas, dentro de 1 mes', 'calcular edad(1990-05-01), dia_semana(hoy())']],
  ['Más', 'insertar o actualizar cambia la fila si ya existe; devolver enseña lo creado; sobre mira las filas vecinas.',
    ['insertar o actualizar en clientes { email: "ana@ejemplo.es", nombre: "Ana" } devolver id',
      'buscar pedidos mostrar id, total,\n  puesto() sobre (por cliente_id ordenar total desc) como puesto',
      'explicar buscar pedidos donde fecha > hace 30 dias', 'crear vista madrileños = buscar clientes donde ciudad = "Madrid"']],
  ['Procedimientos', 'Órdenes guardadas en la base que se ejecutan juntas: si una falla, no se aplica ninguna. Los parámetros se escriben :nombre.',
    [PLANTILLA_PROCEDIMIENTO, 'ejecutar transferir(1, 2, 30)', 'procedimientos', 'borrar procedimiento transferir']],
  ['Cambios seguros', 'Dentro de una transacción nada se guarda hasta «confirmar».',
    ['empezar\nactualizar productos poner precio = precio * 1.1 todas\nconfirmar', 'deshacer']],
];

/* Plantillas según dónde estés: con una tabla abierta, ya llevan su nombre y sus columnas. */
function plantillas() {
  const m = estado.meta;
  if (!m || m.tipo !== 'tabla') {
    return [['crear tabla', 'crear tabla nueva {\n  id      entero clave\n  nombre  texto obligatorio\n}'],
      ['buscar', 'buscar tabla donde columna = valor'], ['procedimiento', PLANTILLA_PROCEDIMIENTO], ['tablas', 'tablas'], ['bases', 'bases']];
  }
  const t = nombre(m.nombre);
  const cols = m.columnas.filter(c => !c.auto);
  const ejemplo = c => ({ INTEGER: '0', REAL: '0.0', MONEY: '0.00', BOOLEAN: 'verdadero', DATE: 'hoy()', TIMESTAMP: 'ahora()' }[c.tipo_base] || '""');
  const clave = m.clave[0] ? nombre(m.clave[0]) : nombre(m.columnas[0].nombre);
  const lista = [
    ['buscar', `buscar ${t}\n  donde ${nombre((cols[0] || m.columnas[0]).nombre)} = ${ejemplo(cols[0] || m.columnas[0])}\n  ordenar ${clave}\n  limite 50`],
    ['insertar', `insertar en ${t} {\n${cols.map(c => `  ${nombre(c.nombre)}: ${ejemplo(c)}`).join(',\n')}\n}`],
    ['actualizar', `actualizar ${t}\n  poner ${nombre((cols[0] || m.columnas[0]).nombre)} = ${ejemplo(cols[0] || m.columnas[0])}\n  donde ${clave} = 1`],
    ['borrar', `borrar de ${t} donde ${clave} = 1`],
    ['contar', `buscar ${t} mostrar contar()`],
  ];
  const ref = m.columnas.find(c => c.ref);
  if (ref) lista.push(['unir', `buscar ${t} como a\n  unir ${nombre(ref.ref.tabla)} como b en b.${nombre(ref.ref.columna)} = a.${nombre(ref.nombre)}\n  limite 50`]);
  lista.push(['añadir columna', `añadir a ${t} nueva_columna texto`]);
  return lista;
}

function textoInicial() {
  const r = estado.ruta;
  const guardado = leer(claveBorrador(), null);
  if (guardado !== null) return guardado;
  if (r.tabla) return `buscar ${nombre(r.tabla)} limite 50`;
  if (r.base) return estado.info?.tablas.length ? `buscar ${nombre(estado.info.tablas[0].nombre)} limite 50` : 'crear tabla notas {\n  id     entero clave\n  texto  texto obligatorio\n  fecha  fecha = hoy()\n}';
  return 'bases';
}

function vistaCodigo() {
  const ed = editorCodigo(textoInicial());
  const zona = h('div', { class: 'resultados' });
  const ejecutarBoton = h('button', { class: 'boton-tinta', type: 'button', onclick: () => ejecutarCodigo() }, 'ejecutar');
  const anteriores = h('select', { class: 'anteriores', 'aria-label': 'Órdenes anteriores', onchange: ev => {
    if (ev.target.value !== '') ed.poner(estado.historial[Number(ev.target.value)]);
    ev.target.value = '';
  } }, h('option', { value: '' }, 'anteriores…'), estado.historial.slice(0, 30).map((o, i) => h('option', { value: String(i) }, o.replace(/\s+/g, ' ').slice(0, 70))));

  async function ejecutarCodigo() {
    const ta = ed.ta;
    const seleccion = ta.value.slice(ta.selectionStart, ta.selectionEnd).trim();
    const texto = seleccion || ta.value.trim();
    if (!texto) return;
    if (!llevaClave(texto)) {
      estado.historial = [texto, ...estado.historial.filter(x => x !== texto)].slice(0, 60);
      guardar('sbd.historial', estado.historial);
    }
    ejecutarBoton.disabled = true;
    zona.replaceChildren(h('p', { class: 'trabajando' }, 'ejecutando'));
    const datos = await correr(texto);
    ejecutarBoton.disabled = false;
    estado.codigo = { datos, seleccion: Boolean(seleccion) };
    const r = estado.ruta;
    const cambia = datos.resultados.some(x => x.cambia) || (datos.base !== undefined && datos.base !== r.base);
    if (datos.base !== undefined && datos.base !== r.base) {
      // «usar» o «crear base»: se sigue en el código de la base nueva, con el mismo texto y sus resultados.
      guardar(claveBorrador({ base: datos.base, tabla: null }), ta.value);
      history.pushState(null, '', enlace({ base: datos.base, p: 'codigo' }));
      await mostrar();
      return;
    }
    if (cambia) {
      try { await cargarTodo(); } catch { /* si hace falta entrar, ya se está pidiendo */ }
      if (estado.ruta.tabla !== r.tabla) { history.replaceState(null, '', enlace({ ...estado.ruta, p: 'codigo' })); }
      pintarIndice();
      pintarCabecera();
      pintarPestanas();
    }
    zona.replaceChildren(...pintarResultados(datos));
  }
  ed.alEjecutar = ejecutarCodigo;
  if (estado.codigo?.datos) zona.replaceChildren(...pintarResultados(estado.codigo.datos));

  const chuleta = h('aside', { class: 'chuleta', 'aria-label': 'Chuleta del lenguaje' },
    h('h2', {}, 'Chuleta'),
    h('p', { class: 'pista' }, 'Pulsa un ejemplo para llevarlo al editor.'),
    AYUDA.map(([titulo, texto, ejemplos], i) => h('details', { open: i < 3 },
      h('summary', {}, titulo), h('p', {}, texto),
      ejemplos.map(ej => h('button', { class: 'ejemplo', type: 'button', onclick: () => ed.insertar(ej) }, preOrden(ej, 'mini'))))));

  return h('div', { class: 'mesa-codigo' },
    h('div', { class: 'columna-codigo' },
      h('div', { class: 'barra' },
        h('span', { class: 'tenue' }, 'plantillas:'),
        plantillas().map(([etiqueta, texto]) => h('button', { class: 'plantilla', type: 'button', onclick: () => ed.insertar(texto) }, etiqueta)),
        h('span', { class: 'empuje' }), anteriores),
      ed.nodo,
      h('div', { class: 'barra' },
        ejecutarBoton,
        h('span', { class: 'pista' }, h('kbd', {}, 'Ctrl'), '+', h('kbd', {}, '⏎'), ' ejecuta · si hay texto seleccionado, solo ese · el ; es opcional'),
        h('span', { class: 'empuje' }),
        h('button', { class: 'enlace', type: 'button', onclick: () => { ed.poner(''); zona.replaceChildren(); estado.codigo = null; } }, 'limpiar')),
      zona),
    chuleta);
}

function pintarResultados(datos) {
  const piezas = datos.resultados.map((r, i) => h('article', { class: 'resultado' },
    h('div', { class: 'resultado-cab' }, h('span', { class: 'numero' }, `${i + 1}`), preOrden(r.orden)),
    r.columnas ? [r.filas.length ? tablaEstatica(r) : h('p', { class: 'mensaje' }, 'Sin filas.'), pieResultado(r)]
      : h('p', { class: 'mensaje' }, h('span', { class: 'ok' }, '✓ '), r.mensaje, h('span', { class: 'ms' }, ` · ${r.ms} ms`))));
  if (datos.error) {
    piezas.push(h('article', { class: 'resultado mal' },
      datos.orden_fallida && h('div', { class: 'resultado-cab' }, h('span', { class: 'numero' }, '✕'), preOrden(datos.orden_fallida)),
      h('div', { class: 'fallo' }, h('b', {}, datos.resultados.length ? 'aquí se paró' : 'no se pudo'), datos.error),
      datos.resultados.length > 0 && !datos.transaccion && h('p', { class: 'pista' }, 'Las órdenes de antes sí se hicieron. Para que sea todo o nada, envuélvelas en «empezar … confirmar».')));
  }
  if (!piezas.length) piezas.push(h('p', { class: 'mensaje' }, 'Nada que hacer.'));
  return piezas;
}

/* El editor: renglones numerados, resaltado del lenguaje y autocompletar. */
function editorCodigo(inicial) {
  const ta = h('textarea', { class: 'codigo-texto', spellcheck: 'false', autocapitalize: 'off', wrap: 'off', 'data-foco': '',
    'aria-label': 'Código SBD', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false', 'aria-controls': 'sugerencias' });
  const pre = h('pre', { class: 'codigo-resaltado', 'aria-hidden': 'true' });
  const medidor = h('div', { class: 'codigo-medidor', 'aria-hidden': 'true' });
  const numeros = h('div', { class: 'codigo-numeros', 'aria-hidden': 'true' });
  const ed = {
    ta, medidor, alEjecutar: null,
    nodo: h('div', { class: 'codigo' }, numeros, h('div', { class: 'codigo-hoja' }, pre, medidor, ta)),
    pintar() {
      pre.innerHTML = resaltar(ta.value) + '\n';
      const lineas = ta.value.split('\n').length;
      if (numeros.childElementCount !== lineas) {
        numeros.replaceChildren(...Array.from({ length: lineas }, (_, i) => h('span', {}, i + 1)));
      }
      ta.style.height = 'auto';
      ta.style.height = `${ta.scrollHeight}px`;
      pre.scrollLeft = ta.scrollLeft;
      guardar(claveBorrador(), ta.value);
    },
    poner(texto) {
      ta.value = texto;
      ed.pintar();
      ta.focus();
      ta.setSelectionRange(texto.length, texto.length);
    },
    insertar(texto) {
      // Sobre un editor vacío (o con la orden de siempre) se pone en su lugar; si no, se añade al final.
      const actual = ta.value.trim();
      if (!actual || actual === textoInicialSinBorrador()) { ed.poner(texto); return; }
      ed.poner(`${ta.value.replace(/\s+$/, '')}\n\n${texto}`);
    },
  };
  ta.value = inicial;
  ta.addEventListener('input', () => { ed.pintar(); actualizarSugerencias(ed); });
  ta.addEventListener('scroll', () => { pre.scrollLeft = ta.scrollLeft; });
  ta.addEventListener('keydown', ev => {
    if (teclaSugerencias(ev, ed)) return;
    if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); ed.alEjecutar?.(); return; }
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.altKey) {
      // Mantiene la sangría del renglón, y la aumenta después de una «{».
      ev.preventDefault();
      const antes = ta.value.slice(0, ta.selectionStart);
      const renglon = antes.slice(antes.lastIndexOf('\n') + 1);
      const sangria = renglon.match(/^\s*/)[0] + (/\{\s*$/.test(renglon) ? '  ' : '');
      ta.setRangeText(`\n${sangria}`, ta.selectionStart, ta.selectionEnd, 'end');
      ed.pintar();
    }
  });
  ta.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== ta) cerrarSugerencias(); }, 120));
  requestAnimationFrame(() => ed.pintar());
  return ed;
}

function textoInicialSinBorrador() {
  const r = estado.ruta;
  if (r.tabla) return `buscar ${nombre(r.tabla)} limite 50`;
  return r.base ? '' : 'bases';
}

/* ------------------------------------------------------- autocompletar */
const CONTEXTO_TABLA = new Set(['buscar', 'unir', 'opcional', 'completo', 'inverso', 'cruzar', 'de', 'describir',
  'vaciar', 'indexar', 'actualizar', 'tabla', 'columna', 'vista']);
const sugerencias = { lista: [], elegida: -1, movida: false, desde: 0, ed: null };

function tablasDeLaOrden(texto) {
  const info = estado.info;
  if (!info) return { tablas: [], alias: {} };
  const conocidas = new Set(info.tablas.map(t => t.nombre));
  const tablas = [], alias = {};
  const patron = /(?:^|[^\p{L}\p{N}_`])(`?)([\p{L}_][\p{L}\p{N}_]*)\1(?:\s+como\s+([\p{L}_][\p{L}\p{N}_]*))?/gu;
  for (const m of texto.matchAll(patron)) {
    if (conocidas.has(m[2])) {
      if (!tablas.includes(m[2])) tablas.push(m[2]);
      if (m[3]) alias[m[3]] = m[2];
    }
  }
  return { tablas, alias };
}

function candidatos(texto, cursor) {
  const antes = texto.slice(0, cursor);
  if (/("(?:[^"]|"")*|'(?:[^']|'')*)$/.test(antes.replace(/"(?:[^"]|"")*"|'(?:[^']|'')*'/g, ''))) return null;  // dentro de un texto
  const renglon = antes.slice(antes.lastIndexOf('\n') + 1);
  if (/(--|#)/.test(renglon.replace(/"(?:[^"]|"")*"|'(?:[^']|'')*'/g, ''))) return null;  // dentro de un comentario
  const info = estado.info;
  const tablaDe = n => info?.tablas.find(t => t.nombre === n);
  const punto = antes.match(/`?([\p{L}_][\p{L}\p{N}_]*)`?\.([\p{L}\p{N}_]*)$/u);
  if (punto) {
    const { alias } = tablasDeLaOrden(texto);
    const t = tablaDe(alias[punto[1]] || punto[1]);
    if (!t) return null;
    return { prefijo: punto[2], items: t.columnas.map(c => ({ texto: c.nombre, clase: 'columna', que: c.tipo || '' })) };
  }
  const palabra = antes.match(/[\p{L}\p{N}_]*$/u)[0];
  const tras = antes.slice(0, antes.length - palabra.length);
  const anterior = (tras.match(/([\p{L}_]+)\s*$/u) || [])[1]?.toLowerCase();
  const enTabla = anterior === 'en' && /\b(insertar(\s+o\s+actualizar)?|importar\s+("[^"]*"|'[^']*'))\s+en\s+$/i.test(tras);
  if (anterior && (CONTEXTO_TABLA.has(anterior) || enTabla) && info) {
    return { prefijo: palabra, items: [
      ...info.tablas.map(t => ({ texto: t.nombre, clase: 'tabla', que: plural(t.filas, 'fila', 'filas') })),
      ...(['buscar', 'unir', 'opcional', 'completo', 'inverso', 'cruzar', 'describir'].includes(anterior)
        ? info.vistas.map(v => ({ texto: v, clase: 'vista', que: '' })) : [])] };
  }
  if (info && (anterior === 'ejecutar' || (anterior === 'procedimiento' && /\bborrar\s+procedimiento\s+$/.test(tras)))) {
    return { prefijo: palabra, items: info.procedimientos.map(p => ({ texto: p.nombre, clase: 'procedimiento', que: `(${p.parametros.join(', ')})` })) };
  }
  if (anterior === 'usar' || (anterior === 'base' && /\b(borrar|usar|copiar)\s+base\s+$/.test(tras))) {
    return { prefijo: palabra, items: estado.bases.map(b => ({ texto: b.nombre, clase: 'base', que: plural(b.tablas, 'tabla', 'tablas') })) };
  }
  if (!palabra) return null;
  const { tablas } = tablasDeLaOrden(texto);
  const columnas = tablas.flatMap(n => tablaDe(n).columnas.map(c => ({ texto: c.nombre, clase: 'columna', que: `${n} · ${c.tipo || ''}` })));
  const vistas = new Set();
  return { prefijo: palabra, items: [
    ...columnas.filter(c => !vistas.has(c.texto) && vistas.add(c.texto)),
    ...(info?.tablas || []).map(t => ({ texto: t.nombre, clase: 'tabla', que: plural(t.filas, 'fila', 'filas') })),
    ...[...estado.palabras].filter(p => /^[\p{L}_]+$/u.test(p)).map(p => ({ texto: p, clase: 'palabra', que: '' })),
    ...estado.funciones.map(f => ({ texto: f, clase: 'función', que: '( )' }))] };
}

function actualizarSugerencias(ed) {
  const ta = ed.ta;
  if (ta.selectionStart !== ta.selectionEnd) { cerrarSugerencias(); return; }
  const c = candidatos(ta.value, ta.selectionStart);
  if (!c) { cerrarSugerencias(); return; }
  const p = c.prefijo.toLowerCase();
  const vistos = new Set();
  const lista = c.items
    .filter(it => it.texto.toLowerCase().startsWith(p) && it.texto.toLowerCase() !== p && !vistos.has(it.texto + it.clase) && vistos.add(it.texto + it.clase))
    .slice(0, 8);
  if (!lista.length) { cerrarSugerencias(); return; }
  Object.assign(sugerencias, { lista, elegida: 0, movida: false, desde: ta.selectionStart - c.prefijo.length, ed });
  pintarSugerencias(c.prefijo.length);
}

function pintarSugerencias(largo) {
  const ul = $('#sugerencias');
  const { ta, medidor } = sugerencias.ed;
  ul.replaceChildren(...sugerencias.lista.map((it, i) => h('li', {
    role: 'option', id: `sug-${i}`, 'aria-selected': String(i === sugerencias.elegida),
    onmousedown: ev => { ev.preventDefault(); aceptarSugerencia(i); } },
  h('span', { class: 'clase' }, it.clase),
  h('span', {}, h('mark', {}, it.texto.slice(0, largo)), it.texto.slice(largo)),
  it.que && h('span', { class: 'que' }, it.que))));
  ul.hidden = false;
  ta.setAttribute('aria-expanded', 'true');
  ta.setAttribute('aria-activedescendant', `sug-${sugerencias.elegida}`);
  // Se coloca bajo el cursor, midiendo el texto en un espejo invisible del editor.
  medidor.textContent = ta.value.slice(0, ta.selectionStart);
  const marca = h('span', {}, '​');
  medidor.append(marca);
  const r = marca.getBoundingClientRect();
  const x = r.left - ta.scrollLeft;
  ul.style.left = `${Math.max(12, Math.min(x - 12, innerWidth - ul.offsetWidth - 12))}px`;
  const abajo = r.bottom + 6;
  ul.style.top = `${abajo + ul.offsetHeight > innerHeight - 8 ? Math.max(8, r.top - ul.offsetHeight - 6) : abajo}px`;
}

function cerrarSugerencias() {
  sugerencias.lista = [];
  $('#sugerencias').hidden = true;
  sugerencias.ed?.ta.setAttribute('aria-expanded', 'false');
  sugerencias.ed?.ta.removeAttribute('aria-activedescendant');
}

function aceptarSugerencia(i = sugerencias.elegida) {
  const it = sugerencias.lista[i];
  const ed = sugerencias.ed;
  if (!it || !ed) return;
  const ta = ed.ta;
  // Tras una tabla o una palabra suele venir otra cosa; tras una columna, casi siempre una coma u operador.
  const texto = it.clase === 'función' || it.clase === 'procedimiento' ? `${nombre(it.texto)}(`
    : it.clase === 'columna' ? nombre(it.texto)
      : (it.clase === 'palabra' ? it.texto : nombre(it.texto)) + ' ';
  ta.setRangeText(texto, sugerencias.desde, ta.selectionStart, 'end');
  cerrarSugerencias();
  ed.pintar();
  ta.focus();
}

/* Devuelve true si la tecla la ha usado la lista de sugerencias. */
function teclaSugerencias(ev, ed) {
  if ($('#sugerencias').hidden || sugerencias.ed !== ed) return false;
  const n = sugerencias.lista.length;
  if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
    sugerencias.elegida = (sugerencias.elegida + (ev.key === 'ArrowDown' ? 1 : n - 1)) % n;
    sugerencias.movida = true;
    pintarSugerencias(ed.ta.selectionStart - sugerencias.desde);
  } else if (ev.key === 'Tab' || (ev.key === 'Enter' && !ev.ctrlKey && !ev.metaKey && sugerencias.movida)) {
    aceptarSugerencia();
  } else if (ev.key === 'Escape') {
    cerrarSugerencias();
  } else {
    return false;
  }
  ev.preventDefault();
  return true;
}

/* ------------------------------------------------------------- entrar */
function mostrarEntrada(mensaje, modo = 'entrar') {
  const dialogo = $('#ficha');
  if (dialogo.open && dialogo.dataset.tipo === 'entrar' && modo === 'entrar') return;
  const registrar = modo === 'registrar';
  const usuario = h('input', { type: 'text', id: 'entrar-usuario', autocomplete: 'username', spellcheck: 'false', autocapitalize: 'off',
    placeholder: registrar ? 'minúsculas, números y _' : null });
  const clave = h('input', { type: 'password', id: 'entrar-clave', autocomplete: registrar ? 'new-password' : 'current-password',
    placeholder: registrar ? '8 caracteres o más' : null });
  const repetir = registrar && h('input', { type: 'password', id: 'entrar-repetir', autocomplete: 'new-password' });
  const fallo = h('div', { class: 'fallo', hidden: !mensaje || /hay que entrar/.test(mensaje) }, h('b', {}, 'no se pudo'), mensaje || '');
  const primerUsuario = estado.sinUsuarios;  // se acaba de crear el primero desde este panel
  estado.sinUsuarios = false;
  const boton = h('button', { class: 'boton-tinta', type: 'submit' }, registrar ? 'crear cuenta' : 'entrar');
  const cambiar = estado.registro && h('p', { class: 'nota' }, registrar ? '¿Ya tienes cuenta? ' : '¿Todavía no tienes cuenta? ',
    h('button', { class: 'enlace', type: 'button', onclick: () => mostrarEntrada(null, registrar ? 'entrar' : 'registrar') },
      registrar ? 'entra' : 'créala gratis'));
  const d = abrirDialogo({
    titulo: registrar ? 'Crear una cuenta' : 'Entrar en el archivador', folio: 'SBD', clase: 'entrar',
    cuerpo: h('div', { class: 'ficha-cuerpo' },
      registrar && h('p', { class: 'nota' }, 'SBD es una base de datos con su propio lenguaje, en español. Con tu cuenta tienes tus propias bases, un panel para manejarlas y claves para conectar tus aplicaciones.'),
      h('div', { class: 'renglon' }, h('label', { for: 'entrar-usuario' }, 'usuario'), usuario, h('span')),
      h('div', { class: 'renglon' }, h('label', { for: 'entrar-clave' }, 'clave'), clave, h('span')),
      registrar && h('div', { class: 'renglon' }, h('label', { for: 'entrar-repetir' }, 'repite la clave'), repetir, h('span')),
      !registrar && h('p', { class: 'nota' }, primerUsuario
        ? 'Acabas de crear el primer usuario: desde ahora el panel pide entrar. Entra con él para seguir.'
        : estado.registro ? null : 'Los usuarios los crea quien administra el servidor.'),
      cambiar,
      estado.enlaces.length > 0 && h('p', { class: 'nota' }, 'Sin entrar, puedes ver: ',
        estado.enlaces.map(e => h('a', { class: 'enlace', href: e.url }, e.texto)))),
    pie: h('form', { class: 'ficha-pie', method: 'dialog', onsubmit: async ev => {
      ev.preventDefault();
      if (registrar && clave.value !== repetir.value) {
        fallo.hidden = false;
        fallo.replaceChildren(h('b', {}, 'no se pudo'), 'las dos claves no coinciden');
        repetir.select();
        return;
      }
      boton.disabled = true;
      try {
        const datos = await api(registrar ? '/api/registrar' : '/api/entrar', { usuario: usuario.value.trim(), clave: clave.value });
        estado.usuario = datos.usuario;
        entradas++;
        delete d.dataset.tipo;
        d.close();
        if (registrar) history.replaceState(null, '', enlace());
        await mostrar();
      } catch (err) {
        fallo.hidden = false;
        fallo.replaceChildren(h('b', {}, 'no se pudo'), err.message);
        (registrar && /usuario|nombre|cuenta|reservado/.test(err.message) ? usuario : clave).select();
      } finally {
        boton.disabled = false;
      }
    } }, fallo, h('div', { class: 'botones' }, boton)),
  });
  d.dataset.tipo = 'entrar';
  d.oncancel = ev => ev.preventDefault();  // sin entrar no hay nada que ver
  usuario.focus();
}

async function salir() {
  await api('/api/salir', {});
  estado.usuario = null;
  try {
    const situacion = await api('/api/estado');
    estado.registro = Boolean(situacion.registro);
    estado.enlaces = situacion.enlaces || [];
  } catch { /* se queda como estaba */ }
  $('#vista').replaceChildren();
  mostrarEntrada();
}

/* ---------------------------------------------------------------- marco */
function abrirIndice() {
  $('#indice').classList.add('abierto');
  $('#velo').hidden = false;
}
function cerrarIndice() {
  $('#indice').classList.remove('abierto');
  $('#velo').hidden = true;
}

function aplicarTema(tema) {
  if (tema) document.documentElement.dataset.tema = tema;
  else delete document.documentElement.dataset.tema;
  const oscuro = tema ? tema === 'pizarra' : matchMedia('(prefers-color-scheme: dark)').matches;
  $('#tema').textContent = oscuro ? 'papel' : 'pizarra';
}

const VISTAS = {
  bases: vistaBases, codigo: vistaCodigo, usuarios: vistaUsuarios, cuenta: vistaCuenta, copias: vistaCopias,
  tablas: vistaTablas, nueva: vistaNuevaTabla, importar: vistaImportar, exportar: vistaExportar, operaciones: () =>
    estado.ruta.tabla ? vistaOperacionesTabla() : vistaOperacionesBase(),
  examinar: vistaExaminar, estructura: vistaEstructura, buscar: vistaBuscar, insertar: vistaInsertar,
};

async function iniciar() {
  aplicarTema(leer('sbd.tema', null));
  $('#tema').addEventListener('click', () => {
    const tema = $('#tema').textContent;
    aplicarTema(tema);
    guardar('sbd.tema', tema);
  });
  $('#sello').addEventListener('click', ev => {
    const orden = ev.target.closest('[data-orden]')?.dataset.orden;
    if (orden) hacer(orden);
  });
  $('#abrir-indice').addEventListener('click', abrirIndice);
  $('#cerrar-indice').addEventListener('click', cerrarIndice);
  $('#velo').addEventListener('click', cerrarIndice);
  $('#indice').addEventListener('click', ev => { if (ev.target.closest('a')) cerrarIndice(); });
  $('#filtro-indice').addEventListener('input', pintarIndice);
  addEventListener('popstate', mostrar);  // también salta al seguir un enlace «#…»
  addEventListener('resize', cerrarSugerencias);
  document.addEventListener('scroll', cerrarSugerencias, true);
  document.addEventListener('keydown', ev => {
    if (ev.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { ev.preventDefault(); $('#filtro-indice').focus(); }
  });
  try {
    const situacion = await api('/api/estado');
    estado.sinUsuarios = situacion.sin_usuarios;
    estado.registro = Boolean(situacion.registro);
    estado.enlaces = situacion.enlaces || [];
    estado.usuario = situacion.usuario || estado.usuario;
    if (situacion.necesita_entrar) { mostrarEntrada(); return; }
  } catch (err) {
    $('#vista').replaceChildren(vacio('Sin conexión', err.message));
    return;
  }
  if (!location.hash) {
    const guardada = leer('sbd.base', null);
    history.replaceState(null, '', enlace({ base: guardada }));
  }
  await mostrar();
}

addEventListener('popstate', () => guardar('sbd.base', leerRuta().base));
iniciar();
