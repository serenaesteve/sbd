'use strict';

/* La biblioteca: cada acción llama a la API y pinta, además del resultado, su camino hasta SBD. */

const $ = s => document.querySelector(s);
const estado = { puede: false, usuario: null, pestana: 'catalogo', q: '', aviso: null };

function h(tag, props, ...hijos) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (k === 'value') n.value = v;
    else if (typeof v === 'boolean') n[k] = v;
    else n.setAttribute(k, v);
  }
  for (const x of hijos.flat(Infinity)) if (x != null && x !== false) n.append(x instanceof Node ? x : String(x));
  return n;
}

const PALABRAS_SQL = /\b(SELECT|DISTINCT|FROM|WHERE|JOIN|LEFT|OUTER|INNER|ON|AS|AND|OR|NOT|IN|IS|NULL|GROUP|BY|HAVING|ORDER|DESC|ASC|LIMIT|OFFSET|INSERT|INTO|VALUES|UPDATE|SET|DELETE|COMMIT|ROLLBACK|CASE|WHEN|THEN|ELSE|END|OVER|PARTITION|LIKE|COUNT|SUM|MIN|RANK)\b/g;
const PALABRAS_SBD = /\b(buscar|calcular|unir|opcional|como|en|donde|agrupar|teniendo|mostrar|distintos|ordenar|desc|limite|saltar|insertar|actualizar|poner|borrar|de|y|o|no|es|nulo|parece|confirmar|deshacer|empezar|contar|sumar|minimo|puesto|sobre|por|caso|cuando|entonces|sino|fin)\b/g;

/* Un fragmento resaltado, sin HTML: los trozos son nodos de texto. */
function codigo(texto, patron) {
  const c = h('code');
  const partes = texto.split(/("(?:[^"]|"")*"|'(?:[^']|'')*')/);
  partes.forEach((trozo, i) => {
    if (i % 2) { c.append(h('span', { class: 's' }, trozo)); return; }
    let ultimo = 0;
    for (const m of trozo.matchAll(patron)) {
      c.append(trozo.slice(ultimo, m.index), h('span', { class: 'k' }, m[0]));
      ultimo = m.index + m[0].length;
    }
    c.append(trozo.slice(ultimo));
  });
  return c;
}

function pintarRuta(r) {
  const destino = $('#ruta');
  if (!r || !r.ruta) return;
  const bloques = [h('div', { class: 'estrato objeto' }, h('b', {}, 'objeto de Python'), h('code', {}, r.llamada || ''))];
  const pasos = r.ruta.slice(0, 40);
  const paso = p => h('div', { class: 'paso' },
    h('div', { class: 'estrato' }, h('b', {}, 'SQL que genera SQLAlchemy'), codigo(p.sql, PALABRAS_SQL)),
    h('p', { class: 'flecha', 'aria-hidden': 'true' }, '↓ el conector lo traduce'),
    h('div', { class: 'estrato sbd' }, h('b', {}, 'orden que recibe SBD'), codigo(p.sbd, PALABRAS_SBD),
      p.valores.length > 0 && h('p', { class: 'valores' }, `con: ${p.valores.join(' · ')}`)));
  // Si algo cambia, lo que importa es la escritura: las lecturas de antes quedan plegadas.
  const lecturas = pasos.filter(p => p.sql.startsWith('SELECT'));
  if (lecturas.length && lecturas.length < pasos.length) {
    bloques.push(h('details', { class: 'lecturas' }, h('summary', {}, `${lecturas.length} ${lecturas.length === 1 ? 'lectura' : 'lecturas'} antes de escribir`), lecturas.map(paso)));
    bloques.push(...pasos.filter(p => !p.sql.startsWith('SELECT')).map(paso));
  } else {
    bloques.push(...pasos.map(paso));
  }
  if (r.ruta.length > pasos.length) bloques.push(h('p', { class: 'valores' }, `… y ${r.ruta.length - pasos.length} órdenes más`));
  if (!r.ruta.length) bloques.push(h('p', { class: 'valores' }, 'No ha hecho falta preguntar a la base.'));
  destino.replaceChildren(...bloques);
}

async function api(ruta, cuerpo) {
  const opciones = cuerpo === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo) };
  let resp, datos;
  try {
    resp = await fetch(ruta, opciones);
    datos = await resp.json();
  } catch {
    throw new Error('No hay conexión con la biblioteca.');
  }
  if (!resp.ok) throw new Error(datos.error || `Error ${resp.status}`);
  return datos;
}

function tabla(cabecera, filas) {
  return h('div', { class: 'hoja' }, h('table', {}, h('thead', {}, h('tr', {}, cabecera.map(c => h('th', { class: c.num ? 'num' : null }, c.t ?? c)))), h('tbody', {}, filas)));
}

function avisoActual() {
  const a = estado.aviso;
  estado.aviso = null;
  return a && h('p', { class: `mensaje${a.mal ? ' mal' : ''}`, role: a.mal ? 'alert' : 'status' }, a.texto);
}

/* ------------------------------------------------------------ pestañas */
async function vistaCatalogo() {
  const r = await api(`api/libros?q=${encodeURIComponent(estado.q)}`);
  pintarRuta(r);
  const buscador = h('input', { class: 'buscador', type: 'search', value: estado.q, placeholder: 'título o autor', 'aria-label': 'Buscar libros',
    oninput: retrasar(ev => { estado.q = ev.target.value; mostrar({ foco: '.buscador' }); }, 300) });
  const filas = r.datos.map(x => h('tr', {},
    h('td', {}, h('span', { class: 'titulo-libro' }, x.titulo), h('span', { class: 'generos' }, x.generos.join(' · '))),
    h('td', {}, x.autor),
    h('td', { class: 'num tenue' }, x.anio ?? ''),
    h('td', { class: 'num' }, x.precio.replace('.', ',')),
    h('td', { class: `num${x.disponibles === 0 ? ' agotado' : ''}` }, `${x.disponibles} de ${x.ejemplares}`),
    estado.puede && h('td', {}, h('button', { class: 'accion', type: 'button', disabled: x.disponibles === 0, onclick: () => fichaPrestar(x) }, 'prestar'))));
  return [
    h('div', { class: 'barra' }, buscador, h('span', { class: 'empuje' }),
      estado.puede && h('button', { class: 'boton', type: 'button', onclick: fichaLibro }, '+ nuevo libro')),
    r.datos.length ? tabla(['título', 'autor', { t: 'año', num: true }, { t: 'precio', num: true }, { t: 'libres', num: true }, ...(estado.puede ? [''] : [])], filas)
      : h('p', { class: 'vacio' }, `Ningún libro coincide con «${estado.q}».`)];
}

async function vistaPrestamos() {
  const r = await api('api/prestamos');
  pintarRuta(r);
  const hoy = new Date().toISOString().slice(0, 10);
  const filas = r.datos.map(p => h('tr', {},
    h('td', {}, h('span', { class: 'titulo-libro' }, p.libro)), h('td', {}, p.socio), h('td', { class: 'tenue' }, p.fecha),
    h('td', { class: p.vence < hoy ? 'agotado' : null }, p.vence, p.vence < hoy ? ' · vencido' : ''),
    estado.puede && h('td', {}, h('button', { class: 'accion', type: 'button', onclick: () => accion('api/devolver', { prestamo: p.id }) }, 'devolver'))));
  return r.datos.length ? tabla(['libro', 'socio', 'desde', 'vence', ...(estado.puede ? [''] : [])], filas)
    : h('p', { class: 'vacio' }, 'No hay ningún libro prestado.');
}

async function vistaSocios() {
  const r = await api('api/socios');
  pintarRuta(r);
  return [
    estado.puede && h('div', { class: 'barra' }, h('span', { class: 'empuje' }), h('button', { class: 'boton', type: 'button', onclick: fichaSocio }, '+ nuevo socio')),
    tabla([{ t: 'nº', num: true }, 'nombre', 'correo', 'alta', { t: 'libros', num: true }], r.datos.map(s => h('tr', {},
      h('td', { class: 'num tenue' }, s.id), h('td', {}, s.nombre), h('td', { class: 'tenue' }, s.email), h('td', { class: 'tenue' }, s.alta), h('td', { class: 'num' }, s.libros))))];
}

async function vistaInformes() {
  const r = await api('api/informes');
  pintarRuta(r);
  return h('div', { class: 'informes' }, r.datos.map(i => h('section', {}, h('h2', {}, i.titulo),
    i.filas.length ? tabla(i.columnas, i.filas.map(f => h('tr', {}, f.map(v => h('td', {}, v ?? ''))))) : h('p', { class: 'vacio' }, 'Nada que enseñar.'))));
}

const PESTANAS = { catalogo: ['Catálogo', vistaCatalogo], prestamos: ['Préstamos', vistaPrestamos], socios: ['Socios', vistaSocios], informes: ['Informes SQL', vistaInformes] };

function retrasar(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

let turno = 0;
async function mostrar({ foco } = {}) {
  const mio = ++turno;
  $('#pestanas').replaceChildren(...Object.entries(PESTANAS).map(([id, [t]]) => h('button', { class: 'pestana', type: 'button',
    'aria-current': id === estado.pestana ? 'page' : null, onclick: () => { estado.pestana = id; mostrar(); } }, t)));
  let contenido;
  try {
    contenido = [await PESTANAS[estado.pestana][1]()].flat();
  } catch (err) {
    contenido = [h('p', { class: 'mensaje mal', role: 'alert' }, err.message)];
  }
  if (mio !== turno) return;
  $('#vista').replaceChildren(...[avisoActual(), ...contenido].filter(Boolean));
  if (foco) {
    const n = $(foco);
    n?.focus();
    n?.setSelectionRange?.(n.value.length, n.value.length);
  }
}

/* ------------------------------------------------------------ acciones */
async function accion(ruta, cuerpo, { alTerminar } = {}) {
  let r;
  try {
    r = await api(ruta, cuerpo);
  } catch (err) {
    r = { error: err.message };
  }
  estado.aviso = r.error ? { texto: r.error, mal: true } : { texto: r.mensaje };
  if (!r.error) alTerminar?.();
  const ruta_escritura = r;
  await mostrar();
  pintarRuta(ruta_escritura);   // lo que se ve es el camino del cambio, no el de la lista que se repinta
  return r;
}

function abrirFicha(titulo, folio, campos, enviar) {
  const d = $('#ficha');
  const fallo = h('p', { class: 'mensaje mal', role: 'alert', hidden: true });
  const boton = h('button', { class: 'boton tinta', type: 'submit' }, 'guardar');
  d.replaceChildren(h('form', { method: 'dialog', onsubmit: async ev => {
    ev.preventDefault();
    boton.disabled = true;
    const r = await enviar();
    boton.disabled = false;
    if (r?.error) { fallo.hidden = false; fallo.textContent = r.error; return; }
    d.close();
  } },
  h('div', { class: 'ficha-cab' }, h('h2', {}, titulo), h('span', {}, folio)),
  campos.map(([etiqueta, control]) => h('label', { class: 'renglon' }, etiqueta, control)),
  fallo,
  h('div', { class: 'ficha-pie' }, h('button', { class: 'accion', type: 'button', onclick: () => d.close() }, 'cancelar'), boton)));
  d.showModal();
}

async function fichaPrestar(libro) {
  const socios = (await api('api/socios')).datos;
  const socio = h('select', {}, socios.map(s => h('option', { value: String(s.id) }, `${s.nombre} · ${s.libros} prestados`)));
  abrirFicha(libro.titulo, 'préstamo', [['socio', socio]], () => accion('api/prestar', { socio: Number(socio.value), libros: [libro.id] }));
}

function fichaSocio() {
  const nombre = h('input', { required: true, maxlength: '80', autocomplete: 'off' });
  const email = h('input', { required: true, type: 'email', maxlength: '120', autocomplete: 'off' });
  abrirFicha('Nuevo socio', 'alta', [['nombre', nombre], ['correo', email]], () => accion('api/socios', { nombre: nombre.value, email: email.value }));
}

function fichaLibro() {
  const titulo = h('input', { required: true, maxlength: '160', autocomplete: 'off' });
  const isbn = h('input', { required: true, maxlength: '20', autocomplete: 'off' });
  const autor = h('input', { required: true, maxlength: '80', autocomplete: 'off' });
  const precio = h('input', { inputmode: 'decimal', value: '0', autocomplete: 'off' });
  const ejemplares = h('input', { type: 'number', min: '1', max: '999', value: '1' });
  const generos = h('input', { placeholder: 'Novela, Cuento', autocomplete: 'off' });
  abrirFicha('Nuevo libro', 'alta', [['título', titulo], ['ISBN', isbn], ['autor', autor], ['precio', precio], ['ejemplares', ejemplares], ['géneros', generos]],
    () => accion('api/libros', { titulo: titulo.value, isbn: isbn.value, autor: autor.value, precio: precio.value, ejemplares: Number(ejemplares.value), generos: generos.value }));
}

/* -------------------------------------------------------------- inicio */
async function iniciar() {
  try {
    const e = await api('api/estado');
    estado.puede = e.puede;
    estado.usuario = e.usuario;
    $('#conexion').textContent = `Conectada a: ${e.conexion}.`;
    $('#quien').replaceChildren(...(e.puede
      ? [e.usuario ? `Has entrado como ${e.usuario}: puedes prestar, devolver y dar de alta.` : 'Puedes prestar, devolver y dar de alta.']
      : ['Estás mirando. Para prestar o devolver, ', h('a', { href: '../' }, 'entra en el panel'), ' con permiso «todo».']));
  } catch (err) {
    $('#quien').textContent = err.message;
  }
  await mostrar();
}

iniciar();
