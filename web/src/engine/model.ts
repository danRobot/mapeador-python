import type { Atributo, Def, Documento, Llamada, Param } from "../tipos";

/**
 * Modelo de dibujo.
 *
 * Cada definición del proyecto (función, método, clase, módulo, app...) se dibuja como un
 * nodo estilo PlantUML: una cabecera con el estereotipo y filas en compartimentos
 * (parámetros · retorno · llamadas, o bases · atributos · métodos). Igual que en jsoncrack,
 * las filas que llevan a otras definiciones tienen un botón "+" y de ellas sale la arista.
 *
 * El grafo del proyecto tiene ciclos y nodos compartidos, así que el árbol que se dibuja
 * se construye a demanda (`Arbol`): un nodo es una aparición de una definición en el
 * camino desde la entrada; sus hijos se crean la primera vez que se abre la fila.
 */

export type FilaKind =
  | "param"
  | "retorno"
  | "base"
  | "atributo"
  | "enum"
  | "metodo"
  | "clase"
  | "subclase"
  | "submodulo"
  | "funcion"
  | "main"
  | "nivel"
  | "app"
  | "ruta"
  | "llamada"
  | "crea"
  | "encola"
  | "referencia"
  | "lanza"
  | "externo"
  | "libreria"
  | "noresuelta"
  | "aviso";

export type Arista = "llamada" | "crea" | "encola" | "referencia" | "tipo" | "herencia" | "subclase" | "miembro" | "ruta" | "lanza";

export type ColorKey =
  | "key"
  | "value"
  | "tipo"
  | "tipoProyecto"
  | "muted"
  | "linea"
  | "crea"
  | "cola"
  | "ref"
  | "lanza"
  | "verbo"
  | "str"
  | "num"
  | `ext:${string}`;

export interface Fila {
  /** Clave estable dentro de la definición (no cambia al filtrar). */
  key: string;
  kind: FilaKind;
  /** Compartimento: entre compartimentos se dibuja una línea más marcada. */
  seccion: number;
  izq: string;
  der: string;
  cIzq: ColorKey;
  cDer: ColorKey;
  cursiva?: boolean;
  destinos: number[];
  arista: Arista;
  marca?: string;
  linea?: number;
  llamada?: Llamada;
  param?: Param;
  atributo?: Atributo;
}

export interface Filtros {
  /** Llamadas a librerías y funciones integradas (print, json.dumps, logger.info...). */
  libreria: boolean;
  noResueltas: boolean;
  privados: boolean;
  excepciones: boolean;
  referencias: boolean;
  /** Parámetros y tipos en los nodos de función. */
  parametros: boolean;
}

export const FILTROS_DEFECTO: Filtros = {
  libreria: false,
  noResueltas: true,
  privados: true,
  excepciones: true,
  referencias: true,
  parametros: true,
};

export interface Cabecera {
  letra: string;
  estereo: string;
  titulo: string;
  /** Parte del título que se atenúa (Clase. en Clase.metodo). */
  prefijo: string;
  sub: string;
}

export const HEADER_H = 42;
export const ROW_H = 26;
export const PAD_X = 10;
export const COLLAPSE_W = 22;
export const MAX_NODE_W = 620;
export const MIN_NODE_W = 170;
export const MAX_DER = 72;

const PY_VISIBLES = new Set(["__init__", "__call__", "__enter__", "__exit__", "__iter__", "__next__", "__getitem__"]);

const corto = (q: string) => q.slice(q.lastIndexOf(".") + 1);

export class Modelo {
  readonly defs: Def[];
  filtros: Filtros = { ...FILTROS_DEFECTO };
  private cacheFilas = new Map<number, Fila[]>();
  private cacheIndice = new Map<number, Map<string, number>>();
  private cacheTam = new Map<number, [number, number]>();
  private cacheCab = new Map<number, Cabecera>();
  private _llamadores: Map<number, { def: number; linea: number; texto: string; especie: string }[]> | null = null;

  constructor(
    readonly doc: Documento,
    readonly charW: number
  ) {
    this.defs = doc.defs;
  }

  setFiltros(f: Filtros) {
    this.filtros = { ...f };
    this.cacheFilas.clear();
    this.cacheIndice.clear();
    this.cacheTam.clear();
  }

  def(id: number): Def {
    return this.defs[id];
  }

  // ------------------------------------------------------------------ cabecera

  cabecera(id: number): Cabecera {
    let c = this.cacheCab.get(id);
    if (c) return c;
    const d = this.defs[id];
    const m = new Set(d.marcas ?? []);
    let letra = "F";
    let estereo = "funcion";
    let titulo = d.nombre;
    let prefijo = "";
    const subs: string[] = [];
    switch (d.tipo) {
      case "proyecto":
        letra = "P";
        estereo = "proyecto";
        subs.push("proyecto");
        break;
      case "paquete":
        letra = "P";
        estereo = "paquete";
        titulo = d.qual;
        subs.push("paquete");
        break;
      case "modulo":
        letra = "M";
        estereo = "modulo";
        titulo = d.qual;
        subs.push("módulo");
        break;
      case "clase":
        letra = "C";
        estereo = "clase";
        if (m.has("enum")) {
          letra = "E";
          estereo = "enum";
        } else if (m.has("protocolo")) {
          letra = "I";
          estereo = "protocolo";
        } else if (m.has("abstracta")) {
          letra = "A";
          estereo = "abstracta";
        } else if (m.has("excepcion")) {
          letra = "X";
          estereo = "excepcion";
        } else if (m.has("dataclass") || m.has("pydantic") || m.has("namedtuple") || m.has("typeddict")) {
          estereo = "datos";
        }
        if (d.clase !== undefined && d.clase >= 0) prefijo = this.defs[d.clase].nombre + ".";
        for (const s of ["dataclass", "pydantic", "enum", "protocolo", "abstracta", "excepcion", "modelo-orm", "namedtuple", "typeddict"])
          if (m.has(s)) subs.push(`«${s}»`);
        break;
      case "metodo":
        letra = "m";
        estereo = "metodo";
        if (d.clase !== undefined && d.clase >= 0) prefijo = this.defs[d.clase].nombre + ".";
        break;
      case "funcion":
        letra = "F";
        estereo = "funcion";
        if (m.has("anidada") && d.padre !== undefined && d.padre >= 0) prefijo = this.defs[d.padre].nombre + ".";
        break;
      case "main":
        letra = "▶";
        estereo = "main";
        titulo = d.archivo ?? d.qual;
        subs.push('if __name__ == "__main__"');
        break;
      case "nivel":
        letra = "≡";
        estereo = "nivel";
        titulo = d.archivo ?? d.qual;
        subs.push("nivel de módulo");
        break;
      case "app": {
        letra = "W";
        estereo = "app";
        const cl = d.clase_app ? corto(d.clase_app) : "app";
        titulo = `${cl} ${d.nombre}`;
        subs.push(`${d.llamadas?.length ?? 0} rutas`);
        break;
      }
    }
    if (d.tipo === "funcion" || d.tipo === "metodo") {
      for (const [k, t] of [
        ["async", "async"],
        ["static", "{static}"],
        ["classmethod", "{classmethod}"],
        ["property", "{property}"],
        ["abstracta", "{abstract}"],
        ["generador", "generador"],
      ] as const)
        if (m.has(k)) subs.push(t);
      const deco = (d.decoradores ?? []).filter(x => !/^@(staticmethod|classmethod|property|abstractmethod|override)\b/.test(x));
      if (deco.length) subs.push(deco[0].length > 34 ? deco[0].slice(0, 33) + "…" : deco[0]);
    }
    if (m.has("error-sintaxis")) subs.push("⚠ error de sintaxis");
    if (d.archivo && d.tipo !== "main" && d.tipo !== "nivel") subs.push(`${d.archivo}${d.linea && d.tipo !== "modulo" && d.tipo !== "paquete" ? ":" + d.linea : ""}`);
    else if (d.linea && (d.tipo === "main" || d.tipo === "nivel")) subs.push(`línea ${d.linea}`);
    c = { letra, estereo, titulo, prefijo, sub: subs.join(" · ") };
    this.cacheCab.set(id, c);
    return c;
  }

  // ------------------------------------------------------------------ filas

  filas(id: number): Fila[] {
    let f = this.cacheFilas.get(id);
    if (!f) {
      f = this.construirFilas(this.defs[id]);
      this.cacheFilas.set(id, f);
    }
    return f;
  }

  indiceFila(id: number, key: string): number {
    let m = this.cacheIndice.get(id);
    if (!m) {
      m = new Map();
      this.filas(id).forEach((f, i) => m!.set(f.key, i));
      this.cacheIndice.set(id, m);
    }
    return m.get(key) ?? -1;
  }

  private construirFilas(d: Def): Fila[] {
    switch (d.tipo) {
      case "funcion":
      case "metodo":
      case "main":
      case "nivel":
        return this.filasFuncion(d);
      case "clase":
        return this.filasClase(d);
      case "app":
        return this.filasApp(d);
      default:
        return this.filasContenedor(d);
    }
  }

  private filasFuncion(d: Def): Fila[] {
    const r: Fila[] = [];
    const fl = this.filtros;
    if (fl.parametros) {
      for (const p of d.params ?? []) {
        if (p.especie === "self" || p.especie === "cls") continue;
        const pref = p.especie === "*" ? "*" : p.especie === "**" ? "**" : "";
        let der = p.tipo ?? "";
        if (p.defecto) der = der ? `${der} = ${p.defecto}` : `= ${p.defecto}`;
        const refs = (p.refs ?? []).filter(x => x >= 0);
        r.push({
          key: `p:${p.nombre}`,
          kind: "param",
          seccion: 0,
          izq: pref + p.nombre,
          der,
          cIzq: "key",
          cDer: refs.length ? "tipoProyecto" : p.tipo ? "tipo" : "muted",
          cursiva: p.origen === "inferido" || p.origen === "defecto",
          destinos: refs,
          arista: "tipo",
          marca: p.origen === "inferido" ? "inferido" : p.origen === "docstring" ? "doc" : undefined,
          param: p,
        });
      }
      if (d.retorno) {
        const refs = (d.retorno.refs ?? []).filter(x => x >= 0);
        r.push({
          key: "r",
          kind: "retorno",
          seccion: 1,
          izq: "→",
          der: d.retorno.tipo,
          cIzq: "muted",
          cDer: refs.length ? "tipoProyecto" : "tipo",
          cursiva: d.retorno.origen === "inferido",
          destinos: refs,
          arista: "tipo",
          marca: d.retorno.origen === "inferido" ? "inferido" : d.retorno.origen === "docstring" ? "doc" : undefined,
        });
      }
    }
    (d.llamadas ?? []).forEach((ll, i) => {
      const f = this.filaLlamada(ll, i);
      if (f) r.push(f);
    });
    if (d.error) r.push({ key: "err", kind: "aviso", seccion: 2, izq: "⚠", der: d.error, cIzq: "lanza", cDer: "muted", destinos: [], arista: "llamada" });
    return r;
  }

  filaLlamada(ll: Llamada, i: number): Fila | null {
    const fl = this.filtros;
    const base = { key: `c:${i}`, seccion: 2, izq: `${ll.linea}`, cIzq: "linea" as ColorKey, linea: ll.linea, llamada: ll };
    const destinos = (ll.destinos ?? []).filter(x => x >= 0);
    const marcaConf = ll.confianza === "media" ? "~" : ll.confianza === "baja" ? "?" : undefined;
    const multi = destinos.length > 1 ? `×${destinos.length}` : undefined;
    const marca = [multi, marcaConf].filter(Boolean).join(" ") || undefined;
    switch (ll.especie) {
      case "llamada":
        return { ...base, kind: "llamada", der: ll.texto, cDer: "value", destinos, arista: "llamada", marca };
      case "crea":
        return { ...base, kind: "crea", der: ll.texto, cDer: "crea", destinos, arista: "crea", marca: marca ?? "«create»" };
      case "encola":
        return { ...base, kind: "encola", der: `⇉ ${ll.texto}`, cDer: "cola", destinos, arista: "encola", marca: "cola" };
      case "referencia":
        if (!fl.referencias) return null;
        return { ...base, kind: "referencia", der: ll.texto, cDer: "ref", cursiva: true, destinos, arista: "referencia", marca };
      case "lanza":
        if (!fl.excepciones) return null;
        return { ...base, kind: "lanza", der: ll.texto, cDer: "lanza", destinos, arista: "lanza" };
      case "externo": {
        const e = ll.externo!;
        const destino = e.destino ?? ll.texto;
        const der = `${e.tipo}${e.verbo ? " " + e.verbo : ""}  ${destino}`;
        // destinos: si el servicio lo hace una clase propia (regla de [[terminales]]) se puede abrir
        return { ...base, kind: "externo", der, cDer: `ext:${e.tipo}`, destinos, arista: "llamada", marca: "⇢" };
      }
      case "libreria":
        if (!fl.libreria) return null;
        return { ...base, kind: "libreria", der: ll.texto, cDer: "muted", destinos: [], arista: "llamada" };
      case "no-resuelta":
        if (!fl.noResueltas) return null;
        return { ...base, kind: "noresuelta", der: `? ${ll.texto}`, cDer: "muted", cursiva: true, destinos: [], arista: "llamada" };
      case "ruta":
        return {
          key: `c:${i}`,
          seccion: 0,
          kind: "ruta",
          izq: ll.verbo ?? "*",
          der: ll.ruta ?? ll.texto,
          cIzq: "verbo",
          cDer: "value",
          destinos,
          arista: "ruta",
          linea: ll.linea,
          llamada: ll,
        };
    }
    return null;
  }

  private filasClase(d: Def): Fila[] {
    const r: Fila[] = [];
    const fl = this.filtros;
    (d.bases ?? []).forEach((b, i) => {
      const refs = (b.refs ?? []).filter(x => x >= 0);
      r.push({ key: `b:${i}`, kind: "base", seccion: 0, izq: "◁", der: b.tipo, cIzq: "muted", cDer: refs.length ? "tipoProyecto" : "tipo", destinos: refs, arista: "herencia" });
    });
    for (const a of d.atributos ?? []) {
      if (!fl.privados && a.nombre.startsWith("_")) continue;
      if (a.especie === "enum") {
        r.push({ key: `a:${a.nombre}`, kind: "enum", seccion: 1, izq: a.nombre, der: a.tipo ?? "", cIzq: "key", cDer: /^['"]/.test(a.tipo ?? "") ? "str" : "num", destinos: [], arista: "tipo", linea: a.linea, atributo: a });
        continue;
      }
      const refs = (a.refs ?? []).filter(x => x >= 0);
      let der = a.tipo ?? "";
      if (a.defecto && a.especie === "clase") der = der ? `${der} = ${a.defecto}` : `= ${a.defecto}`;
      r.push({
        key: `a:${a.nombre}`,
        kind: "atributo",
        seccion: 1,
        izq: (a.nombre.startsWith("__") ? "-" : a.nombre.startsWith("_") ? "#" : "+") + a.nombre,
        der,
        cIzq: "key",
        cDer: refs.length ? "tipoProyecto" : a.tipo ? "tipo" : "muted",
        cursiva: a.origen === "inferido",
        destinos: refs,
        arista: "tipo",
        marca: a.especie === "instancia" ? "self" : undefined,
        linea: a.linea,
        atributo: a,
      });
    }
    const metodos: Def[] = [];
    const clases: Def[] = [];
    for (const h of d.hijos ?? []) {
      const x = this.defs[h];
      if (x.tipo === "metodo") metodos.push(x);
      else if (x.tipo === "clase") clases.push(x);
    }
    metodos.sort((a, b) => peso(a) - peso(b) || (a.linea ?? 0) - (b.linea ?? 0));
    for (const m of metodos) {
      const n = m.nombre;
      const dunder = n.startsWith("__") && n.endsWith("__");
      if (dunder && !PY_VISIBLES.has(n)) continue;
      if (!fl.privados && n.startsWith("_") && !dunder) continue;
      const marcas = new Set(m.marcas ?? []);
      const vis = dunder ? "+" : n.startsWith("__") ? "-" : n.startsWith("_") ? "#" : "+";
      let der = (m.firma ?? "()").replace(/^\(/, "(");
      if (marcas.has("async")) der = "async " + der;
      const tags = [marcas.has("static") ? "{static}" : "", marcas.has("classmethod") ? "{classmethod}" : "", marcas.has("abstracta") ? "{abstract}" : "", marcas.has("property") ? "{property}" : ""]
        .filter(Boolean)
        .join(" ");
      r.push({ key: `m:${m.id}`, kind: "metodo", seccion: 2, izq: vis + n, der, cIzq: "key", cDer: "value", destinos: [m.id], arista: "miembro", marca: tags || undefined, linea: m.linea });
    }
    for (const c of clases) {
      r.push({ key: `k:${c.id}`, kind: "clase", seccion: 3, izq: "C", der: c.nombre, cIzq: "muted", cDer: "tipoProyecto", destinos: [c.id], arista: "miembro", linea: c.linea });
    }
    for (const s of d.subclases ?? []) {
      r.push({ key: `s:${s}`, kind: "subclase", seccion: 4, izq: "▷", der: this.defs[s].nombre, cIzq: "muted", cDer: "tipoProyecto", destinos: [s], arista: "subclase", marca: "subclase" });
    }
    return r;
  }

  private filasApp(d: Def): Fila[] {
    return (d.llamadas ?? []).map((ll, i) => this.filaLlamada(ll, i)).filter((f): f is Fila => !!f);
  }

  private filasContenedor(d: Def): Fila[] {
    const r: Fila[] = [];
    const fl = this.filtros;
    for (const h of d.hijos ?? []) {
      const x = this.defs[h];
      switch (x.tipo) {
        case "paquete":
        case "modulo": {
          const n = (x.hijos ?? []).length;
          const der = x.tipo === "paquete" ? `paquete · ${n} elementos` : x.error ? "⚠ error de sintaxis" : `${x.lineas ?? 0} líneas`;
          r.push({ key: `h:${h}`, kind: "submodulo", seccion: 0, izq: x.nombre, der, cIzq: "key", cDer: "muted", destinos: [h], arista: "miembro" });
          break;
        }
        case "clase": {
          const marcas = (x.marcas ?? []).filter(m => ["dataclass", "pydantic", "enum", "excepcion", "protocolo", "abstracta", "modelo-orm"].includes(m));
          const bases = (x.bases ?? []).map(b => b.tipo).join(", ");
          const der = (bases ? `(${bases})` : "") + (marcas.length ? ` «${marcas[0]}»` : "");
          r.push({ key: `h:${h}`, kind: "clase", seccion: 1, izq: "C " + x.nombre, der, cIzq: "tipoProyecto", cDer: "muted", destinos: [h], arista: "miembro", linea: x.linea });
          break;
        }
        case "funcion":
          if (!fl.privados && x.nombre.startsWith("_")) break;
          r.push({ key: `h:${h}`, kind: "funcion", seccion: 2, izq: x.nombre, der: x.firma ?? "()", cIzq: "key", cDer: "value", destinos: [h], arista: "miembro", linea: x.linea });
          break;
        case "main":
          r.push({ key: `h:${h}`, kind: "main", seccion: 3, izq: "▶", der: 'if __name__ == "__main__"', cIzq: "tipoProyecto", cDer: "value", destinos: [h], arista: "miembro", linea: x.linea });
          break;
        case "nivel":
          r.push({ key: `h:${h}`, kind: "nivel", seccion: 3, izq: "≡", der: "código de nivel de módulo", cIzq: "muted", cDer: "muted", destinos: [h], arista: "miembro", linea: x.linea });
          break;
        case "app":
          r.push({ key: `h:${h}`, kind: "app", seccion: 3, izq: "W", der: `${x.clase_app ? corto(x.clase_app) : "app"} ${x.nombre} · ${x.llamadas?.length ?? 0} rutas`, cIzq: "verbo", cDer: "value", destinos: [h], arista: "miembro", linea: x.linea });
          break;
      }
    }
    return r;
  }

  // ------------------------------------------------------------------ medidas

  tam(id: number): [number, number] {
    let t = this.cacheTam.get(id);
    if (t) return t;
    const cw = this.charW;
    const cab = this.cabecera(id);
    let max = Math.max((cab.prefijo.length + cab.titulo.length) * cw * 1.08 + 46, Math.min(cab.sub.length, 70) * cw * 0.84 + 46);
    const filas = this.filas(id);
    for (const f of filas) {
      const der = Math.min(MAX_DER, f.der.length);
      const chars = f.izq.length + (der ? 2 + der : 0);
      let px = chars * cw + 2 * PAD_X + 6;
      if (f.destinos.length) px += COLLAPSE_W;
      if (f.marca) px += (f.marca.length + 1) * cw * 0.84;
      if (px > max) max = px;
    }
    const w = Math.min(MAX_NODE_W, Math.max(MIN_NODE_W, Math.ceil(max)));
    const h = HEADER_H + Math.max(1, filas.length) * ROW_H;
    t = [w, h];
    this.cacheTam.set(id, t);
    return t;
  }

  // ------------------------------------------------------------------ índices

  /** Quién llama a cada definición (de las filas de llamadas de todo el proyecto). */
  llamadores(id: number) {
    if (!this._llamadores) {
      const m = new Map<number, { def: number; linea: number; texto: string; especie: string }[]>();
      for (const d of this.defs) {
        for (const ll of d.llamadas ?? []) {
          if (ll.especie === "libreria" || ll.especie === "no-resuelta" || ll.especie === "externo") continue;
          for (const t of ll.destinos ?? []) {
            let l = m.get(t);
            if (!l) m.set(t, (l = []));
            l.push({ def: d.id, linea: ll.linea, texto: ll.texto, especie: ll.especie });
          }
        }
      }
      this._llamadores = m;
    }
    return this._llamadores.get(id) ?? [];
  }

  /** Destinos que se siguen al recorrer el flujo (BFS de caminos y servicios alcanzables). */
  sucesores(id: number, estructura = false): number[] {
    const d = this.defs[id];
    const r: number[] = [];
    for (const ll of d.llamadas ?? []) {
      if (ll.especie === "llamada" || ll.especie === "crea" || ll.especie === "encola" || ll.especie === "ruta" || ll.especie === "referencia")
        for (const t of ll.destinos ?? []) r.push(t);
    }
    if (estructura || d.tipo === "proyecto" || d.tipo === "paquete" || d.tipo === "modulo" || d.tipo === "clase") for (const h of d.hijos ?? []) r.push(h);
    return r;
  }
}

function peso(m: Def) {
  if (m.nombre === "__init__") return 0;
  if (m.nombre.startsWith("__")) return 1;
  if ((m.marcas ?? []).includes("property")) return 2;
  if (m.nombre.startsWith("_")) return 4;
  return 3;
}

/** Valor de las filas de texto para resaltar búsquedas. */
export const textoFila = (f: Fila) => (f.izq + " " + f.der).toLowerCase();

/**
 * Árbol que se dibuja, construido a demanda. Un nodo es una aparición de una definición;
 * la misma función puede aparecer en varios caminos (y en ciclos: se marca y no se
 * expande sola al usar "expandir todo").
 */
export class Arbol {
  def: number[] = [];
  padre: number[] = [];
  /** Fila del padre de la que cuelga el nodo. */
  filaPadre: string[] = [];
  depth: number[] = [];
  ciclo: boolean[] = [];
  /** Valores conocidos de los parámetros en este camino (para completar URLs, SQL...). */
  valores: (Record<string, string> | undefined)[] = [];
  private hijos: (Map<string, number[]> | undefined)[] = [];
  private abiertas: (Set<string> | undefined)[] = [];

  constructor(readonly modelo: Modelo) {}

  get count() {
    return this.def.length;
  }

  nuevo(defId: number, padre = -1, fila = "", valores?: Record<string, string>): number {
    const id = this.def.length;
    this.def.push(defId);
    this.padre.push(padre);
    this.filaPadre.push(fila);
    this.depth.push(padre >= 0 ? this.depth[padre] + 1 : 0);
    this.ciclo.push(padre >= 0 && this.esAncestroDef(padre, defId));
    this.valores.push(valores);
    this.hijos.push(undefined);
    this.abiertas.push(undefined);
    return id;
  }

  esAncestroDef(n: number, defId: number): boolean {
    for (let x = n; x >= 0; x = this.padre[x]) if (this.def[x] === defId) return true;
    return false;
  }

  hijosDe(n: number, f: Fila): number[] {
    let m = this.hijos[n];
    if (!m) this.hijos[n] = m = new Map();
    let l = m.get(f.key);
    if (!l) {
      l = [];
      const vals = this.valoresHijo(n, f);
      for (const d of f.destinos) l.push(this.nuevo(d, n, f.key, vals));
      m.set(f.key, l);
    }
    return l;
  }

  /** Hijos ya creados de una fila (sin crearlos). */
  hijosCreados(n: number, key: string): number[] | undefined {
    return this.hijos[n]?.get(key);
  }

  private valoresHijo(n: number, f: Fila): Record<string, string> | undefined {
    const ll = f.llamada;
    if (!ll?.valores) return undefined;
    const propios = this.valores[n];
    const r: Record<string, string> = {};
    for (const [k, v] of Object.entries(ll.valores)) r[k] = propios ? sustituir(v, propios) : v;
    return r;
  }

  abierta(n: number, key: string) {
    return !!this.abiertas[n]?.has(key);
  }

  abrir(n: number, key: string, si = true) {
    let s = this.abiertas[n];
    if (!s) this.abiertas[n] = s = new Set();
    if (si) s.add(key);
    else s.delete(key);
  }

  filasAbiertas(n: number): string[] {
    return [...(this.abiertas[n] ?? [])];
  }

  cerrarTodo(n: number) {
    this.abiertas[n]?.clear();
  }
}

const FLUJO_AUTO = new Set<FilaKind>(["llamada", "crea", "encola", "ruta"]);
const ESTRUCTURA_AUTO = new Set<FilaKind>(["submodulo", "clase", "funcion", "main", "nivel", "app"]);
const METODOS_AUTO = new Set<FilaKind>(["metodo"]);
const CONTENEDORES = new Set(["proyecto", "paquete", "modulo"]);

/**
 * Filas de un nodo que se abren solas (al empezar o con «Expandir»). En el modo Flujo un
 * archivo o paquete abre su contenido (funciones, clases, main, app) y desde ahí se siguen
 * las llamadas; una clase que cuelga de un archivo abre sus métodos.
 */
export function filasAuto(modo: "flujo" | "estructura", modelo: Modelo, arbol: Arbol, n: number): Set<FilaKind> {
  if (modo === "estructura") return ESTRUCTURA_AUTO;
  const tipo = modelo.def(arbol.def[n]).tipo;
  if (CONTENEDORES.has(tipo)) return ESTRUCTURA_AUTO;
  if (tipo === "clase") {
    const p = arbol.padre[n];
    if (p < 0 || CONTENEDORES.has(modelo.def(arbol.def[p]).tipo)) return METODOS_AUTO;
  }
  return FLUJO_AUTO;
}

/** Reemplaza "{param}" por el valor conocido en el camino. */
export function sustituir(texto: string, valores: Record<string, string>): string {
  return texto.replace(/\{([A-Za-z_][\w]*)\}/g, (m, nombre: string) => (nombre in valores ? valores[nombre] : m));
}

/** Valor conocido de un parámetro; del texto por defecto solo sirven cadenas y números (no None ni Field(...)). */
function valorParam(p: Param, construccion: boolean): string | undefined {
  const v = (construccion ? p.valor_construccion : undefined) ?? p.valor;
  if (v) return v;
  const d = p.defecto;
  if (d && /^(['"]).*\1$/.test(d)) return d.slice(1, -1);
  if (d && /^-?\d+(\.\d+)?$/.test(d)) return d;
  return undefined;
}

/**
 * Destino de un servicio externo visto desde un nodo: primero los valores que llegan por el
 * camino, después los valores por defecto de los parámetros y, para lo que sale de
 * `self.x`, el valor con que se construye la clase en el proyecto (si es uno solo).
 */
export function destinoEnCamino(modelo: Modelo, arbol: Arbol, n: number, ll: Llamada): string {
  return destinoEstatico(modelo, arbol.def[n], ll, arbol.valores[n]);
}

/** Igual que `destinoEnCamino`, para una definición sin camino concreto (o con `vals` dados). */
export function destinoEstatico(modelo: Modelo, defId: number, ll: Llamada, vals?: Record<string, string>): string {
  const e = ll.externo;
  if (!e) return ll.texto;
  let destino = e.destino ?? "";
  if (vals) destino = sustituir(destino, vals);
  if (destino.includes("{")) {
    const d = modelo.def(defId);
    const otros: Record<string, string> = {};
    const propios = new Set((d.params ?? []).map(p => p.nombre));
    for (const p of d.params ?? []) {
      const v = valorParam(p, d.nombre === "__init__");
      if (v !== undefined && !(vals && p.nombre in vals)) otros[p.nombre] = v;
    }
    const init = d.tipo === "metodo" && d.clase !== undefined && d.clase >= 0 ? modelo.def(d.clase).init : undefined;
    if (init !== undefined)
      for (const p of modelo.def(init).params ?? []) {
        const v = valorParam(p, true);
        if (v !== undefined && !propios.has(p.nombre) && !(p.nombre in otros)) otros[p.nombre] = v;
      }
    destino = sustituir(destino, otros);
  }
  return destino || ll.texto;
}
