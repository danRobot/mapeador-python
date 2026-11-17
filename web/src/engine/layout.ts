import type { Arbol, Modelo } from "./model";

export type Direction = "RIGHT" | "DOWN";

/**
 * Layout por capas en O(n) (el de jsoncrack-lite, adaptado al árbol a demanda).
 *
 *  - Se dibuja la "etapa" actual: el subárbol de `root`, hasta `levels` niveles, siguiendo
 *    solo las filas abiertas. Los hijos de una fila se crean aquí la primera vez.
 *  - Eje de profundidad: una columna por nivel, del ancho del nodo más ancho del nivel.
 *  - Eje de apilado: cada subárbol ocupa un bloque contiguo y el padre queda centrado
 *    respecto a sus hijos; cada columna queda ordenada (búsqueda binaria al dibujar).
 */
export interface LayoutOptions {
  root: number;
  levels: number;
  dir: Direction;
}

export interface Layout {
  dir: Direction;
  root: number;
  base: number;
  pos: Float64Array;
  colPos: Float64Array;
  colSize: Float64Array;
  visible: Uint8Array;
  lists: Uint32Array[];
  count: number;
  maxDepth: number;
  stackExtent: number;
  depthExtent: number;
  /** Ancho y alto de cada nodo (por id de nodo). */
  w: Float64Array;
  h: Float64Array;
  /** Índice de la fila del padre de la que sale la arista de cada nodo visible. */
  rowIdx: Int32Array;
}

export const LAYOUT_GAPS = {
  RIGHT: { sibling: 22, column: 90 },
  DOWN: { sibling: 30, column: 70 },
} as const;

export function computeLayout(arbol: Arbol, modelo: Modelo, o: LayoutOptions): Layout {
  const { root, dir } = o;
  const maxRel = o.levels > 0 ? o.levels - 1 : Infinity;
  const base = arbol.depth[root];
  const isRight = dir === "RIGHT";
  const { sibling: SIB, column: COL_GAP } = LAYOUT_GAPS[dir];

  // DFS en pre-orden; los hijos se crean al recorrer (el árbol puede crecer aquí)
  const order: number[] = [];
  const stack: number[] = [root];
  const kids: number[] = [];
  while (stack.length) {
    const n = stack.pop()!;
    order.push(n);
    if (arbol.depth[n] - base >= maxRel) continue;
    kids.length = 0;
    const filas = modelo.filas(arbol.def[n]);
    for (const f of filas) {
      if (!f.destinos.length || !arbol.abierta(n, f.key)) continue;
      for (const c of arbol.hijosDe(n, f)) kids.push(c);
    }
    for (let k = kids.length - 1; k >= 0; k--) stack.push(kids[k]);
  }
  const N = arbol.count;
  const cnt = order.length;
  const visible = new Uint8Array(N);
  const w = new Float64Array(N);
  const h = new Float64Array(N);
  const rowIdx = new Int32Array(N).fill(-1);
  for (const n of order) {
    visible[n] = 1;
    const [ww, hh] = modelo.tam(arbol.def[n]);
    w[n] = ww;
    h[n] = hh;
    const p = arbol.padre[n];
    if (n !== root && p >= 0) rowIdx[n] = modelo.indiceFila(arbol.def[p], arbol.filaPadre[n]);
  }
  const depthSize = isRight ? w : h;
  const stackSize = isRight ? h : w;

  let maxDepth = 0;
  for (const n of order) {
    const d = arbol.depth[n] - base;
    if (d > maxDepth) maxDepth = d;
  }
  const colSize = new Float64Array(maxDepth + 1);
  const perDepth = new Uint32Array(maxDepth + 1);
  for (const n of order) {
    const d = arbol.depth[n] - base;
    if (depthSize[n] > colSize[d]) colSize[d] = depthSize[n];
    perDepth[d]++;
  }
  const colPos = new Float64Array(maxDepth + 1);
  for (let d = 1; d <= maxDepth; d++) colPos[d] = colPos[d - 1] + colSize[d - 1] + COL_GAP;

  // bloques de cada subárbol (de hojas a raíz)
  const blk = new Float64Array(N);
  const acc = new Float64Array(N);
  const nk = new Uint32Array(N);
  for (let i = cnt - 1; i >= 0; i--) {
    const n = order[i];
    let b = stackSize[n];
    if (nk[n] > 0) {
      const s = acc[n] + (nk[n] - 1) * SIB;
      if (s > b) b = s;
    }
    blk[n] = b;
    if (n !== root) {
      const p = arbol.padre[n];
      acc[p] += b;
      nk[p]++;
    }
  }

  // posiciones (de raíz a hojas)
  const pos = new Float64Array(N);
  const cursor = new Float64Array(N);
  for (let i = 0; i < cnt; i++) {
    const n = order[i];
    if (n !== root) {
      const p = arbol.padre[n];
      const b0 = cursor[p];
      pos[n] = b0 + (blk[n] - stackSize[n]) / 2;
      cursor[p] = b0 + blk[n] + SIB;
      cursor[n] = b0;
    } else {
      cursor[n] = 0;
      pos[n] = (blk[n] - stackSize[n]) / 2;
    }
    if (nk[n] > 0) cursor[n] += (blk[n] - (acc[n] + (nk[n] - 1) * SIB)) / 2;
  }
  // en RIGHT, alinear el padre con la fila de la que salen sus hijos se ve mejor si el
  // padre no queda por debajo de su primer hijo: se respeta el centrado de jsoncrack.

  const lists: Uint32Array[] = [];
  const fill = new Uint32Array(maxDepth + 1);
  for (let d = 0; d <= maxDepth; d++) lists.push(new Uint32Array(perDepth[d]));
  for (const n of order) {
    const d = arbol.depth[n] - base;
    lists[d][fill[d]++] = n;
  }

  return {
    dir,
    root,
    base,
    pos,
    colPos,
    colSize,
    visible,
    lists,
    count: cnt,
    maxDepth,
    stackExtent: blk[root],
    depthExtent: colPos[maxDepth] + colSize[maxDepth],
    w,
    h,
    rowIdx,
  };
}

/** Primer índice de `list` cuyo final (pos + tamaño) es >= v. */
export function lowerBoundEnd(list: Uint32Array, pos: Float64Array, size: Float64Array, v: number) {
  let lo = 0;
  let hi = list.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    const n = list[mid];
    if (pos[n] + size[n] < v) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Primer índice de `list` cuya posición es > v. */
export function upperBoundStart(list: Uint32Array, pos: Float64Array, v: number) {
  let lo = 0;
  let hi = list.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (pos[list[mid]] <= v) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}
