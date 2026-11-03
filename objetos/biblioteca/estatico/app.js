'use strict';

/* El explorador del Baúl: clases, objetos y la ficha de cada uno, con sus referencias para seguir el grafo. */

const $ = s => document.querySelector(s);
const estado = { puede: false, clase: null, oid: null, aviso: null, ordenesDelCambio: null };

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

async function api(ruta, cuerpo) {
  const opciones = cuerpo === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo) };
  let resp, datos;
  try {
    resp = await fetch(ruta, opciones);
    datos = await resp.json();
  } catch {
    throw new Error('No hay conexión con el Baúl.');
  }
  if (!resp.ok) throw new Error(datos.error || `Error ${resp.status}`);
  return datos;
}

/* ------------------------------------------------------------ piezas */
const referencia = r => h('button', { class: 'ref', type: 'button', onclick: () => irA(r.ref), title: `${r.clase} ${r.ref}` },
  r.resumen || `${r.clase} ${r.ref}`, ' ', h('span', { class: 'cl' }, `${r.clase} ${r.ref}`));

function valor(v) {
  if (v.ref != null) return referencia(v);
  if (v.valor_de) {
    return h('div', { class: 'valor' }, h('span', { class: 'cl' }, `objeto de valor · ${v.valor_de}`),
      h('dl', {}, v.campos.map(c => [h('dt', {}, c.nombre), h('dd', {}, valor(c))])));
  }
  if (v.lista) {
    if (!v.lista.length) return h('span', { class: 'nulo' }, `${v.tipo} vacía`);
    return h('div', { class: 'coleccion' }, v.lista.map(valor), h('span', { class: 'tipo' }, `${v.tipo} de ${v.lista.length}`));
  }
  if (v.dicc) {
    return h('div', { class: 'valor' }, h('span', { class: 'cl' }, 'diccionario'),
      h('dl', {}, v.dicc.map(c => [h('dt', {}, c.nombre), h('dd', {}, valor(c))])));
  }
  if (v.valor === null) return h('span', { class: 'nulo' }, 'None');
  return [String(v.valor), v.tipo && !['str'].includes(v.tipo) ? h('span', { class: 'tipo' }, v.tipo) : null];
}

/* El YAML guardado, con las etiquetas (!ref, !Direccion…) resaltadas: solo nodos de texto, nada de HTML. */
function yaml(texto) {
  const pre = h('pre', { class: 'yaml' });
  for (const linea of (texto || '').split('\n')) {
    const m = linea.match(/^(\s*-?\s*)([\wñáéíóú ]+:)?(.*)$/i);
    pre.append(m[1]);
    if (m[2]) pre.append(h('span', { class: 'clave' }, m[2]));
    m[3].split(/(!\w+)/).forEach((t, i) => pre.append(i % 2 ? h('span', { class: 'tag' }, t) : t));
    pre.append('\n');
  }
  return pre;
}

// Qué ha hecho el Baúl con cada orden, para quien no conoce su lenguaje
function paraQue(sbd) {
  if (sbd.startsWith('buscar baul')) return '¿ha guardado alguien algo? Si no, no se relee nada';
  if (sbd.includes('donde oid entre')) return 'carga los objetos que hacen falta, todos de una vez';
  if (sbd.includes('datos contiene')) return 'quién apunta a este objeto: lo busca SBD en los datos guardados';
  if (sbd.startsWith('buscar objetos ordenar oid')) return 'el índice: qué objetos hay y de qué clase, sin sus datos';
  return '';
}

function ordenes(lista, titulo = 'Órdenes que ha recibido SBD para enseñar esta ficha') {
  const cargo = lista.some(o => o.sbd.includes('donde oid entre'));
  return h('section', {}, h('h3', {}, titulo),
    lista.length ? h('ul', { class: 'ordenes' }, lista.slice(0, 30).map(o => h('li', {}, o.sbd,
      o.valores.length ? h('span', { class: 'v' }, `  · con: ${o.valores.map(v => v.length > 60 ? v.slice(0, 60) + '…' : v).join(' · ')}`) : '',
      paraQue(o.sbd) ? h('span', { class: 'para' }, paraQue(o.sbd)) : '')))
      : h('p', { class: 'pista' }, 'Ninguna.'),
    !cargo && lista.length ? h('p', { class: 'pista' }, 'Estos objetos ya estaban cargados de antes y nadie los ha cambiado: no ha hecho falta traerlos.') : '');
}

/* ------------------------------------------------------------ vistas */
async function pintarClases() {
  const r = await api('api/clases');
  $('#clases').replaceChildren(...r.clases.map(c => h('li', {}, h('button', { type: 'button', 'aria-current': String(c.clase === estado.clase),
    onclick: () => verClase(c.clase) }, c.clase, h('span', { class: 'n' }, c.objetos)))));
  $('#raices').replaceChildren(...r.raices.map(x => h('li', {}, `${x.nombre} → `, referencia(x))));
  return r;
}

function pintarLista(titulo, r) {
  $('#titulo-lista').textContent = titulo;
  $('#llamada-lista').textContent = r.llamada;
  $('#objetos').replaceChildren(...(r.objetos.length ? r.objetos.map(o => h('li', {}, h('button', { type: 'button',
    'aria-current': String(o.ref === estado.oid), onclick: () => irA(o.ref) },
  h('span', { class: 'o' }, o.ref), h('span', { class: 'r' }, o.resumen || '—', h('span', { class: 'c' }, o.clase)))))
    : [h('li', { class: 'vacio' }, 'Ningún objeto cumple la consulta.')]));
}

async function verClase(clase) {
  estado.clase = clase;
  pintarLista(clase, await api(`api/lista?clase=${encodeURIComponent(clase)}`));
  await pintarClases();
}

let turno = 0;  // si se pide otra ficha antes de que llegue esta, gana la última

async function verObjeto(oid) {
  const mio = ++turno;
  estado.oid = oid;
  const ficha = $('#ficha');
  let o;
  try {
    o = await api(`api/objeto/${oid}`);
  } catch (err) {
    if (mio === turno) ficha.replaceChildren(h('p', { class: 'mensaje mal', role: 'alert' }, err.message));
    return;
  }
  if (mio !== turno) return;
  document.querySelectorAll('.objetos li button').forEach(b => b.setAttribute('aria-current', String(b.textContent.startsWith(String(oid)))));
  const aviso = estado.aviso;
  const delCambio = estado.ordenesDelCambio;
  estado.aviso = null;
  estado.ordenesDelCambio = null;
  ficha.replaceChildren(...[
    aviso && h('p', { class: `mensaje${aviso.mal ? ' mal' : ''}`, role: aviso.mal ? 'alert' : 'status' }, aviso.texto),
    h('header', { class: 'ficha-cab' },
      h('span', { class: 'clase' }, `${o.clase} · oid ${o.oid} · versión ${o.version}`),
      h('h2', {}, o.resumen || `${o.clase} ${o.oid}`),
      h('p', {}, o.herencia.length ? `hereda de ${o.herencia.join(' → ')}` : 'objeto persistente', ' · ', h('code', {}, o.llamada))),
    h('dl', { class: 'atributos' }, o.atributos.map(a => [h('dt', {}, a.nombre), h('dd', {}, valor(a))])),
    ...o.acciones.map(accion => formularioAccion(o, accion)),
    h('section', {}, h('h3', {}, `Lo usan (${o.lo_usan.length})`),
      o.lo_usan.length ? h('div', { class: 'coleccion' }, o.lo_usan.map(referencia)) : h('p', { class: 'pista' }, 'Ningún objeto apunta a este.')),
    h('section', {}, h('h3', {}, 'Así está guardado en SBD: una fila de la tabla «objetos»'), yaml(o.yaml)),
    delCambio ? ordenes(delCambio, 'Órdenes que recibió SBD para hacer el cambio') : ordenes(o.ordenes),
  ].filter(Boolean));
}

function formularioAccion(o, accion) {
  if (accion.accion === 'prestar') {
    const socio = h('select', { 'aria-label': 'Socio' }, accion.socios.map(s => h('option', { value: String(s.ref) }, s.resumen)));
    return h('form', { class: 'accion-form', onsubmit: ev => { ev.preventDefault(); hacer('api/prestar', { socio: Number(socio.value), publicacion: o.oid }); } },
      h('span', { class: 'pista' }, 'prestar a'), socio, h('button', { class: 'boton tinta', type: 'submit' }, 'prestar'));
  }
  return h('form', { class: 'accion-form', onsubmit: ev => { ev.preventDefault(); hacer('api/devolver', { prestamo: o.oid }); } },
    h('button', { class: 'boton tinta', type: 'submit' }, 'devolver'));
}

async function hacer(ruta, cuerpo) {
  let r;
  try {
    r = await api(ruta, cuerpo);
  } catch (err) {
    r = { error: err.message };
  }
  estado.aviso = r.error ? { texto: r.error, mal: true } : { texto: `${r.mensaje} (${r.llamada})` };
  estado.ordenesDelCambio = r.ordenes || null;
  const destino = r.oid ?? estado.oid;
  if (estado.clase) await verClase(estado.clase);
  else await pintarClases();
  if (location.hash === `#${destino}`) await verObjeto(destino);
  else location.hash = String(destino);
}

function irA(oid) {
  if (location.hash === `#${oid}`) verObjeto(oid);
  else location.hash = String(oid);
}

async function alCambiarDireccion() {
  const oid = Number(location.hash.slice(1));
  if (Number.isInteger(oid) && oid > 0) {
    await verObjeto(oid);
    $('#ficha').focus({ preventScroll: true });
  }
}

async function iniciar() {
  try {
    const e = await api('api/estado');
    estado.puede = e.puede;
    $('#conexion').textContent = `Guardado en: ${e.conexion}.`;
    $('#quien').replaceChildren(...(e.puede
      ? [e.usuario ? `Has entrado como ${e.usuario}: puedes prestar y devolver.` : 'Puedes prestar y devolver.']
      : ['Estás mirando. Para prestar o devolver, ', h('a', { href: '../' }, 'entra en el panel'), ' con permiso «todo».']));
    $('#consulta').addEventListener('submit', async ev => {
      ev.preventDefault();
      const tipo = $('#tipo-consulta').value;
      try {
        pintarLista($('#tipo-consulta').selectedOptions[0].textContent + ` «${$('#valor-consulta').value}»`,
          await api(`api/consulta?tipo=${tipo}&valor=${encodeURIComponent($('#valor-consulta').value)}`));
      } catch (err) {
        pintarLista(err.message, { objetos: [], llamada: '' });
      }
    });
    const r = await pintarClases();
    await verClase(r.clases.find(c => c.clase === 'Libro') ? 'Libro' : r.clases[0]?.clase);
    window.addEventListener('hashchange', alCambiarDireccion);
    if (location.hash) await alCambiarDireccion();
    else if (r.raices[0]) location.hash = String(r.raices[0].ref);
  } catch (err) {
    $('#ficha').replaceChildren(h('p', { class: 'mensaje mal', role: 'alert' }, err.message));
  }
}

iniciar();
