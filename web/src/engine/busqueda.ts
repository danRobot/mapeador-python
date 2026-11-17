import { destinoEstatico, type FilaKind, type Modelo } from "./model";

/** Filas que se siguen al buscar un camino en el modo Flujo. */
export const KINDS_FLUJO = new Set<FilaKind>(["llamada", "crea", "encola", "ruta", "referencia"]);
/** Filas de estructura (paquete → módulo → clase → método). */
export const KINDS_ESTRUCTURA = new Set<FilaKind>(["submodulo", "clase", "funcion", "main", "nivel", "app", "metodo"]);
/** Filas que llevan a tipos (parámetros, atributos, bases): último recurso para llegar a una clase. */
export const KINDS_TIPOS = new Set<FilaKind>(["param", "retorno", "atributo", "base", "subclase"]);

const LETRA: Record<string, string> = {
  proyecto: "P",
  paquete: "P",
  modulo: "M",
  clase: "C",
  funcion: "F",
  metodo: "m",
  main: "▶",
  nivel: "≡",
  app: "W",
};

export interface Resultado {
  def: number;
  letra: string;
  /** Nombre corto (Clase.metodo, función, módulo). */
  titulo: string;
  /** Dónde está: paquete.modulo. */
  contexto: string;
  sub: string;
}

/** Busca definiciones por nombre (exacto, prefijo o contenido en el nombre completo). */
export class Buscador {
  private nombres: string[];
  private quals: string[];

  constructor(private modelo: Modelo) {
    this.nombres = modelo.defs.map(d => d.nombre.toLowerCase());
    this.quals = modelo.defs.map(d => (d.tipo === "main" || d.tipo === "nivel" ? `${d.archivo ?? ""} ${d.qual}` : d.qual).toLowerCase());
  }

  buscar(consulta: string, limite = 60): Resultado[] {
    const q = consulta.trim().toLowerCase();
    if (!q) return [];
    const puntos: [number, number][] = [];
    const defs = this.modelo.defs;
    for (let i = 0; i < defs.length; i++) {
      const d = defs[i];
      if (d.tipo === "proyecto") continue;
      const n = this.nombres[i];
      let p = -1;
      if (n === q) p = 0;
      else if (n.startsWith(q)) p = 1;
      else if (n.includes(q)) p = 2;
      else if (this.quals[i].includes(q)) p = 3;
      if (p < 0) continue;
      // las funciones y clases antes que los módulos; lo privado al final
      const peso = p * 10 + (d.tipo === "paquete" || d.tipo === "modulo" ? 3 : 0) + (d.nombre.startsWith("_") ? 2 : 0);
      puntos.push([peso, i]);
    }
    puntos.sort((a, b) => a[0] - b[0] || defs[a[1]].qual.length - defs[b[1]].qual.length);
    return puntos.slice(0, limite).map(([, i]) => {
      const d = defs[i];
      let titulo = d.nombre;
      if (d.tipo === "metodo" && d.clase !== undefined && d.clase >= 0) titulo = `${defs[d.clase].nombre}.${d.nombre}`;
      else if (d.tipo === "main") titulo = `${d.archivo} · main`;
      else if (d.tipo === "nivel") titulo = `${d.archivo} · nivel de módulo`;
      else if (d.tipo === "modulo" || d.tipo === "paquete") titulo = d.qual;
      const pre = d.qual.endsWith(titulo) ? d.qual.slice(0, d.qual.length - titulo.length).replace(/\.$/, "") : "";
      return {
        def: i,
        letra: LETRA[d.tipo] ?? "?",
        titulo,
        contexto: d.tipo === "main" || d.tipo === "nivel" ? "" : pre,
        sub: d.archivo ? `${d.archivo}${d.linea ? ":" + d.linea : ""}` : d.tipo,
      };
    });
  }
}

/**
 * Camino más corto (BFS) de `desde` a `hasta` siguiendo las filas visibles de los tipos
 * indicados. Devuelve los ids de definición, incluidos los extremos, o null.
 */
export function camino(modelo: Modelo, desde: number, hasta: number, kinds: Set<FilaKind>, limite = 60000): number[] | null {
  if (desde === hasta) return [desde];
  const previo = new Map<number, number>([[desde, -1]]);
  const cola = [desde];
  let k = 0;
  while (k < cola.length && previo.size < limite) {
    const x = cola[k++];
    for (const f of modelo.filas(x)) {
      if (!kinds.has(f.kind)) continue;
      for (const d of f.destinos) {
        if (previo.has(d)) continue;
        previo.set(d, x);
        if (d === hasta) {
          const r = [d];
          for (let y = x; y !== -1; y = previo.get(y)!) r.push(y);
          return r.reverse();
        }
        cola.push(d);
      }
    }
  }
  return null;
}

/**
 * Servicios externos alcanzables desde una definición siguiendo el flujo (para el panel).
 * Devuelve cada servicio una vez, con la primera definición desde la que se llega.
 */
export function serviciosAlcanzables(modelo: Modelo, desde: number, limite = 4000) {
  const vistos = new Set<number>([desde]);
  const cola = [desde];
  const r = new Map<string, { tipo: string; verbo?: string; destino: string; via: number; linea: number }>();
  let k = 0;
  while (k < cola.length && vistos.size < limite) {
    const x = cola[k++];
    const d = modelo.def(x);
    for (const ll of d.llamadas ?? []) {
      if (ll.especie === "externo" && ll.externo) {
        const e = ll.externo;
        const destino = destinoEstatico(modelo, x, ll);
        const clave = `${e.tipo}|${e.verbo ?? ""}|${destino}`;
        if (!r.has(clave)) r.set(clave, { tipo: e.tipo, verbo: e.verbo, destino, via: x, linea: ll.linea });
      }
      if (ll.especie === "llamada" || ll.especie === "crea" || ll.especie === "encola" || ll.especie === "ruta" || ll.especie === "referencia") {
        for (const t of ll.destinos ?? []) {
          if (!vistos.has(t)) {
            vistos.add(t);
            cola.push(t);
          }
        }
      }
    }
  }
  return { servicios: [...r.values()], alcanzadas: vistos.size - 1, truncado: vistos.size >= limite };
}
