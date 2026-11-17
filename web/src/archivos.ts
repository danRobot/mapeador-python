import type { Modelo } from "./engine/model";
import type { Def, Documento, Entrada } from "./tipos";

/**
 * Lista de archivos del proyecto (barra lateral): aquí se elige desde qué archivo, o desde
 * qué función o clase de un archivo, empieza el mapa. Las entradas que detecta el analizador
 * (main, app web, scripts...) solo se muestran como sugerencias: nada arranca solo.
 */

export interface Sugerencia {
  /** Módulo (archivo) sugerido. */
  def: number;
  archivo: string;
  motivo: string;
  entrada: Entrada;
}

interface Carpeta {
  k: "c";
  nombre: string;
  ruta: string;
  hijos: Nodo[];
  archivos: number;
}

interface Archivo {
  k: "a";
  nombre: string;
  ruta: string;
  def: number;
}

type Nodo = Carpeta | Archivo;

const CONTENEDORES = new Set(["proyecto", "paquete", "modulo"]);
const MIEMBROS = new Set(["clase", "funcion", "main", "nivel", "app"]);

const INSIGNIA: Record<string, string> = {
  app: "web",
  main: "main",
  __main__: "main",
  funcion: "main()",
  declarado: "script",
  cli: "cli",
  serverless: "lambda",
  tarea: "tarea",
  script: "script",
  raiz: "raíz",
};

const esc = (s: unknown) =>
  String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

const ICONO_CARPETA = '<svg viewBox="0 0 24 24" class="ar-ico"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const ICONO_ARCHIVO = '<svg viewBox="0 0 24 24" class="ar-ico"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>';
const ICONO_PROYECTO = '<svg viewBox="0 0 24 24" class="ar-ico"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>';

/** ¿El bloque tiene llamadas que lleven a algo? (para no listar «nivel de módulo» vacíos). */
export function conFlujo(d: Def): boolean {
  return (d.llamadas ?? []).some(ll => ll.especie === "externo" || (!!ll.destinos?.length && ["llamada", "crea", "encola", "ruta"].includes(ll.especie)));
}

/** Módulo (archivo) al que pertenece una definición. */
export function moduloDe(defs: Def[], d: Def): Def | null {
  if (d.tipo === "modulo" || d.tipo === "paquete") return d;
  if (d.modulo !== undefined && d.modulo >= 0) return defs[d.modulo];
  for (let x = d; x.padre !== undefined && x.padre >= 0; ) {
    x = defs[x.padre];
    if (x.tipo === "modulo" || x.tipo === "paquete") return x;
  }
  return null;
}

/** Nombre corto del inicio para la barra: archivo, archivo:función o «todo el proyecto». */
export function etiquetaInicio(defs: Def[], id: number): string {
  const d = defs[id];
  if (!d) return "";
  if (d.tipo === "proyecto") return "todo el proyecto";
  if (d.tipo === "paquete") return `${d.qual}/`;
  if (d.tipo === "modulo") return d.archivo ?? d.qual;
  const mod = moduloDe(defs, d);
  const archivo = mod?.archivo ?? d.archivo ?? "";
  let nombre = d.nombre;
  if (d.tipo === "main") nombre = "__main__";
  else if (d.tipo === "nivel") nombre = "(nivel de módulo)";
  else if (d.tipo === "app") nombre = `app ${d.nombre}`;
  else if (d.tipo === "metodo" && d.clase !== undefined && d.clase >= 0) nombre = `${defs[d.clase].nombre}.${d.nombre}`;
  return archivo ? `${archivo}:${nombre}` : d.qual;
}

/**
 * Busca la definición desde la que empezar (para `?inicio=` en la URL). Acepta la ruta de un
 * archivo (`api/main.py`, o solo su final si no es ambiguo), archivo y función
 * (`api/routes.py:predict`, `x.py:Clase.metodo`), carpeta de un paquete o un nombre completo.
 */
export function buscarInicio(defs: Def[], texto: string): number | null {
  let t = texto.trim().replace(/\\/g, "/");
  while (t.startsWith("./")) t = t.slice(2);
  if (!t) return null;
  let archivo = t;
  let nombre = "";
  const m = /^(.+\.pyw?):(.+)$/.exec(t);
  if (m) [archivo, nombre] = [m[1], m[2]];
  const modulos = defs.filter(d => (d.tipo === "modulo" || d.tipo === "paquete") && d.archivo);
  if (/\.pyw?$/.test(archivo)) {
    const exactos = modulos.filter(d => d.archivo === archivo);
    const candidatos = exactos.length ? exactos : modulos.filter(d => d.archivo!.endsWith("/" + archivo)).sort((a, b) => a.archivo!.length - b.archivo!.length);
    const mod = candidatos[0];
    if (!mod) return null;
    if (!nombre) return mod.id;
    const dentro = defs.filter(d => d.archivo === mod.archivo && ["funcion", "metodo", "clase", "app"].includes(d.tipo));
    const q = dentro.find(d => d.qual === `${mod.qual}.${nombre}` || d.qual.endsWith("." + nombre));
    if (q) return q.id;
    const unico = dentro.filter(d => d.nombre === nombre);
    return unico.length === 1 ? unico[0].id : null;
  }
  const carpeta = t.replace(/\/+$/, "");
  const paq = modulos.find(d => d.tipo === "paquete" && (d.archivo === `${carpeta}/__init__.py` || d.archivo === `${carpeta}/__init__.pyw`));
  if (paq) return paq.id;
  const q = defs.find(d => d.qual === t && d.tipo !== "main" && d.tipo !== "nivel");
  if (q) return q.id;
  const unico = defs.filter(d => d.nombre === t && (d.tipo === "funcion" || d.tipo === "clase" || d.tipo === "metodo"));
  return unico.length === 1 ? unico[0].id : null;
}

function motivo(e: Entrada, d: Def, archivo: string): string {
  switch (e.tipo) {
    case "main":
      return 'if __name__ == "__main__"';
    case "funcion":
      return `función ${d.nombre}()`;
    case "script":
      return "script (código de nivel de módulo)";
    default: {
      const partes = e.etiqueta.split(/\s+·\s+/).filter(p => p !== archivo && p !== d.archivo);
      return partes.join(" · ") || e.tipo;
    }
  }
}

export interface OpcionesExplorador {
  lista: HTMLElement;
  filtro: HTMLInputElement;
  contador?: HTMLElement;
  onElegir(def: number): void;
}

export class Explorador {
  private doc: Documento | null = null;
  private modelo: Modelo | null = null;
  private raiz: Carpeta | null = null;
  /** Carpetas ("c:ruta") y archivos ("a:ruta") desplegados. */
  private abiertos = new Set<string>();
  private entradaDe = new Map<number, Entrada>();
  private actual = -1;
  private consulta = "";
  private resultados: number[] = [];
  private resaltado = 0;
  private verSugeridos = true;
  sugerencias: Sugerencia[] = [];

  constructor(private o: OpcionesExplorador) {
    o.lista.addEventListener("click", e => this.clic(e));
    o.filtro.addEventListener("input", () => {
      this.consulta = o.filtro.value.trim().toLowerCase();
      this.resaltado = 0;
      this.render();
    });
    o.filtro.addEventListener("keydown", e => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const n = this.resultados.length;
        if (!n) return;
        this.resaltado = (this.resaltado + (e.key === "ArrowDown" ? 1 : -1) + n) % n;
        this.render();
      } else if (e.key === "Enter") {
        e.preventDefault();
        const def = this.resultados[this.resaltado];
        if (def !== undefined) this.o.onElegir(def);
      } else if (e.key === "Escape") {
        if (o.filtro.value) {
          e.stopPropagation();
          o.filtro.value = "";
          this.consulta = "";
          this.render();
        } else o.filtro.blur();
      }
    });
  }

  /** Carga el proyecto; `inicio` es lo que ya está elegido (-1 si nada). */
  cargar(doc: Documento, modelo: Modelo, inicio = -1) {
    this.doc = doc;
    this.modelo = modelo;
    this.actual = -1;
    this.verSugeridos = inicio < 0;
    this.abiertos.clear();
    this.entradaDe.clear();
    this.consulta = "";
    this.o.filtro.value = "";
    const defs = doc.defs;
    // mejor entrada de cada archivo (las entradas vienen ordenadas por puntaje)
    for (const e of doc.entradas) {
      const d = defs[e.def];
      const mod = d && moduloDe(defs, d);
      if (mod && !this.entradaDe.has(mod.id)) this.entradaDe.set(mod.id, e);
    }
    this.sugerencias = [...this.entradaDe.entries()].map(([id, e]) => {
      const archivo = defs[id].archivo ?? defs[id].qual;
      return { def: id, archivo, motivo: motivo(e, defs[e.def], archivo), entrada: e };
    });
    // árbol de carpetas a partir de las rutas
    const raiz: Carpeta = { k: "c", nombre: doc.proyecto.nombre, ruta: "", hijos: [], archivos: 0 };
    const carpetas = new Map<string, Carpeta>([["", raiz]]);
    const carpeta = (ruta: string): Carpeta => {
      let c = carpetas.get(ruta);
      if (c) return c;
      const i = ruta.lastIndexOf("/");
      const padre = carpeta(i < 0 ? "" : ruta.slice(0, i));
      c = { k: "c", nombre: ruta.slice(i + 1), ruta, hijos: [], archivos: 0 };
      padre.hijos.push(c);
      carpetas.set(ruta, c);
      return c;
    };
    let total = 0;
    for (const d of defs) {
      if ((d.tipo !== "modulo" && d.tipo !== "paquete") || !d.archivo) continue;
      const i = d.archivo.lastIndexOf("/");
      const c = carpeta(i < 0 ? "" : d.archivo.slice(0, i));
      c.hijos.push({ k: "a", nombre: d.archivo.slice(i + 1), ruta: d.archivo, def: d.id });
      total++;
    }
    const ordenar = (c: Carpeta): number => {
      c.hijos.sort((a, b) => (a.k === b.k ? (a.nombre.toLowerCase() < b.nombre.toLowerCase() ? -1 : 1) : a.k === "c" ? -1 : 1));
      c.archivos = c.hijos.reduce((n, h) => n + (h.k === "c" ? ordenar(h) : 1), 0);
      return c.archivos;
    };
    ordenar(raiz);
    this.raiz = raiz;
    // carpetas abiertas de entrada: las de primer nivel si el proyecto no es enorme
    const abrir = (c: Carpeta, prof: number) => {
      for (const h of c.hijos)
        if (h.k === "c" && (prof === 0 ? total <= 400 : total <= 60)) {
          this.abiertos.add(`c:${h.ruta}`);
          abrir(h, prof + 1);
        }
    };
    abrir(raiz, 0);
    if (this.o.contador) this.o.contador.textContent = `${total.toLocaleString("es-CO")} archivo${total === 1 ? "" : "s"}`;
    this.marcar(inicio);
  }

  /** Marca el inicio actual y despliega las carpetas hasta él. */
  marcar(def: number, revelar = true) {
    this.actual = def;
    const doc = this.doc;
    if (doc && def >= 0 && revelar) {
      const d = doc.defs[def];
      const mod = d ? moduloDe(doc.defs, d) : null;
      if (mod?.archivo) {
        const partes = mod.archivo.split("/");
        for (let i = 1; i < partes.length; i++) this.abiertos.add(`c:${partes.slice(0, i).join("/")}`);
        if (mod.id !== def) this.abiertos.add(`a:${mod.archivo}`);
      }
    }
    this.render();
    if (revelar) this.o.lista.querySelector(".ar-fila.on")?.scrollIntoView({ block: "nearest" });
  }

  enfocar() {
    this.o.filtro.focus();
    this.o.filtro.select();
  }

  // ---------------------------------------------------------------- dibujo

  private render() {
    const lista = this.o.lista;
    if (!this.doc || !this.raiz) {
      lista.innerHTML = "";
      return;
    }
    if (this.consulta) {
      lista.innerHTML = this.renderFiltro();
      lista.querySelector(".ar-fila.kb")?.scrollIntoView({ block: "nearest" });
      return;
    }
    this.resultados = [];
    const h: string[] = [];
    if (this.sugerencias.length) {
      h.push(
        `<button type="button" class="ar-seccion" data-seccion="sug" title="Archivos con main, apps web, scripts...: solo son sugerencias"><span class="ar-chev">${this.verSugeridos ? "▾" : "▸"}</span>Sugeridos <small>${this.sugerencias.length} detectados automáticamente</small></button>`
      );
      if (this.verSugeridos)
        for (const s of this.sugerencias.slice(0, 6)) {
          const on = s.def === this.actual ? " on" : "";
          const nombre = s.archivo.slice(s.archivo.lastIndexOf("/") + 1);
          h.push(
            `<button type="button" class="ar-fila ar-sug${on}" data-def="${s.def}" title="${esc(`${s.archivo}\n${s.entrada.etiqueta}\n${s.entrada.evidencia}`)}"><span class="ar-ins ins-${esc(s.entrada.tipo.replace(/_/g, ""))}">${esc(INSIGNIA[s.entrada.tipo] ?? s.entrada.tipo)}</span><span class="ar-txt"><b>${esc(nombre)}</b> <span class="ar-dim">${esc(s.motivo)}</span></span></button>`
          );
        }
      h.push(`<div class="ar-seccion ar-seccion-fija">Archivos</div>`);
    }
    const on0 = this.actual === 0 ? " on" : "";
    h.push(
      `<button type="button" class="ar-fila ar-proyecto${on0}" data-def="0" title="Empezar por la estructura de todo el proyecto (paquetes y módulos)" style="--p:0">${ICONO_PROYECTO}<span class="ar-txt"><b>${esc(this.raiz.nombre)}</b> <span class="ar-dim">todo el proyecto</span></span></button>`
    );
    this.renderCarpeta(this.raiz, 1, h);
    lista.innerHTML = h.join("");
  }

  private renderCarpeta(c: Carpeta, prof: number, h: string[]) {
    const defs = this.doc!.defs;
    for (const n of c.hijos) {
      if (n.k === "c") {
        const abierta = this.abiertos.has(`c:${n.ruta}`);
        h.push(
          `<button type="button" class="ar-fila ar-carpeta" data-carpeta="${esc(n.ruta)}" style="--p:${prof}" title="${esc(n.ruta)}"><span class="ar-chev">${abierta ? "▾" : "▸"}</span>${ICONO_CARPETA}<span class="ar-txt">${esc(n.nombre)}</span><span class="ar-num">${n.archivos}</span></button>`
        );
        if (abierta) this.renderCarpeta(n, prof + 1, h);
        continue;
      }
      const d = defs[n.def];
      const miembros = this.miembros(d);
      const abierto = this.abiertos.has(`a:${n.ruta}`);
      const actual = this.actual >= 0 ? defs[this.actual] : null;
      const on = this.actual === n.def ? " on" : actual && this.actual !== n.def && moduloDe(defs, actual)?.id === n.def ? " contiene" : "";
      const e = this.entradaDe.get(n.def);
      const ins = e ? `<span class="ar-ins ins-${esc(e.tipo.replace(/_/g, ""))}" title="${esc(e.etiqueta)}">${esc(INSIGNIA[e.tipo] ?? e.tipo)}</span>` : "";
      const nClases = (d.hijos ?? []).filter(x => defs[x].tipo === "clase").length;
      const nFunciones = (d.hijos ?? []).filter(x => defs[x].tipo === "funcion").length;
      const titulo = `${n.ruta} · ${d.lineas ?? 0} líneas · ${nFunciones} funciones · ${nClases} clases${e ? `\nsugerido: ${e.etiqueta}` : ""}\nClic: empezar el mapa desde este archivo`;
      const chev = miembros.length ? `<span class="ar-chev" data-toggle="a:${esc(n.ruta)}" title="Ver funciones y clases">${abierto ? "▾" : "▸"}</span>` : `<span class="ar-chev"></span>`;
      h.push(
        `<button type="button" class="ar-fila ar-archivo${on}${d.error ? " err" : ""}" data-def="${n.def}" style="--p:${prof}" title="${esc(titulo)}">${chev}${ICONO_ARCHIVO}<span class="ar-txt">${esc(n.nombre)}</span>${ins}</button>`
      );
      if (abierto) for (const m of miembros) h.push(this.filaMiembro(m, prof + 1));
    }
  }

  private miembros(d: Def): Def[] {
    const defs = this.doc!.defs;
    return (d.hijos ?? [])
      .map(x => defs[x])
      .filter(x => MIEMBROS.has(x.tipo) && (x.tipo !== "nivel" || conFlujo(x)))
      .sort((a, b) => (a.linea ?? 0) - (b.linea ?? 0));
  }

  private filaMiembro(m: Def, prof: number, ruta = ""): string {
    const cab = this.modelo!.cabecera(m.id);
    const actual = this.actual >= 0 ? this.doc!.defs[this.actual] : null;
    const on = this.actual === m.id ? " on" : actual && (actual.clase === m.id || actual.padre === m.id) ? " contiene" : "";
    const kb = this.consulta && this.resultados[this.resaltado] === m.id ? " kb" : "";
    let nombre = m.nombre;
    let extra = "";
    if (m.tipo === "main") nombre = 'if __name__ == "__main__"';
    else if (m.tipo === "nivel") nombre = "nivel de módulo";
    else if (m.tipo === "app") (nombre = `${cab.titulo}`), (extra = cab.sub.split(" · ")[0] ?? "");
    else if (m.tipo === "funcion" || m.tipo === "metodo") extra = m.firma ?? "()";
    if (m.tipo === "metodo" && m.clase !== undefined && m.clase >= 0) nombre = `${this.doc!.defs[m.clase].nombre}.${m.nombre}`;
    if (ruta) extra = ruta;
    return `<button type="button" class="ar-fila ar-miembro${on}${kb}" data-def="${m.id}" style="--p:${prof}" title="${esc(`${m.qual}${m.firma ?? ""}\nClic: empezar el mapa desde aquí`)}"><span class="letra est-${esc(cab.estereo)}">${esc(cab.letra)}</span><span class="ar-txt">${esc(nombre)} <span class="ar-dim">${esc(extra)}</span></span></button>`;
  }

  private renderFiltro(): string {
    const q = this.consulta;
    const defs = this.doc!.defs;
    const archivos: [number, Def][] = [];
    const otros: [number, Def][] = [];
    for (const d of defs) {
      if ((d.tipo === "modulo" || d.tipo === "paquete") && d.archivo) {
        const ruta = d.archivo.toLowerCase();
        const nombre = ruta.slice(ruta.lastIndexOf("/") + 1);
        let p = -1;
        if (nombre.startsWith(q)) p = 0;
        else if (nombre.includes(q)) p = 1;
        else if (ruta.includes(q) || d.qual.toLowerCase().includes(q)) p = 2;
        if (p >= 0) archivos.push([p * 1000 + ruta.length, d]);
      } else if (d.tipo === "funcion" || d.tipo === "clase" || d.tipo === "metodo" || d.tipo === "app") {
        const n = d.nombre.toLowerCase();
        let p = -1;
        if (n === q) p = 0;
        else if (n.startsWith(q)) p = 1;
        else if (n.includes(q)) p = 2;
        if (p >= 0) otros.push([p * 10 + (d.tipo === "metodo" ? 1 : 0) + (d.nombre.startsWith("_") ? 2 : 0), d]);
      }
    }
    archivos.sort((a, b) => a[0] - b[0]);
    otros.sort((a, b) => a[0] - b[0] || a[1].nombre.localeCompare(b[1].nombre));
    const arch = archivos.slice(0, 80).map(x => x[1]);
    const fun = otros.slice(0, 60).map(x => x[1]);
    this.resultados = [...arch.map(d => d.id), ...fun.map(d => d.id)];
    if (this.resaltado >= this.resultados.length) this.resaltado = 0;
    if (!this.resultados.length) return `<div class="ar-vacio">Nada coincide con «${esc(this.o.filtro.value)}»</div>`;
    const h: string[] = [];
    if (arch.length) h.push(`<div class="ar-seccion ar-seccion-fija">Archivos <small>${archivos.length}</small></div>`);
    for (const d of arch) {
      const ruta = d.archivo!;
      const i = ruta.lastIndexOf("/");
      const on = this.actual === d.id ? " on" : "";
      const kb = this.resultados[this.resaltado] === d.id ? " kb" : "";
      const e = this.entradaDe.get(d.id);
      const ins = e ? `<span class="ar-ins ins-${esc(e.tipo.replace(/_/g, ""))}">${esc(INSIGNIA[e.tipo] ?? e.tipo)}</span>` : "";
      h.push(
        `<button type="button" class="ar-fila ar-archivo${on}${kb}" data-def="${d.id}" style="--p:0" title="${esc(ruta)}"><span class="ar-chev"></span>${ICONO_ARCHIVO}<span class="ar-txt">${esc(ruta.slice(i + 1))} <span class="ar-dim">${esc(i > 0 ? ruta.slice(0, i) : "")}</span></span>${ins}</button>`
      );
    }
    if (fun.length) h.push(`<div class="ar-seccion ar-seccion-fija">Funciones y clases <small>${otros.length}</small></div>`);
    for (const d of fun) {
      const mod = moduloDe(defs, d);
      h.push(this.filaMiembro(d, 0, mod?.archivo ?? ""));
    }
    return h.join("");
  }

  // ---------------------------------------------------------------- eventos

  private clic(e: MouseEvent) {
    const t = e.target as HTMLElement;
    const sec = t.closest<HTMLElement>("[data-seccion]");
    if (sec) {
      this.verSugeridos = !this.verSugeridos;
      this.render();
      return;
    }
    const tog = t.closest<HTMLElement>("[data-toggle]");
    if (tog) {
      e.stopPropagation();
      const k = tog.dataset.toggle!;
      if (this.abiertos.has(k)) this.abiertos.delete(k);
      else this.abiertos.add(k);
      this.render();
      return;
    }
    const car = t.closest<HTMLElement>("[data-carpeta]");
    if (car) {
      const k = `c:${car.dataset.carpeta}`;
      if (this.abiertos.has(k)) this.abiertos.delete(k);
      else this.abiertos.add(k);
      this.render();
      return;
    }
    const fila = t.closest<HTMLElement>("[data-def]");
    if (fila) {
      const def = Number(fila.dataset.def);
      const d = this.doc?.defs[def];
      // al elegir un archivo se despliega su contenido para poder afinar el inicio
      if (d && CONTENEDORES.has(d.tipo) && d.archivo && this.miembros(d).length) this.abiertos.add(`a:${d.archivo}`);
      this.o.onElegir(def);
    }
  }
}
