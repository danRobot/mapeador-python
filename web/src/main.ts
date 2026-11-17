import "./styles.css";
import { GraphView, type Modo } from "./engine/view";
import { Minimap } from "./engine/minimap";
import { Arbol, FILTROS_DEFECTO, Modelo, type Fila, type Filtros, destinoEnCamino, filasAuto } from "./engine/model";
import { Buscador, KINDS_ESTRUCTURA, KINDS_FLUJO, KINDS_TIPOS, camino, type Resultado } from "./engine/busqueda";
import { plantumlClases, plantumlSecuencia } from "./engine/plantuml";
import { DEFAULT_CONFIG } from "./config";
import type { Direction } from "./engine/layout";
import type { ThemeMode } from "./engine/theme";
import type { Documento } from "./tipos";
import { renderPanel } from "./panel";
import { Explorador, buscarInicio, conFlujo, etiquetaInicio } from "./archivos";
import {
  ErrorServidor,
  analizarCarpetaServidor,
  analizarEnServidor,
  desdeArrastre,
  desdeInput,
  fuenteServidor,
  leerTextos,
  listarProyectos,
  type Seleccion,
} from "./carga";

// ------------------------------------------------------------------ utilidades

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const fmtInt = (n: number) => n.toLocaleString("es-CO");
const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`);
const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const store = {
  get(k: string) {
    try {
      return localStorage.getItem(`mpy:${k}`);
    } catch {
      return null;
    }
  },
  set(k: string, v: string) {
    try {
      localStorage.setItem(`mpy:${k}`, v);
    } catch {
      /* almacenamiento no disponible */
    }
  },
};

let toastTimer = 0;
function toast(msg: string) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => (el.hidden = true), 2000);
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  toast("Copiado al portapapeles");
}

function descargar(nombre: string, contenido: Blob) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(contenido);
  a.download = nombre;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

// ------------------------------------------------------------------ estado

let doc: Documento | null = null;
let modelo: Modelo | null = null;
let arbol: Arbol | null = null;
let buscador: Buscador | null = null;
let modo: Modo = "flujo";
/** Definición desde la que empieza el mapa (un archivo, o una función/clase de un archivo); -1 = sin elegir. */
let inicio = -1;
/** Código del proyecto si se abrió la carpeta desde el navegador (para el panel). */
let fuentesLocales: Record<string, string> | null = null;
let origenServidor: string | null = null;
let lastLayoutMs = 0;
let visibleCount = 0;
let tiempoCarga = 0;

const wrap = $("canvas-wrap");
const view = new GraphView(wrap, {
  onSelect: (n, row) => (n >= 0 ? abrirPanel(n, row) : cerrarPanel(false)),
  onViewChange: () => scheduleHud(),
  onLayout: ({ ms, visible }) => {
    lastLayoutMs = ms;
    visibleCount = visible;
    minimap.invalidate();
    updateStatus();
  },
  onStageChange: () => renderCrumbs(),
});
const minimap = new Minimap($<HTMLCanvasElement>("minimap"), view);
const explorador = new Explorador({ lista: $("archivos-lista"), filtro: $<HTMLInputElement>("archivos-filtro"), contador: $("archivos-num"), onElegir: iniciarDesde });

view.tooltip = (n, row, button) => {
  if (!modelo || !arbol) return "";
  const d = modelo.def(arbol.def[n]);
  if (row < 0) return `${d.qual}${d.firma ? d.firma : ""}${d.doc ? "\n" + d.doc : ""}`;
  const f = modelo.filas(d.id)[row];
  if (!f) return "";
  if (button && view.isDrillNode(n)) return "Abrir en una etapa nueva";
  return tooltipFila(n, f);
};

function tooltipFila(n: number, f: Fila): string {
  const m = modelo!;
  const ll = f.llamada;
  if (ll) {
    let t = `línea ${ll.linea}: ${ll.texto}`;
    if (ll.externo) t += `\n${ll.externo.tipo} ${ll.externo.verbo ?? ""} ${destinoEnCamino(m, arbol!, n, ll)}`;
    if (ll.destinos?.length) t += `\n→ ${ll.destinos.map(x => m.def(x).qual).join("\n→ ")}`;
    if (ll.confianza && ll.confianza !== "alta") t += `\nconfianza ${ll.confianza} (${ll.mecanismo})`;
    if (ll.motivo) t += `\n${ll.motivo}`;
    return t;
  }
  if (f.param) return `${f.param.nombre}: ${f.param.tipo ?? "?"}${f.param.origen ? ` (${f.param.origen})` : ""}`;
  return `${f.izq} ${f.der}`;
}

let hudRaf = 0;
function scheduleHud() {
  if (hudRaf) return;
  hudRaf = requestAnimationFrame(() => {
    hudRaf = 0;
    const pct = view.zoom * 100;
    $("zoom-label").textContent = `${pct >= 10 ? Math.round(pct) : pct.toFixed(1)}%`;
    minimap.update();
    $("st-perf").textContent = doc ? `carga ${fmtMs(tiempoCarga)} · layout ${fmtMs(lastLayoutMs)} · frame ${view.lastDrawMs.toFixed(1)} ms` : "";
  });
}

function updateStatus() {
  if (!doc) {
    $("st-file").textContent = "Sin proyecto";
    $("st-stats").textContent = "";
    $("st-adv").hidden = true;
    return;
  }
  const t = doc.proyecto.totales;
  $("st-file").textContent = `${doc.proyecto.nombre} · ${fmtInt(t.archivos)} archivos · ${fmtInt(t.lineas)} líneas`;
  $("st-stats").textContent = `${fmtInt(t.clases)} clases · ${fmtInt(t.funciones + t.metodos)} funciones · ${fmtInt(t.llamadas)} llamadas (${t.resueltas_pct}% resueltas) · ${fmtInt(visibleCount)} en pantalla`;
  const adv = doc.advertencias.length;
  const st = $("st-adv");
  st.hidden = !adv;
  st.textContent = `⚠ ${adv} advertencia${adv === 1 ? "" : "s"}`;
  st.title = doc.advertencias.slice(0, 20).join("\n");
  scheduleHud();
}

// ------------------------------------------------------------------ preferencias

const TEMAS: ThemeMode[] = ["dark", "light", "plantuml"];
const ICONO_TEMA: Record<ThemeMode, string> = {
  dark: '<svg viewBox="0 0 24 24"><path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/></svg>',
  light: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  plantuml: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 9h16M8 13h8M8 16h5"/></svg>',
};

function applyTheme(mode: ThemeMode) {
  document.documentElement.dataset.theme = mode;
  view.setTheme(mode);
  minimap.invalidate();
  store.set("theme", mode);
  $("btn-theme").innerHTML = ICONO_TEMA[mode];
  $("btn-theme").title = `Tema: ${mode === "dark" ? "oscuro" : mode === "light" ? "claro" : "PlantUML"} (clic para cambiar)`;
}

function applyDirection(dir: Direction) {
  view.setDirection(dir);
  store.set("dir", dir);
  $("dir-icon").innerHTML = dir === "RIGHT" ? '<path d="M4 12h14m-5-5 5 5-5 5"/>' : '<path d="M12 4v14m-5-5 5 5 5-5"/>';
  $("btn-dir").title = dir === "RIGHT" ? "Layout: izquierda → derecha" : "Layout: arriba → abajo";
  minimap.invalidate();
}

function applyWheel(mode: "zoom" | "pan") {
  view.wheelMode = mode;
  store.set("wheel", mode);
  const b = $("wheel-mode");
  b.title = mode === "zoom" ? "Rueda: zoom (Shift = desplazar). Clic para cambiar a desplazar" : "Rueda: desplazar (Ctrl = zoom). Clic para cambiar a zoom";
  b.classList.toggle("toggle", true);
  b.classList.toggle("on", mode === "pan");
}

applyTheme((store.get("theme") as ThemeMode) || "dark");
view.dir = (store.get("dir") as Direction) || "RIGHT";
applyDirection(view.dir);
applyWheel((store.get("wheel") as "zoom" | "pan") || "zoom");
if (store.get("grid") === "0") {
  view.setShowGrid(false);
  $("btn-grid").classList.remove("on");
}
if (store.get("minimap") === "0") {
  $("minimap").hidden = true;
  $("toggle-minimap").classList.remove("on");
}

// ------------------------------------------------------------------ ajustes

interface Ajustes {
  levels: number;
  autoFit: boolean;
  pruebas: boolean;
  filtros: Filtros;
}

function cargarAjustes(): Ajustes {
  const a: Ajustes = { levels: DEFAULT_CONFIG.stageLevels, autoFit: DEFAULT_CONFIG.autoFit, pruebas: false, filtros: { ...FILTROS_DEFECTO } };
  try {
    const g = JSON.parse(store.get("ajustes") || "null") as Partial<Ajustes> | null;
    if (g) Object.assign(a, g, { filtros: { ...FILTROS_DEFECTO, ...(g.filtros ?? {}) } });
  } catch {
    /* ignorar */
  }
  const qp = new URLSearchParams(location.search);
  if (qp.has("niveles")) a.levels = Number(qp.get("niveles")) || 0;
  a.levels = a.levels > 0 ? Math.max(2, Math.min(50, Math.floor(a.levels))) : 0;
  return a;
}

let ajustes = cargarAjustes();
view.levels = ajustes.levels;
view.autoFit = ajustes.autoFit;
view.autoFitMinZoom = DEFAULT_CONFIG.autoFitMinZoom;

const FILTRO_IDS: [keyof Filtros, string][] = [
  ["parametros", "f-parametros"],
  ["noResueltas", "f-noresueltas"],
  ["libreria", "f-libreria"],
  ["excepciones", "f-excepciones"],
  ["referencias", "f-referencias"],
  ["privados", "f-privados"],
];

function syncAjustes() {
  $<HTMLInputElement>("cfg-levels-off").checked = ajustes.levels === 0;
  $<HTMLInputElement>("cfg-levels").disabled = ajustes.levels === 0;
  $<HTMLInputElement>("cfg-levels").value = String(ajustes.levels || DEFAULT_CONFIG.stageLevels);
  $<HTMLInputElement>("cfg-autofit").checked = ajustes.autoFit;
  $<HTMLInputElement>("cfg-pruebas").checked = ajustes.pruebas;
  for (const [k, id] of FILTRO_IDS) $<HTMLInputElement>(id).checked = ajustes.filtros[k];
}

function leerAjustes(): Ajustes {
  const lv = Number($<HTMLInputElement>("cfg-levels").value);
  const filtros = { ...ajustes.filtros };
  for (const [k, id] of FILTRO_IDS) filtros[k] = $<HTMLInputElement>(id).checked;
  return {
    levels: $<HTMLInputElement>("cfg-levels-off").checked ? 0 : Math.max(2, Math.min(50, Math.floor(lv) || DEFAULT_CONFIG.stageLevels)),
    autoFit: $<HTMLInputElement>("cfg-autofit").checked,
    pruebas: $<HTMLInputElement>("cfg-pruebas").checked,
    filtros,
  };
}

function aplicarAjustes(nuevos: Ajustes) {
  const cambianFiltros = JSON.stringify(nuevos.filtros) !== JSON.stringify(ajustes.filtros);
  const cambianNiveles = nuevos.levels !== ajustes.levels;
  ajustes = nuevos;
  store.set("ajustes", JSON.stringify(ajustes));
  view.autoFit = ajustes.autoFit;
  if (cambianFiltros && modelo) {
    modelo.setFiltros(ajustes.filtros);
    view.relayout(view.selected >= 0 ? view.selected : view.stageRoot);
    minimap.invalidate();
    if (view.selected >= 0) abrirPanel(view.selected, -1);
  }
  if (cambianNiveles) view.setLevels(ajustes.levels);
  syncAjustes();
  renderCrumbs();
}

syncAjustes();
for (const id of ["cfg-levels", "cfg-levels-off", "cfg-autofit", "cfg-pruebas", ...FILTRO_IDS.map(x => x[1])]) $(id).addEventListener("change", () => aplicarAjustes(leerAjustes()));
$("cfg-reset").addEventListener("click", () => aplicarAjustes({ levels: DEFAULT_CONFIG.stageLevels, autoFit: DEFAULT_CONFIG.autoFit, pruebas: false, filtros: { ...FILTROS_DEFECTO } }));
const settingsPop = $("settings");
$("btn-settings").addEventListener("click", e => {
  e.stopPropagation();
  settingsPop.hidden = !settingsPop.hidden;
  $("btn-settings").classList.toggle("on", !settingsPop.hidden);
});
document.addEventListener("pointerdown", e => {
  const tgt = e.target as Node;
  if (!settingsPop.hidden && !settingsPop.contains(tgt) && !$("btn-settings").contains(tgt)) {
    settingsPop.hidden = true;
    $("btn-settings").classList.remove("on");
  }
  if (!$("search-results").hidden && !(tgt as HTMLElement).closest?.(".search")) $("search-results").hidden = true;
});

// ------------------------------------------------------------------ breadcrumbs

function etiquetaNodo(n: number) {
  const d = modelo!.def(arbol!.def[n]);
  if (d.tipo === "main" || d.tipo === "nivel") return d.archivo ?? d.qual;
  if (d.tipo === "metodo" && d.clase !== undefined && d.clase >= 0) return `${modelo!.def(d.clase).nombre}.${d.nombre}`;
  if (d.tipo === "app") return `app ${d.nombre}`;
  if (d.tipo === "modulo") return d.archivo ?? d.qual;
  return d.tipo === "paquete" ? d.qual : d.nombre;
}

function renderCrumbs() {
  const bar = $("crumbs");
  document.body.classList.toggle("sin-mapa", !view.stages.length);
  if (!doc || !arbol || !view.stages.length) {
    bar.hidden = true;
    view.stageCaption = "";
    return;
  }
  const stages = view.stages;
  bar.hidden = false;
  bar.innerHTML = "";
  const back = document.createElement("button");
  back.className = "crumb-back";
  back.title = "Volver a la etapa anterior (Retroceso / Alt+←)";
  back.disabled = stages.length < 2;
  back.innerHTML = '<svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>';
  back.addEventListener("click", () => view.goBack());
  bar.appendChild(back);
  stages.forEach((s, i) => {
    if (i > 0) {
      const sep = document.createElement("span");
      sep.className = "crumb-sep";
      sep.textContent = "›";
      bar.appendChild(sep);
    }
    const b = document.createElement("button");
    b.className = "crumb";
    let label = etiquetaNodo(s.root);
    if (label.length > 42) label = "…" + label.slice(-40);
    b.textContent = label;
    b.title = modelo!.def(arbol!.def[s.root]).qual;
    if (i === stages.length - 1) {
      b.classList.add("current");
      b.setAttribute("aria-current", "page");
    } else b.addEventListener("click", () => view.goToStage(i));
    if (i === 0) b.classList.add("crumb-root");
    bar.appendChild(b);
  });
  const prev = view.stageSibling(-1);
  const next = view.stageSibling(1);
  if (prev >= 0 || next >= 0) {
    const mk = (n: number, dirn: "prev" | "next") => {
      const b = document.createElement("button");
      b.className = "crumb-sib";
      b.disabled = n < 0;
      b.title = n < 0 ? "" : `${dirn === "prev" ? "Anterior" : "Siguiente"}: ${etiquetaNodo(n)}  (tecla ${dirn === "prev" ? "[" : "]"})`;
      b.innerHTML = dirn === "prev" ? '<svg viewBox="0 0 24 24"><path d="M14 7l-5 5 5 5"/></svg>' : '<svg viewBox="0 0 24 24"><path d="M10 7l5 5-5 5"/></svg>';
      if (n >= 0) b.addEventListener("click", () => view.switchStageRoot(n));
      return b;
    };
    const grp = document.createElement("span");
    grp.className = "crumb-sibs";
    grp.append(mk(prev, "prev"), mk(next, "next"));
    bar.appendChild(grp);
  }
  const info = document.createElement("span");
  info.className = "crumb-info";
  info.textContent = view.levels > 0 ? `${view.levels} niveles por etapa` : "sin límite de niveles";
  bar.appendChild(info);
  bar.scrollLeft = bar.scrollWidth;
  // camino de la entrada a la raíz de la etapa, sobre el nodo raíz
  if (stages.length > 1) {
    const partes: string[] = [];
    for (let x = view.stageRoot; x >= 0; x = arbol.padre[x]) partes.push(etiquetaNodo(x));
    view.stageCaption = partes.reverse().join(" › ");
  } else view.stageCaption = "";
  view.requestDraw();
  updateStatus();
}

// ------------------------------------------------------------------ carga

function showLoading(text: string, ratio: number | null, sub = "") {
  $("loading").hidden = false;
  $("loading-text").textContent = text;
  $("loading-bar").style.width = ratio == null ? "100%" : `${Math.round(ratio * 100)}%`;
  $("loading-bar").style.opacity = ratio == null ? "0.35" : "1";
  $("loading-sub").textContent = sub;
}
const hideLoading = () => ($("loading").hidden = true);

function showError(message: string, titulo = "No se pudo analizar") {
  hideLoading();
  $("error").hidden = false;
  $("error-title").textContent = titulo;
  $("error-msg").textContent = message;
}

/** Quita de la URL lo que era de otro proyecto (para que `?inicio=` no se aplique a otro). */
function limpiarUrl(...claves: string[]) {
  const u = new URL(location.href);
  let cambio = false;
  for (const k of claves)
    if (u.searchParams.has(k)) {
      u.searchParams.delete(k);
      cambio = true;
    }
  if (cambio) history.replaceState(null, "", u);
}

async function cargarSeleccion(sel: Seleccion) {
  if (!sel.archivos.some(a => /\.pyw?$/.test(a.ruta))) {
    showError(`«${sel.nombre}» no tiene archivos .py (se revisaron ${fmtInt(sel.vistos)} archivos, sin contar venv, node_modules, .git…).`);
    return;
  }
  const t0 = performance.now();
  showLoading("Leyendo archivos", 0, `${sel.nombre} · ${fmtInt(sel.archivos.length)} archivos .py`);
  try {
    const textos = await leerTextos(sel.archivos, hechos => showLoading("Leyendo archivos", hechos / sel.archivos.length, `${fmtInt(hechos)} de ${fmtInt(sel.archivos.length)}`));
    showLoading("Analizando en el servidor", null, `${sel.nombre} · ${fmtInt(Object.keys(textos).length)} archivos`);
    const d = await analizarEnServidor(sel.nombre, textos, { incluir_pruebas: ajustes.pruebas });
    limpiarUrl("proyecto", "json", "inicio");
    fuentesLocales = textos;
    origenServidor = null;
    tiempoCarga = performance.now() - t0;
    mostrarDocumento(d);
  } catch (e) {
    showError(e instanceof ErrorServidor ? e.message : `Error inesperado: ${(e as Error).message}`);
  }
}

async function cargarServidor(ruta: string) {
  const t0 = performance.now();
  showLoading("Analizando en el servidor", null, ruta);
  try {
    const d = await analizarCarpetaServidor(ruta, { incluir_pruebas: ajustes.pruebas });
    if (new URLSearchParams(location.search).get("proyecto") !== ruta) limpiarUrl("json", "inicio");
    fuentesLocales = null;
    origenServidor = d.proyecto.servidor ?? ruta;
    tiempoCarga = performance.now() - t0;
    mostrarDocumento(d);
    const u = new URL(location.href);
    u.searchParams.set("proyecto", ruta);
    history.replaceState(null, "", u);
  } catch (e) {
    showError(e instanceof ErrorServidor ? e.message : `Error inesperado: ${(e as Error).message}`);
  }
}

async function cargarJson(texto: string, nombre: string) {
  try {
    const d = JSON.parse(texto) as Documento;
    if (!d || !Array.isArray(d.defs) || !d.proyecto) throw new Error("no es un JSON del mapeador Python");
    fuentesLocales = d.fuentes ?? null;
    origenServidor = null;
    mostrarDocumento(d);
  } catch (e) {
    showError(`${nombre}: ${(e as Error).message}`, "No se pudo abrir el JSON");
  }
}

async function cargarUrlJson(url: string) {
  const t0 = performance.now();
  showLoading("Descargando", null, url);
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const texto = await res.text();
    tiempoCarga = performance.now() - t0;
    await cargarJson(texto, url);
  } catch (e) {
    showError(`${url}: ${(e as Error).message}`, "No se pudo abrir el JSON");
  }
}

function mostrarDocumento(d: Documento) {
  doc = d;
  modelo = new Modelo(d, view.charW);
  modelo.setFiltros(ajustes.filtros);
  buscador = new Buscador(modelo);
  cerrarPanel(false);
  $("empty").hidden = true;
  $("error").hidden = true;
  inicio = inicioGuardado(d);
  explorador.cargar(d, modelo, inicio);
  mostrarArchivos(store.get("archivos") !== "0" || inicio < 0);
  aplicarModo(modo);
  hideLoading();
  document.title = `${d.proyecto.nombre} · Mapeador Python`;
  if (d.advertencias.length) toast(`${d.advertencias.length} advertencias (ver barra inferior)`);
  const q = $<HTMLInputElement>("search-input").value.trim();
  view.consulta = q.toLowerCase();
}

// ------------------------------------------------------------------ inicio del mapa

/**
 * El mapa empieza donde diga el usuario: `?inicio=` en la URL, `--inicio` en la línea de
 * comandos (JSON) o lo último que eligió para este proyecto. Si no hay nada, se le pregunta.
 */
function inicioGuardado(d: Documento): number {
  const qp = new URLSearchParams(location.search).get("inicio");
  if (qp) {
    const id = buscarInicio(d.defs, qp);
    if (id !== null) return id;
    toast(`No se encontró «${qp}» en el proyecto`);
  }
  if (typeof d.inicio === "number" && d.defs[d.inicio]) return d.inicio;
  try {
    const g = JSON.parse(store.get(`inicio:${d.proyecto.nombre}`) || "null") as { tipo: string; qual: string; archivo?: string } | null;
    if (g) {
      const x = d.defs.find(x => x.tipo === g.tipo && x.qual === g.qual && (x.archivo ?? "") === (g.archivo ?? ""));
      if (x) return x.id;
    }
  } catch {
    /* ignorar */
  }
  return -1;
}

function iniciarDesde(defId: number) {
  if (!doc || !doc.defs[defId]) return;
  inicio = defId;
  const d = doc.defs[defId];
  store.set(`inicio:${doc.proyecto.nombre}`, JSON.stringify({ tipo: d.tipo, qual: d.qual, archivo: d.archivo ?? "" }));
  const u = new URL(location.href);
  if (u.searchParams.has("proyecto") || u.searchParams.has("json") || u.searchParams.has("inicio")) {
    u.searchParams.set("inicio", etiquetaInicio(doc.defs, defId).replace(/^todo el proyecto$/, doc.proyecto.nombre));
    history.replaceState(null, "", u);
  }
  explorador.marcar(defId);
  if (window.matchMedia("(max-width: 760px)").matches) mostrarArchivos(false);
  aplicarModo(modo);
}

function actualizarBotonInicio() {
  const b = $<HTMLButtonElement>("btn-inicio");
  b.disabled = !doc;
  b.classList.toggle("vacio", !!doc && inicio < 0);
  b.classList.toggle("on", !$("archivos").hidden);
  const lista = document.querySelector<HTMLElement>('#elegir [data-action="lista"]');
  if (lista) lista.hidden = !$("archivos").hidden;
  $("inicio-nombre").textContent = !doc ? "—" : inicio < 0 ? "elige un archivo…" : etiquetaInicio(doc.defs, inicio);
  b.title = `${doc && inicio >= 0 ? `Inicio: ${doc.defs[inicio].qual}\n` : ""}Clic: mostrar u ocultar la lista de archivos para elegir desde dónde empezar (Ctrl+P para buscar uno)`;
}

function mostrarArchivos(si: boolean) {
  $("archivos").hidden = !si || !doc;
  actualizarBotonInicio();
}

/** Tarjeta «¿desde qué archivo empezamos?» con las sugerencias del analizador. */
function mostrarElegir() {
  const box = $("elegir");
  box.hidden = false;
  const sug = explorador.sugerencias.slice(0, 6);
  $("elegir-sug").hidden = !sug.length;
  const ul = $("elegir-sug-list");
  ul.innerHTML = "";
  for (const s of sug) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "file-item";
    b.title = `${s.entrada.etiqueta}\n${s.entrada.evidencia}`;
    b.innerHTML = `<span>📄</span><span class="name"></span><span class="size"></span>`;
    (b.querySelector(".name") as HTMLElement).textContent = s.archivo;
    (b.querySelector(".size") as HTMLElement).textContent = s.motivo;
    b.addEventListener("click", () => iniciarDesde(s.def));
    li.appendChild(b);
    ul.appendChild(li);
  }
}

/** Abre filas de los primeros niveles para que la vista inicial ya diga algo. */
function abrirNiveles(cola: number[], niveles: number, presupuesto: number) {
  if (!modelo || !arbol) return;
  let total = cola.length;
  for (let nivel = 0; nivel < niveles && cola.length; nivel++) {
    const sig: number[] = [];
    for (const n of cola) {
      const auto = filasAuto(modo, modelo, arbol, n);
      for (const f of modelo.filas(arbol.def[n])) {
        if (!f.destinos.length || !auto.has(f.kind) || f.kind === "referencia") continue;
        if (f.destinos.every(d => arbol!.esAncestroDef(n, d))) continue;
        if (total + f.destinos.length > presupuesto) return;
        arbol.abrir(n, f.key, true);
        for (const c of arbol.hijosDe(n, f)) {
          sig.push(c);
          total++;
        }
      }
    }
    cola = sig;
  }
}

/**
 * Primer despliegue desde el inicio. Para un archivo, en el modo Flujo se abre lo que se
 * ejecuta (main, app web; si no, las funciones sugeridas o el código de nivel de módulo; si no,
 * sus funciones y clases cuando son pocas) y desde ahí se siguen las llamadas.
 */
function abrirInicio(raiz: number) {
  if (!modelo || !arbol || !doc) return;
  const d = modelo.def(arbol.def[raiz]);
  if (modo === "estructura" || d.tipo === "proyecto" || d.tipo === "paquete") {
    abrirNiveles([raiz], d.tipo === "proyecto" || d.tipo === "paquete" ? 2 : 1, 80);
    return;
  }
  if (d.tipo !== "modulo") {
    abrirNiveles([raiz], 2, 60);
    return;
  }
  const filas = modelo.filas(d.id).filter(f => f.destinos.length);
  const sugeridas = new Set(doc.entradas.map(e => e.def));
  let elegidas = filas.filter(f => f.kind === "main" || f.kind === "app");
  if (!elegidas.length) elegidas = filas.filter(f => f.destinos.some(x => sugeridas.has(x)));
  if (!elegidas.length) elegidas = filas.filter(f => f.kind === "nivel" && conFlujo(modelo!.def(f.destinos[0])));
  if (!elegidas.length) {
    const miembros = filas.filter(f => f.kind === "funcion" || f.kind === "clase");
    if (miembros.length <= 8) elegidas = miembros;
    else {
      // archivo grande: sus funciones públicas que nadie del mismo archivo llama (su «puerta»)
      const internas = new Set<number>();
      for (const x of modelo.defs)
        if (x.modulo === d.id && x.id !== d.id) for (const ll of x.llamadas ?? []) for (const t of ll.destinos ?? []) internas.add(t);
      const puertas = miembros.filter(f => f.kind === "funcion" && !modelo!.def(f.destinos[0]).nombre.startsWith("_") && !internas.has(f.destinos[0]));
      if (puertas.length <= 8) elegidas = puertas;
    }
  }
  const hijos: number[] = [];
  for (const f of elegidas) {
    arbol.abrir(raiz, f.key, true);
    hijos.push(...arbol.hijosDe(raiz, f));
  }
  abrirNiveles(hijos, 2, 70);
}

function aplicarModo(m: Modo) {
  modo = m;
  cerrarPanel(false);
  view.modo = m;
  $("modo-flujo").classList.toggle("on", m === "flujo");
  $("modo-estructura").classList.toggle("on", m === "estructura");
  actualizarBotonInicio();
  if (!doc || !modelo) return;
  if (inicio < 0) {
    arbol = null;
    view.setEscena(null);
    mostrarElegir();
    minimap.invalidate();
    updateStatus();
    return;
  }
  $("elegir").hidden = true;
  arbol = new Arbol(modelo);
  const raiz = arbol.nuevo(inicio);
  abrirInicio(raiz);
  view.setEscena({ modelo, arbol }, raiz);
  view.encuadrarInicio(raiz);
  minimap.invalidate();
  updateStatus();
}

$("modo-flujo").addEventListener("click", () => aplicarModo("flujo"));
$("modo-estructura").addEventListener("click", () => aplicarModo("estructura"));
$("btn-inicio").addEventListener("click", () => {
  const abrir = $("archivos").hidden;
  mostrarArchivos(abrir);
  store.set("archivos", abrir ? "1" : "0");
  if (abrir) explorador.marcar(inicio);
});
$("archivos-cerrar").addEventListener("click", () => {
  mostrarArchivos(false);
  store.set("archivos", "0");
});
$("elegir").addEventListener("click", e => {
  const a = (e.target as HTMLElement).closest<HTMLElement>("[data-action]")?.dataset.action;
  if (a === "proyecto") iniciarDesde(0);
  if (a === "lista") {
    mostrarArchivos(true);
    explorador.enfocar();
  }
});

// ancho de la lista de archivos (arrastrando el borde)
{
  const ancho = Number(store.get("archivos-ancho"));
  if (ancho >= 200) document.documentElement.style.setProperty("--arch-w", `${ancho}px`);
  const borde = $("archivos-borde");
  borde.addEventListener("pointerdown", e => {
    e.preventDefault();
    borde.setPointerCapture(e.pointerId);
    borde.classList.add("activo");
    document.body.classList.add("redimensionando");
    const x0 = e.clientX;
    const w0 = $("archivos").getBoundingClientRect().width;
    const mover = (ev: PointerEvent) => {
      const w = Math.max(200, Math.min(window.innerWidth * 0.7, w0 + ev.clientX - x0));
      document.documentElement.style.setProperty("--arch-w", `${Math.round(w)}px`);
    };
    const soltar = () => {
      borde.removeEventListener("pointermove", mover);
      borde.classList.remove("activo");
      document.body.classList.remove("redimensionando");
      store.set("archivos-ancho", String(Math.round($("archivos").getBoundingClientRect().width)));
    };
    borde.addEventListener("pointermove", mover);
    borde.addEventListener("pointerup", soltar, { once: true });
    borde.addEventListener("pointercancel", soltar, { once: true });
  });
}

// ------------------------------------------------------------------ servidor

async function renderListaServidor(ul: HTMLElement, ruta: string, onPick: () => void) {
  ul.innerHTML = '<li class="data-empty">Cargando…</li>';
  const r = await listarProyectos(ruta);
  ul.innerHTML = "";
  if (!r || !r.disponible) {
    ul.innerHTML = '<li class="data-empty">El servidor no tiene carpeta de proyectos (se configura con <code>--proyectos</code>; en Docker, <code>./proyectos</code>).</li>';
    return;
  }
  const add = (label: string, extra: string, onClick: () => void, icon: string, cls = "") => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.className = `file-item ${cls}`;
    b.innerHTML = `<span>${icon}</span><span class="name"></span><span class="size"></span>`;
    (b.querySelector(".name") as HTMLElement).textContent = label;
    (b.querySelector(".size") as HTMLElement).textContent = extra;
    b.addEventListener("click", onClick);
    li.appendChild(b);
    ul.appendChild(li);
  };
  $("servidor-ruta").textContent = ruta ? `/${ruta}` : "";
  if (ruta) {
    const up = ruta.split("/").slice(0, -1).join("/");
    add("..", "", () => renderListaServidor(ul, up, onPick), "↩");
    if (r.py_aqui) add(`Analizar «${ruta.split("/").pop()}»`, `${r.py_aqui} .py aquí`, () => (onPick(), cargarServidor(ruta)), "▶", "accent");
  }
  if (!r.carpetas.length) {
    const li = document.createElement("li");
    li.className = "data-empty";
    li.textContent = "No hay carpetas aquí.";
    ul.appendChild(li);
  }
  for (const c of r.carpetas) {
    const extra = c.py >= 5000 ? "5.000+ .py" : `${fmtInt(c.py)} .py`;
    add(c.nombre, extra, () => (onPick(), cargarServidor(c.ruta)), "📁");
  }
}

async function openServidorDialog() {
  const dlg = $<HTMLDialogElement>("servidor-dialog");
  dlg.showModal();
  await renderListaServidor($("servidor-list"), "", () => dlg.close());
}

async function refreshEmptyServidor() {
  const r = await listarProyectos("");
  const box = $("empty-servidor");
  if (!r || !r.disponible || !r.carpetas.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const ul = $("empty-servidor-list");
  ul.innerHTML = "";
  for (const c of r.carpetas.slice(0, 12)) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "file-item";
    b.innerHTML = `<span>📁</span><span class="name"></span><span class="size"></span>`;
    (b.querySelector(".name") as HTMLElement).textContent = c.nombre;
    (b.querySelector(".size") as HTMLElement).textContent = `${fmtInt(c.py)} .py`;
    b.addEventListener("click", () => cargarServidor(c.ruta));
    li.appendChild(b);
    ul.appendChild(li);
  }
}

// ------------------------------------------------------------------ búsqueda

let resultados: Resultado[] = [];
let resultadoSel = 0;
let searchTimer = 0;
const searchInput = $<HTMLInputElement>("search-input");

function renderResultados() {
  const box = $("search-results");
  if (!resultados.length) {
    box.hidden = !searchInput.value.trim();
    box.innerHTML = searchInput.value.trim() ? '<div class="sr-vacio">Sin coincidencias</div>' : "";
    return;
  }
  box.hidden = false;
  box.innerHTML = resultados
    .map(
      (r, i) =>
        `<button type="button" class="sr${i === resultadoSel ? " on" : ""}" data-i="${i}" title="${esc(r.contexto ? r.contexto + "." + r.titulo : r.titulo)}"><span class="letra">${esc(r.letra)}</span><span class="sr-t"><b>${esc(r.titulo)}</b>${r.contexto ? ` <span class="sr-c">${esc(r.contexto)}</span>` : ""}</span><span class="sr-s">${esc(r.sub)}</span></button>`
    )
    .join("");
  box.querySelector(".sr.on")?.scrollIntoView({ block: "nearest" });
}

let ultimaConsulta = "";

function runSearch(q: string) {
  ultimaConsulta = q;
  view.consulta = q.trim().toLowerCase();
  view.requestDraw();
  if (!buscador || !q.trim()) {
    resultados = [];
    $("search-count").textContent = "";
    renderResultados();
    return;
  }
  resultados = buscador.buscar(q);
  resultadoSel = 0;
  $("search-count").textContent = resultados.length ? `${resultados.length >= 60 ? "60+" : resultados.length}` : "0";
  renderResultados();
}

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => runSearch(searchInput.value), 120);
});
searchInput.addEventListener("focus", () => {
  if (resultados.length) renderResultados();
});
searchInput.addEventListener("keydown", e => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!resultados.length) return;
    resultadoSel = (resultadoSel + (e.key === "ArrowDown" ? 1 : -1) + resultados.length) % resultados.length;
    renderResultados();
  } else if (e.key === "Enter") {
    e.preventDefault();
    clearTimeout(searchTimer);
    if (searchInput.value !== ultimaConsulta) runSearch(searchInput.value);
    if (resultados.length) elegirResultado(resultadoSel);
  } else if (e.key === "Escape") {
    $("search-results").hidden = true;
    searchInput.blur();
  }
});
$("search-results").addEventListener("click", e => {
  const b = (e.target as HTMLElement).closest<HTMLElement>("[data-i]");
  if (b) elegirResultado(Number(b.dataset.i));
});

function elegirResultado(i: number) {
  const r = resultados[i];
  if (!r) return;
  $("search-results").hidden = true;
  irADef(r.def);
}

/**
 * Muestra una definición: si se alcanza desde la raíz de la etapa (o desde la entrada) se
 * abren las filas del camino; si no, se abre como etapa suelta.
 */
function irADef(defId: number) {
  if (!modelo) return;
  if (!arbol) {
    // todavía no hay inicio: lo que se busca pasa a ser el inicio
    iniciarDesde(defId);
    return;
  }
  const kindsTodo = new Set([...KINDS_FLUJO, ...KINDS_ESTRUCTURA, ...KINDS_TIPOS]);
  // en el modo Flujo el inicio puede ser un archivo: primero su contenido y desde ahí las llamadas
  const kindsPrincipales = modo === "flujo" ? new Set([...KINDS_FLUJO, ...KINDS_ESTRUCTURA]) : KINDS_ESTRUCTURA;
  const raices = [...new Set([view.stageRoot, view.stages[0]?.root ?? -1])].filter(x => x >= 0);
  // ¿ya está visible?
  if (view.layout) {
    for (const lista of view.layout.lists)
      for (const n of lista)
        if (arbol.def[n] === defId) {
          view.select(n, -1);
          view.centerOn(n, -1, Math.max(view.zoom, 0.85), true);
          abrirPanel(n, -1);
          return;
        }
  }
  for (const kinds of [kindsPrincipales, kindsTodo]) {
    for (const r of raices) {
      const ruta = camino(modelo, arbol.def[r], defId, kinds);
      if (!ruta) continue;
      let n = r;
      let ok = true;
      for (let i = 1; i < ruta.length; i++) {
        const f = modelo.filas(arbol.def[n]).find(x => kinds.has(x.kind) && x.destinos.includes(ruta[i]));
        if (!f) {
          ok = false;
          break;
        }
        arbol.abrir(n, f.key, true);
        const hijos = arbol.hijosDe(n, f);
        n = hijos[f.destinos.indexOf(ruta[i])];
      }
      if (!ok) continue;
      const cambio = view.navigateTo(n);
      view.select(n, -1);
      view.centerOn(n, -1, Math.max(view.zoom < 0.6 ? 0.9 : view.zoom, 0.85), !cambio);
      abrirPanel(n, -1);
      return;
    }
  }
  // no se alcanza: etapa suelta con la definición como raíz
  const n = arbol.nuevo(defId);
  view.enterStage(n);
  view.select(n, -1);
  abrirPanel(n, -1);
  toast("No se llega desde el inicio: se abre como etapa aparte («Usar como inicio» para empezar desde ahí)");
}

// ------------------------------------------------------------------ panel

let panelN = -1;

async function fuente(archivo: string): Promise<string | null> {
  if (fuentesLocales && archivo in fuentesLocales) return fuentesLocales[archivo];
  if (doc?.fuentes && archivo in doc.fuentes) return doc.fuentes[archivo];
  if (origenServidor) return fuenteServidor(origenServidor, archivo);
  return null;
}

function abrirPanel(n: number, row: number) {
  if (!modelo || !arbol) return;
  panelN = n;
  document.body.classList.add("panel-open");
  $("panel").hidden = false;
  $<HTMLButtonElement>("panel-stage").hidden = n === view.stageRoot;
  $<HTMLButtonElement>("panel-inicio").hidden = arbol.def[n] === inicio;
  renderPanel($("panel-body"), { modelo, arbol, irADef, fuente }, n, row);
  $("panel-body").scrollTop = 0;
}

function cerrarPanel(deselect = true) {
  $("panel").hidden = true;
  document.body.classList.remove("panel-open");
  panelN = -1;
  if (deselect && view.selected !== -1) view.select(-1);
}

$("panel-close").addEventListener("click", () => cerrarPanel());
$("panel").addEventListener("click", e => {
  const t = e.target as HTMLElement;
  const lnk = t.closest<HTMLElement>("[data-def]");
  if (lnk) {
    e.preventDefault();
    irADef(Number(lnk.dataset.def));
    return;
  }
  const b = t.closest<HTMLButtonElement>("button[data-action]");
  if (!b || !modelo || !arbol || panelN < 0) return;
  const accion = b.dataset.action;
  if (accion === "center") view.centerOn(panelN, view.selectedRow, Math.max(view.zoom, 0.8), true);
  if (accion === "copy") copyText(modelo.def(arbol.def[panelN]).qual);
  if (accion === "stage") {
    const n = panelN;
    view.enterStage(n);
    abrirPanel(n, -1);
  }
  if (accion === "puml") abrirPlantuml(panelN);
  if (accion === "inicio") iniciarDesde(arbol.def[panelN]);
});

// ------------------------------------------------------------------ PlantUML

let pumlTab: "secuencia" | "clases" = "secuencia";
let pumlRaiz = -1;

function textoPlantuml(): string {
  if (!modelo || !arbol || !view.layout) return "";
  const visibles = (n: number) => view.isVisible(n);
  if (pumlTab === "secuencia") return plantumlSecuencia(modelo, arbol, pumlRaiz >= 0 ? pumlRaiz : view.stageRoot, visibles);
  const nodos: number[] = [];
  for (const l of view.layout.lists) for (const n of l) nodos.push(n);
  return plantumlClases(modelo, arbol, nodos);
}

function abrirPlantuml(raiz = -1) {
  if (!doc) return;
  pumlRaiz = raiz;
  const dlg = $<HTMLDialogElement>("puml-dialog");
  $<HTMLTextAreaElement>("puml-text").value = textoPlantuml();
  dlg.showModal();
}

document.querySelectorAll<HTMLButtonElement>("#puml-dialog .tab").forEach(t =>
  t.addEventListener("click", () => {
    pumlTab = t.dataset.tab as "secuencia" | "clases";
    document.querySelectorAll("#puml-dialog .tab").forEach(x => x.classList.toggle("on", x === t));
    $<HTMLTextAreaElement>("puml-text").value = textoPlantuml();
  })
);
$("puml-copy").addEventListener("click", () => copyText($<HTMLTextAreaElement>("puml-text").value));
$("puml-download").addEventListener("click", () => {
  const nombre = `${doc?.proyecto.nombre ?? "proyecto"}-${pumlTab}.puml`;
  descargar(nombre, new Blob([$<HTMLTextAreaElement>("puml-text").value], { type: "text/plain" }));
});
$("btn-puml").addEventListener("click", () => abrirPlantuml(-1));

// ------------------------------------------------------------------ barra y controles

const inputCarpeta = $<HTMLInputElement>("input-carpeta");
inputCarpeta.addEventListener("change", () => {
  const files = inputCarpeta.files;
  if (files && files.length) cargarSeleccion(desdeInput(files));
  inputCarpeta.value = "";
});
const inputJson = $<HTMLInputElement>("input-json");
inputJson.addEventListener("change", async () => {
  const f = inputJson.files?.[0];
  if (f) {
    showLoading("Leyendo JSON", null, f.name);
    const t0 = performance.now();
    const texto = await f.text();
    tiempoCarga = performance.now() - t0;
    limpiarUrl("proyecto", "json", "inicio");
    await cargarJson(texto, f.name);
  }
  inputJson.value = "";
});

$("btn-carpeta").addEventListener("click", () => inputCarpeta.click());
$("btn-json").addEventListener("click", () => inputJson.click());
$("btn-servidor").addEventListener("click", openServidorDialog);
$("empty").addEventListener("click", e => {
  const a = (e.target as HTMLElement).closest<HTMLElement>("[data-action]")?.dataset.action;
  if (a === "carpeta") inputCarpeta.click();
  if (a === "json") inputJson.click();
  if (a === "ejemplo") {
    limpiarUrl("proyecto", "json", "inicio");
    cargarUrlJson("ejemplo.mapa.json");
  }
});
$("error-close").addEventListener("click", () => ($("error").hidden = true));
$("st-adv").addEventListener("click", () => {
  if (doc?.advertencias.length) showError(doc.advertencias.join("\n"), "Advertencias del análisis");
});

$("btn-expand").addEventListener("click", () => view.expandStage());
$("btn-collapse").addEventListener("click", () => view.collapseStage());
$("btn-dir").addEventListener("click", () => applyDirection(view.dir === "RIGHT" ? "DOWN" : "RIGHT"));
$("btn-theme").addEventListener("click", () => applyTheme(TEMAS[(TEMAS.indexOf(view.themeMode) + 1) % TEMAS.length]));
$("btn-grid").addEventListener("click", () => {
  const on = !view.showGrid;
  view.setShowGrid(on);
  $("btn-grid").classList.toggle("on", on);
  store.set("grid", on ? "1" : "0");
});
$("zoom-in").addEventListener("click", () => view.zoomAt(1.25));
$("zoom-out").addEventListener("click", () => view.zoomAt(0.8));
$("zoom-label").addEventListener("click", () => view.setZoom(1));
$("fit").addEventListener("click", () => view.fitView());
$("root").addEventListener("click", () => view.focusRoot());
$("wheel-mode").addEventListener("click", () => applyWheel(view.wheelMode === "zoom" ? "pan" : "zoom"));
$("toggle-minimap").addEventListener("click", () => {
  const mm = $("minimap");
  mm.hidden = !mm.hidden;
  $("toggle-minimap").classList.toggle("on", !mm.hidden);
  store.set("minimap", mm.hidden ? "0" : "1");
  if (!mm.hidden) minimap.invalidate();
});
$("export-png").addEventListener("click", () => {
  if (!doc) return;
  view.renderToCanvas().toBlob(b => b && descargar(`${doc!.proyecto.nombre}-vista.png`, b), "image/png");
});

// ------------------------------------------------------------------ arrastrar y soltar

let dragDepth = 0;
window.addEventListener("dragenter", e => {
  if (!e.dataTransfer?.types.includes("Files")) return;
  dragDepth++;
  $("drop").hidden = false;
});
window.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) $("drop").hidden = true;
});
window.addEventListener("dragover", e => e.preventDefault());
window.addEventListener("drop", async e => {
  e.preventDefault();
  dragDepth = 0;
  $("drop").hidden = true;
  if (!e.dataTransfer) return;
  showLoading("Buscando archivos .py", null);
  try {
    const r = await desdeArrastre(e.dataTransfer, n => showLoading("Buscando archivos .py", null, `${fmtInt(n)} archivos revisados`));
    if (!r) {
      showError("Suelta una carpeta con código Python o un JSON generado por el mapeador.", "Nada que abrir");
      return;
    }
    if ("json" in r) {
      showLoading("Leyendo JSON", null, r.json.name);
      limpiarUrl("proyecto", "json", "inicio");
      await cargarJson(await r.json.text(), r.json.name);
      return;
    }
    await cargarSeleccion(r);
  } catch (err) {
    showError(`No se pudo leer lo que soltaste: ${(err as Error).message}`);
  }
});

// ------------------------------------------------------------------ teclado

window.addEventListener("keydown", e => {
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === "f") {
    e.preventDefault();
    searchInput.focus();
    searchInput.select();
    return;
  }
  if (mod && e.key.toLowerCase() === "p" && doc) {
    e.preventDefault();
    mostrarArchivos(true);
    explorador.enfocar();
    return;
  }
  if (mod && e.key.toLowerCase() === "o") {
    e.preventDefault();
    inputCarpeta.click();
    return;
  }
  const tag = (e.target as HTMLElement).tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if (document.querySelector("dialog[open]")) return;
  if (e.key === "Escape") {
    if (!$("panel").hidden) cerrarPanel();
    else if (!settingsPop.hidden) settingsPop.hidden = true;
  } else if (e.key === "Backspace" || (e.altKey && e.key === "ArrowLeft")) view.goBack();
  else if (e.key === "[") view.switchStageRoot(view.stageSibling(-1));
  else if (e.key === "]") view.switchStageRoot(view.stageSibling(1));
  else if (e.key === "f" || e.key === "F") view.fitView();
  else if (e.key === "r" || e.key === "R") view.focusRoot();
  else if (e.key === "e" || e.key === "E") view.expandStage();
  else if (e.key === "+" || e.key === "=") view.zoomAt(1.25);
  else if (e.key === "-" || e.key === "_") view.zoomAt(0.8);
  else if (e.key === "0") view.setZoom(1);
  else if (e.key === "ArrowLeft") view.panBy(80, 0);
  else if (e.key === "ArrowRight") view.panBy(-80, 0);
  else if (e.key === "ArrowUp") view.panBy(0, 80);
  else if (e.key === "ArrowDown") view.panBy(0, -80);
  else return;
  e.preventDefault();
});

// ------------------------------------------------------------------ arranque

const params = new URLSearchParams(location.search);
if (params.get("proyecto")) cargarServidor(params.get("proyecto")!);
else if (params.get("json")) cargarUrlJson(params.get("json")!);
else if (params.has("ejemplo")) cargarUrlJson("ejemplo.mapa.json");
refreshEmptyServidor();
updateStatus();
renderCrumbs();

// expuesto para depuración y pruebas automatizadas
(window as unknown as { __mpy: unknown }).__mpy = {
  view,
  get doc() {
    return doc;
  },
  get modelo() {
    return modelo;
  },
  get arbol() {
    return arbol;
  },
  irADef,
  iniciarDesde,
  get inicio() {
    return inicio;
  },
  cargarJson,
  cargarServidor,
};
