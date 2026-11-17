import { serviciosAlcanzables } from "./engine/busqueda";
import { destinoEnCamino, sustituir, type Arbol, type Fila, type Modelo } from "./engine/model";
import type { Def, Param } from "./tipos";

/** Acciones que el panel pide a la aplicación. */
export interface ContextoPanel {
  modelo: Modelo;
  arbol: Arbol;
  /** Muestra una definición: la revela en el árbol si se alcanza; si no, abre una etapa. */
  irADef(defId: number): void;
  fuente(archivo: string): Promise<string | null>;
}

const esc = (s: unknown) =>
  String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

const ORIGEN: Record<string, string> = {
  anotacion: "anotación",
  docstring: "docstring",
  inferido: "inferido",
  defecto: "valor por defecto",
  self: "self",
  cls: "cls",
};

const ESPECIE: Record<string, string> = {
  llamada: "llamada",
  crea: "construcción («create»)",
  encola: "tarea encolada",
  referencia: "función pasada como argumento",
  lanza: "lanza excepción",
  ruta: "ruta web",
  externo: "servicio externo",
  libreria: "librería",
  "no-resuelta": "no resuelta",
};

const MECANISMO: Record<string, string> = {
  self: "método de la misma clase (self)",
  cls: "método de clase (cls)",
  super: "super()",
  anotacion: "tipo anotado del receptor",
  docstring: "tipo del receptor según el docstring",
  inferido: "tipo inferido de las llamadas (ronda anterior)",
  defecto: "tipo del valor por defecto",
  variable: "variable local tipada por su asignación",
  atributo: "atributo del objeto (asignado en __init__ o anotado)",
  retorno: "valor devuelto por otra llamada",
  modulo: "función del mismo módulo",
  import: "nombre importado",
  clase: "llamada sobre la clase",
  local: "función anidada",
  global: "variable global",
  polimorfismo: "sobrescritura en una subclase (polimorfismo)",
  protocolo: "clase que cumple el Protocol",
  "nombre-unico": "único método del proyecto con ese nombre (heurística)",
  tarea: "tarea asíncrona (delay / apply_async)",
  referencia: "función o clase pasada como argumento",
  app: "aplicación web que se levanta aquí",
  raise: "excepción",
  directa: "llamada directa",
  expresion: "expresión",
};

function link(m: Modelo, id: number, texto?: string) {
  const d = m.def(id);
  return `<a class="lnk" data-def="${id}" title="${esc(d.qual)}">${esc(texto ?? d.nombre)}</a>`;
}

function tipoConLinks(m: Modelo, tipo: string | undefined, refs: number[] | undefined) {
  if (!tipo) return '<span class="dim">—</span>';
  let html = esc(tipo);
  for (const r of refs ?? []) {
    const n = m.def(r).nombre;
    html = html.replace(new RegExp(`\\b${n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`), `<a class="lnk" data-def="${r}">${esc(n)}</a>`);
  }
  return html;
}

function tablaParams(m: Modelo, params: Param[]) {
  const filas = params.filter(p => p.especie !== "self" && p.especie !== "cls");
  if (!filas.length) return '<div class="dim">sin parámetros</div>';
  return `<table class="tabla"><thead><tr><th>parámetro</th><th>tipo</th><th>origen</th><th>por defecto</th></tr></thead><tbody>${filas
    .map(p => {
      const pref = p.especie === "*" ? "*" : p.especie === "**" ? "**" : "";
      let def = p.defecto ? `<code>${esc(p.defecto)}</code>${p.valor ? `<div class="dim small">= ${esc(p.valor)}</div>` : ""}` : "";
      if (p.valor_construccion) def += `<div class="dim small" title="Valor con que se construye la clase en el proyecto">al construir: <code>${esc(p.valor_construccion)}</code></div>`;
      const doc = p.doc ? `<div class="dim small">${esc(p.doc)}</div>` : "";
      return `<tr><td><code class="k">${esc(pref + p.nombre)}</code>${doc}</td><td class="t ${p.origen === "inferido" ? "inf" : ""}">${tipoConLinks(m, p.tipo, p.refs)}</td><td class="dim">${esc(ORIGEN[p.origen ?? ""] ?? "")}</td><td>${def}</td></tr>`;
    })
    .join("")}</tbody></table>`;
}

function chips(d: Def) {
  const c: string[] = [];
  for (const mk of d.marcas ?? []) if (!["privada", "dunder", "oculto"].includes(mk)) c.push(`<span class="chip">${esc(mk)}</span>`);
  for (const dec of (d.decoradores ?? []).slice(0, 4)) c.push(`<span class="chip deco">${esc(dec)}</span>`);
  return c.join("");
}

function detalleFila(ctx: ContextoPanel, n: number, f: Fila): string {
  const m = ctx.modelo;
  const ll = f.llamada;
  const partes: string[] = [];
  if (ll) {
    partes.push(`<div class="kv"><span>especie</span><b>${esc(ESPECIE[ll.especie] ?? ll.especie)}</b></div>`);
    partes.push(`<div class="kv"><span>línea</span><b>${ll.linea}</b></div>`);
    if (ll.confianza) partes.push(`<div class="kv"><span>confianza</span><b class="conf-${ll.confianza}">${ll.confianza}</b></div>`);
    const mecs = ll.mecanismos ?? (ll.mecanismo ? [ll.mecanismo] : []);
    if (ll.destinos?.length) {
      partes.push(
        `<div class="kv col"><span>va a</span><div>${ll.destinos
          .map((t, i) => `${link(m, t, m.def(t).qual)}${mecs[i] || mecs[0] ? ` <span class="dim small">· ${esc(MECANISMO[mecs[i] ?? mecs[0]] ?? mecs[i] ?? mecs[0])}</span>` : ""}`)
          .join("<br>")}</div></div>`
      );
    }
    if (ll.externo) {
      const e = ll.externo;
      const enCamino = destinoEnCamino(m, ctx.arbol, n, ll);
      partes.push(`<div class="kv"><span>servicio</span><b class="ext ext-${esc(e.tipo)}">${esc(e.tipo)} ${esc(e.verbo ?? "")}</b></div>`);
      partes.push(`<div class="kv col"><span>destino en este camino</span><code class="dest">${esc(enCamino)}</code></div>`);
      if (e.destino && e.destino !== enCamino) partes.push(`<div class="kv col"><span>destino en el código</span><code>${esc(e.destino)}</code></div>`);
      if (e.destino_defecto && e.destino_defecto !== enCamino) partes.push(`<div class="kv col"><span>con valores por defecto</span><code>${esc(e.destino_defecto)}</code></div>`);
      if (e.tablas?.length) partes.push(`<div class="kv"><span>tablas</span><b>${esc(e.tablas.join(", "))}</b></div>`);
      if (e.regla) partes.push(`<div class="kv"><span>regla</span><b>${esc(e.regla)}</b></div>`);
      for (const nota of e.notas ?? []) partes.push(`<div class="nota">${esc(nota)}</div>`);
    }
    if (ll.qual && ll.especie !== "externo") partes.push(`<div class="kv"><span>función</span><code>${esc(ll.qual)}</code></div>`);
    if (ll.motivo) partes.push(`<div class="nota">${esc(ll.motivo)}</div>`);
    if (ll.candidatos?.length) partes.push(`<div class="kv col"><span>candidatos</span><div>${ll.candidatos.map(c => link(m, c, m.def(c).qual)).join("<br>")}</div></div>`);
    if (ll.nota) partes.push(`<div class="nota">${esc(ll.nota)}</div>`);
    if (ll.valores && Object.keys(ll.valores).length) {
      const vals = ctx.arbol.valores[n];
      partes.push(
        `<div class="kv col"><span>valores que recibe</span><div>${Object.entries(ll.valores)
          .map(([k, v]) => `<code class="k">${esc(k)}</code> = <code>${esc(vals ? sustituir(v, vals) : v)}</code>`)
          .join("<br>")}</div></div>`
      );
    } else if (ll.args?.length) partes.push(`<div class="kv col"><span>argumentos</span><code>${esc(ll.args.join(", "))}</code></div>`);
    return `<pre class="call">${esc(ll.texto)}</pre>${partes.join("")}`;
  }
  if (f.param) {
    const p = f.param;
    return `<div class="kv"><span>parámetro</span><code class="k">${esc(p.nombre)}</code></div><div class="kv"><span>tipo</span><b>${tipoConLinks(m, p.tipo, p.refs)}</b></div><div class="kv"><span>origen</span><b>${esc(ORIGEN[p.origen ?? ""] ?? "desconocido")}</b></div>${p.defecto ? `<div class="kv"><span>por defecto</span><code>${esc(p.defecto)}</code></div>` : ""}${p.valor ? `<div class="kv col"><span>valor por defecto evaluado</span><code>${esc(p.valor)}</code></div>` : ""}${ctx.arbol.valores[n]?.[p.nombre] ? `<div class="kv col"><span>en este camino recibe</span><code>${esc(ctx.arbol.valores[n]![p.nombre])}</code></div>` : ""}${p.doc ? `<div class="nota">${esc(p.doc)}</div>` : ""}`;
  }
  if (f.atributo) {
    const a = f.atributo;
    return `<div class="kv"><span>atributo</span><code class="k">${esc(a.nombre)}</code></div><div class="kv"><span>tipo</span><b>${tipoConLinks(m, a.tipo, a.refs)}</b></div><div class="kv"><span>origen</span><b>${esc(ORIGEN[a.origen ?? ""] ?? a.origen ?? "")}</b></div><div class="kv"><span>de</span><b>${a.especie === "instancia" ? "instancia (self.…)" : a.especie === "enum" ? "miembro del Enum" : "clase"}</b></div>${a.defecto ? `<div class="kv"><span>valor</span><code>${esc(a.defecto)}</code></div>` : ""}`;
  }
  return `<pre class="call">${esc(f.izq)} ${esc(f.der)}</pre>${f.destinos.length ? `<div class="kv col"><span>lleva a</span><div>${f.destinos.map(t => link(m, t, m.def(t).qual)).join("<br>")}</div></div>` : ""}`;
}

/** Contenido del panel lateral para el nodo `n` (y la fila `row`, si hay). */
export function renderPanel(el: HTMLElement, ctx: ContextoPanel, n: number, row: number) {
  const m = ctx.modelo;
  const a = ctx.arbol;
  const d = m.def(a.def[n]);
  const cab = m.cabecera(d.id);
  const filas = m.filas(d.id);
  const f = row >= 0 ? filas[row] : undefined;
  const s: string[] = [];
  s.push(`<div class="p-titulo"><span class="letra est-${esc(cab.estereo)}">${esc(cab.letra)}</span><div><div class="p-qual">${esc(d.qual)}</div><div class="dim small">${esc(d.tipo)}${d.archivo ? ` · ${esc(d.archivo)}${d.linea ? `:${d.linea}${d.fin && d.fin !== d.linea ? "–" + d.fin : ""}` : ""}` : ""}</div></div></div>`);
  const ch = chips(d);
  if (ch) s.push(`<div class="chips">${ch}</div>`);
  if (d.doc) s.push(`<div class="doc">${esc(d.doc)}</div>`);
  if (a.ciclo[n]) s.push(`<div class="nota">↻ Esta definición ya aparece más arriba en este camino (recursión o ciclo).</div>`);
  if (f) s.push(`<section><h4>Fila seleccionada</h4>${detalleFila(ctx, n, f)}</section>`);

  if (d.tipo === "funcion" || d.tipo === "metodo") {
    s.push(`<section><h4>Firma</h4><pre class="firma">${esc(d.nombre)}${esc(d.firma ?? "()")}</pre>${tablaParams(m, d.params ?? [])}`);
    if (d.retorno) s.push(`<div class="kv"><span>retorna</span><b>${tipoConLinks(m, d.retorno.tipo, d.retorno.refs)}</b> <span class="dim small">(${esc(ORIGEN[d.retorno.origen ?? ""] ?? "")})</span></div>`);
    if (d.clase !== undefined && d.clase >= 0) s.push(`<div class="kv"><span>clase</span><b>${link(m, d.clase, m.def(d.clase).qual)}</b></div>`);
    s.push("</section>");
  }
  if (d.tipo === "clase") {
    if (d.bases?.length) s.push(`<section><h4>Hereda de</h4>${d.bases.map(b => tipoConLinks(m, b.tipo, b.refs)).join(", ")}</section>`);
    if (d.atributos?.length)
      s.push(
        `<section><h4>Atributos</h4><table class="tabla"><thead><tr><th>nombre</th><th>tipo</th><th>de</th></tr></thead><tbody>${d.atributos
          .map(at => `<tr><td><code class="k">${esc(at.nombre)}</code></td><td class="t ${at.origen === "inferido" ? "inf" : ""}">${at.especie === "enum" ? `<code>${esc(at.tipo)}</code>` : tipoConLinks(m, at.tipo, at.refs)}${at.defecto && at.especie !== "enum" ? ` <span class="dim">= ${esc(at.defecto)}</span>` : ""}</td><td class="dim">${at.especie === "instancia" ? "self" : at.especie === "enum" ? "enum" : "clase"}</td></tr>`)
          .join("")}</tbody></table></section>`
      );
    const metodos = (d.hijos ?? []).map(h => m.def(h)).filter(x => x.tipo === "metodo");
    if (metodos.length) s.push(`<section><h4>Métodos (${metodos.length})</h4><div class="lista">${metodos.map(x => `<div>${link(m, x.id, x.nombre)}<span class="dim">${esc(x.firma ?? "")}</span></div>`).join("")}</div></section>`);
    if (d.subclases?.length) s.push(`<section><h4>Subclases</h4>${d.subclases.map(x => link(m, x, m.def(x).qual)).join("<br>")}</section>`);
  }
  if (d.tipo === "app") {
    s.push(`<section><h4>Rutas (${d.llamadas?.length ?? 0})</h4><div class="lista">${(d.llamadas ?? []).map(r => `<div><b class="verbo">${esc(r.verbo)}</b> ${esc(r.ruta)} → ${(r.destinos ?? []).map(t => link(m, t)).join(", ")}</div>`).join("")}</div></section>`);
  }
  if (d.error) s.push(`<div class="nota">⚠ ${esc(d.error)}</div>`);

  // quién la llama
  const llamadores = m.llamadores(d.id);
  if (d.tipo !== "proyecto" && d.tipo !== "paquete" && d.tipo !== "modulo") {
    const vistos = new Set<number>();
    const items = llamadores.filter(x => !vistos.has(x.def) && (vistos.add(x.def), true));
    s.push(
      `<section><h4>Llamada desde (${items.length})</h4>${
        items.length
          ? `<div class="lista">${items
              .slice(0, 60)
              .map(x => `<div>${link(m, x.def, m.def(x.def).qual)} <span class="dim small">línea ${x.linea}${x.especie !== "llamada" ? " · " + esc(ESPECIE[x.especie] ?? x.especie) : ""}</span></div>`)
              .join("")}${items.length > 60 ? `<div class="dim">… y ${items.length - 60} más</div>` : ""}</div>`
          : '<div class="dim">Nadie la llama dentro del proyecto (¿entrada, callback de un framework o código muerto?).</div>'
      }</section>`
    );
  }

  // servicios externos que se alcanzan desde aquí
  if (d.tipo !== "proyecto" && d.tipo !== "paquete" && d.tipo !== "modulo" && d.tipo !== "clase") {
    const { servicios, alcanzadas, truncado } = serviciosAlcanzables(m, d.id);
    s.push(
      `<section><h4>Servicios externos alcanzables (${servicios.length})</h4>${
        servicios.length
          ? `<div class="lista">${servicios
              .slice(0, 80)
              .map(x => `<div><b class="ext ext-${esc(x.tipo)}">${esc(x.tipo)}</b> ${x.verbo ? `<b>${esc(x.verbo)}</b> ` : ""}<code>${esc(x.destino)}</code> <span class="dim small">en ${link(m, x.via)}:${x.linea}</span></div>`)
              .join("")}</div>`
          : '<div class="dim">Ninguno.</div>'
      }<div class="dim small">Recorre ${alcanzadas} ${alcanzadas === 1 ? "función alcanzable" : "funciones alcanzables"}${truncado ? " (recortado)" : ""}.</div></section>`
    );
  }

  // código
  if (d.archivo && d.tipo !== "paquete" && d.tipo !== "proyecto") s.push(`<section><h4>Código <span class="dim small">${esc(d.archivo)}</span></h4><pre class="codigo" id="panel-codigo">cargando…</pre></section>`);
  el.innerHTML = s.join("");

  if (d.archivo && d.tipo !== "paquete" && d.tipo !== "proyecto") {
    const destino = el.querySelector<HTMLElement>("#panel-codigo")!;
    const resaltar = f?.linea;
    ctx.fuente(d.archivo).then(texto => {
      if (!destino.isConnected) return;
      if (texto == null) {
        destino.textContent = "El código no está disponible (abre la carpeta o genera el JSON con --incluir-fuentes).";
        destino.classList.add("dim");
        return;
      }
      const lineas = texto.split(/\r?\n/);
      const ini = Math.max(1, d.tipo === "modulo" ? 1 : (d.linea ?? 1));
      const fin = Math.min(lineas.length, d.tipo === "modulo" ? Math.min(lineas.length, 400) : Math.max(ini, d.fin ?? ini), ini + 600);
      const ancho = String(fin).length;
      destino.innerHTML = lineas
        .slice(ini - 1, fin)
        .map((l, i) => {
          const num = ini + i;
          return `<span class="ln${num === resaltar ? " hl" : ""}"><span class="num">${String(num).padStart(ancho, " ")}</span>${resaltarPython(l)}</span>`;
        })
        .join("\n");
      const hl = destino.querySelector<HTMLElement>(".hl");
      if (hl) hl.scrollIntoView({ block: "center" });
    });
  }
}

const RE_PY =
  /(#[^\n]*)|([rbfuRBFU]{0,2}(?:"""|'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'))|\b(def|class|return|if|elif|else|for|while|try|except|finally|with|as|import|from|raise|yield|async|await|lambda|pass|break|continue|in|is|not|and|or|global|nonlocal|assert|del|match|case)\b|\b(None|True|False|self|cls)\b|(\b\d[\d_]*(?:\.\d+)?\b)|(@[\w.]+)/g;

function resaltarPython(linea: string): string {
  let out = "";
  let last = 0;
  for (const m of linea.matchAll(RE_PY)) {
    out += esc(linea.slice(last, m.index));
    const cls = m[1] ? "c" : m[2] ? "s" : m[3] ? "kw" : m[4] ? "k2" : m[5] ? "n" : "d";
    out += `<span class="${cls}">${esc(m[0])}</span>`;
    last = m.index! + m[0].length;
  }
  return out + esc(linea.slice(last));
}
