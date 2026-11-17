import type { Def } from "../tipos";
import { destinoEnCamino, type Arbol, type Modelo } from "./model";

/**
 * Exportación a PlantUML de lo que está abierto en pantalla:
 *  - diagrama de secuencia: las llamadas abiertas, en orden, desde la raíz de la etapa;
 *  - diagrama de clases: las clases que aparecen (y las de los métodos y tipos de parámetros).
 * El texto se puede pegar en cualquier PlantUML (jar local, extensión de VS Code o el
 * servidor interno de la empresa); aquí no se envía a ningún sitio.
 */

/** Una línea, comillas simples y sin marcas de creole (__x__ subraya, **x** negrita, //x// cursiva). */
const limpiar = (s: string, max = 90) => {
  let t = s.replace(/\s+/g, " ").replace(/"/g, "'");
  if (t.length > max) t = t.slice(0, max - 1) + "…";
  return t.replace(/([_*/\-~])(?=\1)/g, "$1\u200b");
};

const PARTICIPANTE_EXT: Record<string, string> = {
  BD: "database",
  CACHE: "database",
  COLA: "queue",
  ARCHIVO: "collections",
  HTTP: "boundary",
  SOAP: "boundary",
  GRPC: "boundary",
  SOCKET: "boundary",
  NUBE: "cloud",
  CORREO: "boundary",
  PROCESO: "control",
};

function participanteDe(m: Modelo, d: Def): { clave: string; nombre: string; tipo: string } {
  if (d.tipo === "metodo" && d.clase !== undefined && d.clase >= 0) {
    const c = m.def(d.clase);
    return { clave: `c${c.id}`, nombre: c.nombre, tipo: "participant" };
  }
  if (d.tipo === "main" || d.tipo === "nivel") return { clave: `d${d.id}`, nombre: d.archivo ?? "main", tipo: "actor" };
  if (d.tipo === "app") return { clave: `d${d.id}`, nombre: `${d.nombre} (app)`, tipo: "boundary" };
  if (d.tipo === "clase") return { clave: `c${d.id}`, nombre: d.nombre, tipo: "participant" };
  const mod = d.modulo !== undefined && d.modulo >= 0 ? m.def(d.modulo) : d;
  return { clave: `m${mod.id}`, nombre: mod.qual.split(".").slice(-1)[0] || mod.qual, tipo: "participant" };
}

export function plantumlSecuencia(m: Modelo, a: Arbol, raiz: number, visible: (n: number) => boolean): string {
  const participantes = new Map<string, { nombre: string; tipo: string; alias: string }>();
  const lineas: string[] = [];
  const alias = (p: { clave: string; nombre: string; tipo: string }) => {
    let x = participantes.get(p.clave);
    if (!x) participantes.set(p.clave, (x = { nombre: p.nombre, tipo: p.tipo, alias: `P${participantes.size}` }));
    return x.alias;
  };
  const visitar = (n: number, nivel: number) => {
    if (nivel > 40) return;
    const d = m.def(a.def[n]);
    const yo = alias(participanteDe(m, d));
    for (const f of m.filas(d.id)) {
      const ll = f.llamada;
      if (!ll) continue;
      if (f.kind === "externo" && ll.externo) {
        const e = ll.externo;
        const p = alias({ clave: `x${e.tipo}`, nombre: e.tipo, tipo: PARTICIPANTE_EXT[e.tipo] ?? "entity" });
        lineas.push(`${yo} -> ${p} : ${limpiar(`${e.verbo ?? ""} ${destinoEnCamino(m, a, n, ll)}`.trim())}`);
        continue;
      }
      if (f.kind === "lanza") {
        lineas.push(`note right of ${yo} : ${limpiar(ll.texto, 70)}`);
        continue;
      }
      if (!f.destinos.length || !a.abierta(n, f.key)) continue;
      const hijos = (a.hijosCreados(n, f.key) ?? []).filter(visible);
      for (const h of hijos) {
        const dh = m.def(a.def[h]);
        const otro = alias(participanteDe(m, dh));
        const flecha = f.kind === "encola" ? "->>" : f.kind === "referencia" ? "-->" : "->";
        const etiqueta = f.kind === "crea" ? `«create» ${ll.texto}` : f.kind === "ruta" ? `${ll.verbo ?? ""} ${ll.ruta ?? ""}` : ll.texto;
        lineas.push(`${yo} ${flecha} ${otro} : ${limpiar(etiqueta)}`);
        if (a.ciclo[h]) {
          lineas.push(`note right of ${otro} : recursión (ya está más arriba)`);
          continue;
        }
        lineas.push(`activate ${otro}`);
        visitar(h, nivel + 1);
        const ret = dh.retorno?.tipo;
        if (ret && ret !== "None" && f.kind !== "encola") lineas.push(`${otro} --> ${yo} : ${limpiar(ret, 50)}`);
        lineas.push(`deactivate ${otro}`);
      }
    }
  };
  const raizDef = m.def(a.def[raiz]);
  alias(participanteDe(m, raizDef));
  visitar(raiz, 0);
  const cab = [...participantes.values()].map(p => `${p.tipo} "${limpiar(p.nombre, 50)}" as ${p.alias}`);
  return [
    "@startuml",
    `title Flujo desde ${limpiar(raizDef.tipo === "main" || raizDef.tipo === "nivel" ? `${raizDef.archivo ?? raizDef.qual} (${raizDef.tipo === "main" ? "main" : "nivel de módulo"})` : raizDef.qual, 80)}`,
    "autonumber",
    "skinparam responseMessageBelowArrow true",
    ...cab,
    "",
    ...(lineas.length ? lineas : ["note over P0 : (abre filas de llamadas con + para ver el flujo)"]),
    "@enduml",
  ].join("\n");
}

export function plantumlClases(m: Modelo, a: Arbol, nodos: number[]): string {
  const clases = new Set<number>();
  const agregar = (id: number | undefined) => {
    if (id !== undefined && id >= 0 && m.def(id).tipo === "clase") clases.add(id);
  };
  for (const n of nodos) {
    const d = m.def(a.def[n]);
    if (d.tipo === "clase") agregar(d.id);
    if (d.tipo === "metodo") agregar(d.clase);
    for (const p of d.params ?? []) for (const r of p.refs ?? []) agregar(r);
    for (const r of d.retorno?.refs ?? []) agregar(r);
  }
  // también las bases y los tipos de atributos de las clases encontradas (un nivel)
  for (const c of [...clases]) {
    const d = m.def(c);
    for (const b of d.bases ?? []) for (const r of b.refs ?? []) agregar(r);
    for (const at of d.atributos ?? []) for (const r of at.refs ?? []) agregar(r);
  }
  const porModulo = new Map<string, number[]>();
  for (const c of clases) {
    const d = m.def(c);
    const mod = d.modulo !== undefined && d.modulo >= 0 ? m.def(d.modulo).qual : "";
    if (!porModulo.has(mod)) porModulo.set(mod, []);
    porModulo.get(mod)!.push(c);
  }
  const out = ["@startuml", "skinparam classAttributeIconSize 0", "hide empty members", ""];
  const relaciones: string[] = [];
  for (const [mod, ids] of porModulo) {
    out.push(`package "${mod}" {`);
    for (const c of ids) {
      const d = m.def(c);
      const marcas = new Set(d.marcas ?? []);
      let palabra = "class";
      if (marcas.has("enum")) palabra = "enum";
      else if (marcas.has("protocolo")) palabra = "interface";
      else if (marcas.has("abstracta")) palabra = "abstract class";
      const estereo = ["dataclass", "pydantic", "excepcion", "modelo-orm", "namedtuple", "typeddict"].filter(x => marcas.has(x));
      out.push(`  ${palabra} "${d.nombre}" as C${c}${estereo.length ? " <<" + estereo.join(", ") + ">>" : ""} {`);
      for (const at of d.atributos ?? []) {
        if (at.especie === "enum") out.push(`    ${at.nombre}`);
        else out.push(`    ${at.nombre.startsWith("_") ? "-" : "+"}${at.nombre}${at.tipo ? " : " + limpiar(at.tipo, 50) : ""}`);
        for (const r of at.refs ?? []) if (r !== c && clases.has(r)) relaciones.push(`C${c} --> C${r} : ${at.nombre}`);
      }
      for (const h of d.hijos ?? []) {
        const x = m.def(h);
        if (x.tipo !== "metodo") continue;
        const dunder = x.nombre.startsWith("__") && x.nombre.endsWith("__");
        if (dunder && x.nombre !== "__init__" && x.nombre !== "__call__") continue;
        const mm = new Set(x.marcas ?? []);
        const mods = `${mm.has("abstracta") ? "{abstract} " : ""}${mm.has("static") || mm.has("classmethod") ? "{static} " : ""}`;
        const params = (x.params ?? [])
          .filter(p => p.especie !== "self" && p.especie !== "cls")
          .map(p => `${p.nombre}${p.tipo ? ": " + limpiar(p.tipo, 30) : ""}`)
          .join(", ");
        const ret = x.retorno?.tipo && x.retorno.tipo !== "None" ? ` : ${limpiar(x.retorno.tipo, 40)}` : "";
        out.push(`    ${mods}${x.nombre.startsWith("_") && !dunder ? "-" : "+"}${x.nombre}(${limpiar(params, 80)})${ret}`);
        for (const p of x.params ?? []) for (const r of p.refs ?? []) if (r !== c && clases.has(r)) relaciones.push(`C${c} ..> C${r}`);
      }
      out.push("  }");
      for (const b of d.bases ?? []) for (const r of b.refs ?? []) if (clases.has(r)) relaciones.push(`C${r} <|-- C${c}`);
    }
    out.push("}");
  }
  out.push("", ...[...new Set(relaciones)], "@enduml");
  if (!clases.size) return "@startuml\nnote \"No hay clases en lo que está abierto en pantalla\" as N\n@enduml";
  return out.join("\n");
}
