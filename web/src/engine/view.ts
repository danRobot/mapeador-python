import { COLLAPSE_W, HEADER_H, PAD_X, ROW_H, destinoEnCamino, textoFila, type Arbol, type Arista, type ColorKey, type Fila, type Modelo } from "./model";
import { DEFAULT_CONFIG } from "../config";
import { computeLayout, lowerBoundEnd, upperBoundStart, type Direction, type Layout } from "./layout";
import { NODE_FONT_FAMILY, themes, type CanvasTheme, type ThemeMode } from "./theme";

export interface Escena {
  modelo: Modelo;
  arbol: Arbol;
}

export interface ViewEvents {
  onSelect?(n: number, row: number): void;
  onViewChange?(): void;
  onLayout?(info: { ms: number; visible: number }): void;
  onStageChange?(): void;
}

export interface Hit {
  n: number;
  /** -1 = cabecera */
  row: number;
  button: boolean;
}

export interface Stage {
  root: number;
  cam: [number, number, number] | null;
}

export type Modo = "flujo" | "estructura";

const FLUJO_AUTO = new Set(["llamada", "crea", "encola", "ruta"]);
const ESTRUCTURA_AUTO = new Set(["submodulo", "clase", "funcion", "main", "nivel", "app"]);
const MAX_AUTO = 700;
/** Filas "nombre: tipo" (las demás se escriben "clave valor"). */
const CON_DOS_PUNTOS = new Set(["param", "atributo", "enum"]);
const DASH: Record<Arista, number[]> = {
  llamada: [],
  crea: [6, 4],
  encola: [10, 4],
  referencia: [2, 4],
  tipo: [2, 3],
  herencia: [],
  subclase: [],
  miembro: [],
  ruta: [],
  lanza: [6, 3],
};

export class GraphView {
  readonly canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private wrap: HTMLElement;
  private events: ViewEvents;

  escena: Escena | null = null;
  layout: Layout | null = null;
  stages: Stage[] = [];
  modo: Modo = "flujo";

  levels = DEFAULT_CONFIG.stageLevels;
  autoFit = DEFAULT_CONFIG.autoFit;
  autoFitMinZoom = DEFAULT_CONFIG.autoFitMinZoom;
  /** Texto buscado (en minúsculas): resalta las filas visibles que lo contienen. */
  consulta = "";
  selected = -1;
  selectedRow = -1;

  private hover = -1;
  private hoverRow = -2;
  private hoverBtn = false;

  camX = 0;
  camY = 0;
  zoom = 1;
  readonly maxZoom = 4;
  minZoom = 0.01;

  private cssW = 0;
  private cssH = 0;
  private dpr = 1;
  themeMode: ThemeMode = "dark";
  theme: CanvasTheme = themes.dark;
  dir: Direction = "RIGHT";
  wheelMode: "zoom" | "pan" = "zoom";
  showGrid = true;
  charW: number;

  private raf = 0;
  private anim: { from: [number, number, number]; to: [number, number, number]; t0: number; dur: number } | null = null;
  private lastGridKey = "";
  lastDrawMs = 0;
  /** Texto que se dibuja encima de la raíz de la etapa (lo fija la UI). */
  stageCaption = "";

  constructor(wrap: HTMLElement, events: ViewEvents = {}) {
    this.wrap = wrap;
    this.events = events;
    this.canvas = document.createElement("canvas");
    this.canvas.className = "graph-canvas";
    wrap.appendChild(this.canvas);
    const ctx = this.canvas.getContext("2d", { alpha: true });
    if (!ctx) throw new Error("Canvas 2D no disponible");
    this.ctx = ctx;
    this.charW = GraphView.measureCharWidth();
    new ResizeObserver(() => this.resize()).observe(wrap);
    this.resize();
    this.bindInput();
  }

  static measureCharWidth(): number {
    const c = document.createElement("canvas").getContext("2d")!;
    c.font = `500 12px ${NODE_FONT_FAMILY}`;
    return c.measureText("M".repeat(100)).width / 100 || 7.2;
  }

  // ---------------------------------------------------------------- estado

  /** Muestra una escena con `root` como raíz de la primera etapa. */
  setEscena(escena: Escena | null, root = -1) {
    this.escena = escena;
    this.layout = null;
    this.selected = -1;
    this.selectedRow = -1;
    this.hover = -1;
    if (!escena || root < 0) {
      this.stages = [];
      this.requestDraw();
      this.events.onStageChange?.();
      return;
    }
    this.stages = [{ root, cam: null }];
    this.relayout();
    this.centerOn(root, -1, 1, false, true);
    this.events.onStageChange?.();
  }

  setTheme(mode: ThemeMode) {
    this.themeMode = mode;
    this.theme = themes[mode];
    this.lastGridKey = "";
    this.requestDraw();
    this.events.onViewChange?.();
  }

  setDirection(dir: Direction) {
    if (dir === this.dir) return;
    this.dir = dir;
    if (!this.escena) return;
    const anchor = this.selected >= 0 ? this.selected : this.stageRoot;
    this.relayout();
    this.centerOn(anchor, -1, this.zoom, false);
  }

  setShowGrid(v: boolean) {
    this.showGrid = v;
    this.lastGridKey = "";
    this.requestDraw();
  }

  setLevels(levels: number) {
    this.levels = levels === 0 ? 0 : Math.max(2, Math.floor(levels));
    if (!this.escena) return;
    this.relayout(this.selected >= 0 ? this.selected : this.stageRoot);
    this.events.onStageChange?.();
  }

  get stageRoot() {
    return this.stages.length ? this.stages[this.stages.length - 1].root : -1;
  }

  relayout(anchor = -1) {
    const esc = this.escena;
    if (!esc || this.stageRoot < 0) return;
    let ax = 0;
    let ay = 0;
    const hasAnchor = anchor >= 0 && this.layout && anchor < this.layout.visible.length && this.layout.visible[anchor];
    if (hasAnchor) {
      ax = (this.nodeX(anchor) - this.camX) * this.zoom;
      ay = (this.nodeY(anchor) - this.camY) * this.zoom;
    }
    const t0 = performance.now();
    this.layout = computeLayout(esc.arbol, esc.modelo, { root: this.stageRoot, levels: this.levels, dir: this.dir });
    const ms = performance.now() - t0;
    if (hasAnchor && this.layout.visible[anchor]) {
      this.camX = this.nodeX(anchor) - ax / this.zoom;
      this.camY = this.nodeY(anchor) - ay / this.zoom;
    }
    if (this.selected >= 0 && !this.isVisible(this.selected)) {
      this.selected = -1;
      this.selectedRow = -1;
      this.events.onSelect?.(-1, -1);
    }
    this.hover = -1;
    this.updateMinZoom();
    this.events.onLayout?.({ ms, visible: this.layout.count });
    this.requestDraw();
    this.events.onViewChange?.();
  }

  isVisible(n: number) {
    const L = this.layout;
    return !!L && n >= 0 && n < L.visible.length && L.visible[n] === 1;
  }

  private updateMinZoom() {
    const L = this.layout;
    if (!L) return;
    const w = this.dir === "RIGHT" ? L.depthExtent : L.stackExtent;
    const h = this.dir === "RIGHT" ? L.stackExtent : L.depthExtent;
    const fit = Math.min(this.cssW / Math.max(1, w), this.cssH / Math.max(1, h));
    this.minZoom = Math.max(1e-4, Math.min(0.1, fit * 0.5));
  }

  nodeX(n: number) {
    const L = this.layout!;
    return L.dir === "RIGHT" ? L.colPos[this.escena!.arbol.depth[n] - L.base] : L.pos[n];
  }
  nodeY(n: number) {
    const L = this.layout!;
    return L.dir === "RIGHT" ? L.pos[n] : L.colPos[this.escena!.arbol.depth[n] - L.base];
  }

  filasDe(n: number): Fila[] {
    const esc = this.escena!;
    return esc.modelo.filas(esc.arbol.def[n]);
  }

  /** Nivel del nodo dentro de la etapa actual (la raíz es el 1). */
  stageLevel(n: number) {
    const a = this.escena!.arbol;
    return a.depth[n] - a.depth[this.stageRoot] + 1;
  }

  isDrillNode(n: number) {
    return this.levels > 0 && this.stageLevel(n) >= this.levels;
  }

  toggleRow(n: number, row: number) {
    const esc = this.escena;
    if (!esc) return;
    const f = this.filasDe(n)[row];
    if (!f || !f.destinos.length) return;
    if (this.isDrillNode(n)) {
      esc.arbol.abrir(n, f.key, true);
      const hijos = esc.arbol.hijosDe(n, f);
      this.enterStage(hijos.length === 1 ? hijos[0] : n);
      return;
    }
    esc.arbol.abrir(n, f.key, !esc.arbol.abierta(n, f.key));
    this.relayout(n);
    this.autoFitAround(n);
  }

  // ---------------------------------------------------------------- etapas

  private saveStage() {
    const s = this.stages[this.stages.length - 1];
    if (s) s.cam = [this.camX, this.camY, this.zoom];
  }

  /** Abre una etapa nueva con `root` como raíz (un nodo del árbol, o una raíz suelta). */
  enterStage(root: number) {
    if (!this.escena) return;
    if (root === this.stageRoot) {
      this.relayout(root);
      this.autoFitAround(root);
      return;
    }
    this.saveStage();
    this.stages.push({ root, cam: null });
    this.selected = -1;
    this.selectedRow = -1;
    this.events.onSelect?.(-1, -1);
    this.relayout();
    this.centerOn(root, -1, 1, false, true);
    this.autoFitAround(root, false);
    this.events.onStageChange?.();
  }

  goToStage(i: number) {
    if (!this.escena || i < 0 || i >= this.stages.length - 1) return;
    const from = this.stageRoot;
    this.stages.length = i + 1;
    const s = this.stages[i];
    this.selected = -1;
    this.relayout();
    if (s.cam) {
      this.camX = s.cam[0];
      this.camY = s.cam[1];
      this.zoom = s.cam[2];
    } else this.centerOn(s.root, -1, 1, false, true);
    // marca el nodo del que veníamos (o su antecesor visible)
    let x = from;
    const a = this.escena.arbol;
    while (x >= 0 && !this.isVisible(x)) x = a.padre[x];
    if (x >= 0) {
      this.selected = x;
      if (!this.isNodeInView(x)) this.centerOn(x, -1, Math.max(this.zoom, 0.8), false);
    }
    this.requestDraw();
    this.events.onViewChange?.();
    this.events.onStageChange?.();
  }

  goBack() {
    this.goToStage(this.stages.length - 2);
  }

  /** Hermano anterior / siguiente de la raíz de la etapa que cuelga de la misma fila
   * (p. ej. las implementaciones de un método polimórfico). */
  stageSibling(delta: number): number {
    const esc = this.escena;
    if (!esc || this.stages.length < 2) return -1;
    const a = esc.arbol;
    const r = this.stageRoot;
    const p = a.padre[r];
    if (p < 0) return -1;
    const hermanos = a.hijosCreados(p, a.filaPadre[r]) ?? [];
    const i = hermanos.indexOf(r);
    const j = i + delta;
    return i >= 0 && j >= 0 && j < hermanos.length ? hermanos[j] : -1;
  }

  switchStageRoot(sibling: number) {
    if (!this.escena || sibling < 0 || this.stages.length < 2) return;
    this.stages[this.stages.length - 1] = { root: sibling, cam: null };
    this.selected = -1;
    this.events.onSelect?.(-1, -1);
    this.relayout();
    this.centerOn(sibling, -1, this.zoom, false, true);
    this.autoFitAround(sibling, false);
    this.events.onStageChange?.();
  }

  private isNodeInView(n: number) {
    const L = this.layout!;
    const x = (this.nodeX(n) - this.camX) * this.zoom;
    const y = (this.nodeY(n) - this.camY) * this.zoom;
    return x + L.w[n] * this.zoom > 0 && y + L.h[n] * this.zoom > 0 && x < this.cssW && y < this.cssH;
  }

  private isAncestorOrSelf(a: number, n: number) {
    const arbol = this.escena!.arbol;
    for (let x = n; x >= 0; x = arbol.padre[x]) if (x === a) return true;
    return false;
  }

  /**
   * Deja visible el nodo `target`: abre las filas del camino y, si hace falta, sube por el
   * breadcrumb o abre etapas intermedias. Devuelve true si cambió de etapa.
   */
  navigateTo(target: number): boolean {
    const esc = this.escena;
    if (!esc || target < 0) return false;
    const a = esc.arbol;
    const startLen = this.stages.length;
    const startRoot = this.stageRoot;
    let i = this.stages.length - 1;
    while (i > 0 && !this.isAncestorOrSelf(this.stages[i].root, target)) i--;
    if (!this.isAncestorOrSelf(this.stages[i].root, target)) {
      // raíz suelta (definición que no se alcanza desde la entrada): etapa propia
      let r = target;
      while (a.padre[r] >= 0) r = a.padre[r];
      this.saveStage();
      this.stages.push({ root: r, cam: null });
      i = this.stages.length - 1;
    } else if (i < this.stages.length - 1) this.stages.length = i + 1;
    const step = this.levels > 0 ? this.levels - 1 : Infinity;
    let root = this.stages[i].root;
    while (a.depth[target] - a.depth[root] > step) {
      let nr = target;
      const want = a.depth[root] + step;
      while (a.depth[nr] > want) nr = a.padre[nr];
      this.openPath(root, nr);
      this.saveStage();
      this.stages.push({ root: nr, cam: null });
      root = nr;
    }
    this.openPath(root, target);
    this.relayout();
    const changed = this.stages.length !== startLen || this.stageRoot !== startRoot;
    if (changed) this.events.onStageChange?.();
    return changed;
  }

  private openPath(root: number, n: number) {
    const a = this.escena!.arbol;
    while (n !== root && n >= 0) {
      const p = a.padre[n];
      if (p < 0) break;
      a.abrir(p, a.filaPadre[n], true);
      n = p;
    }
  }

  /** Expande la etapa (hasta el límite de niveles) siguiendo el flujo o la estructura. */
  expandStage() {
    const esc = this.escena;
    if (!esc || this.stageRoot < 0) return;
    const { arbol, modelo } = esc;
    const auto = this.modo === "flujo" ? FLUJO_AUTO : ESTRUCTURA_AUTO;
    const maxRel = this.levels > 0 ? this.levels - 1 : 12;
    const base = arbol.depth[this.stageRoot];
    let cola = [this.stageRoot];
    let total = 1;
    while (cola.length && total < MAX_AUTO) {
      const sig: number[] = [];
      for (const n of cola) {
        if (arbol.depth[n] - base >= maxRel || arbol.ciclo[n]) continue;
        for (const f of modelo.filas(arbol.def[n])) {
          if (!f.destinos.length || !auto.has(f.kind)) continue;
          if (f.destinos.every(d => arbol.esAncestroDef(n, d))) continue;
          arbol.abrir(n, f.key, true);
          for (const c of arbol.hijosDe(n, f)) {
            sig.push(c);
            total++;
          }
          if (total >= MAX_AUTO) break;
        }
        if (total >= MAX_AUTO) break;
      }
      cola = sig;
    }
    this.relayout();
    const L = this.layout!;
    const w = this.dir === "RIGHT" ? L.depthExtent : L.stackExtent;
    const h = this.dir === "RIGHT" ? L.stackExtent : L.depthExtent;
    const fit = Math.min((this.cssW - 80) / Math.max(1, w), (this.cssH - 80) / Math.max(1, h));
    if (fit >= 0.4) this.fitView(true);
    else this.frameAround(this.stageRoot, true);
  }

  collapseStage() {
    const esc = this.escena;
    if (!esc || !this.layout) return;
    const L = this.layout;
    for (const list of L.lists) for (const n of list) esc.arbol.cerrarTodo(n);
    this.relayout();
    this.centerOn(this.stageRoot, -1, 1, true, true);
  }

  autoFitAround(n: number, animate = true) {
    if (this.autoFit) this.frameAround(n, animate);
  }

  /** Encuadre legible: toda la etapa si cabe con zoom >= autoFitMinZoom; si no, `n` y sus hijos. */
  frameAround(n: number, animate = true) {
    const L = this.layout;
    const esc = this.escena;
    if (!L || !esc || !this.isVisible(n)) return;
    const a = esc.arbol;
    const pad = 50;
    const W = this.cssW;
    const H = this.cssH;
    const w = this.dir === "RIGHT" ? L.depthExtent : L.stackExtent;
    const h = this.dir === "RIGHT" ? L.stackExtent : L.depthExtent;
    const minZ = Math.min(1, this.autoFitMinZoom);
    const fitZ = Math.min((W - 2 * pad) / Math.max(1, w), (H - 2 * pad) / Math.max(1, h));
    if (fitZ >= minZ) {
      this.lookAt(w / 2, h / 2, Math.min(1, fitZ), animate);
      return;
    }
    let x0 = this.nodeX(n);
    let y0 = this.nodeY(n);
    let x1 = x0 + L.w[n];
    let y1 = y0 + L.h[n];
    for (const list of L.lists) {
      for (const c of list) {
        if (a.padre[c] !== n) continue;
        const cx = this.nodeX(c);
        const cy = this.nodeY(c);
        x0 = Math.min(x0, cx);
        y0 = Math.min(y0, cy);
        x1 = Math.max(x1, cx + L.w[c]);
        y1 = Math.max(y1, cy + L.h[c]);
      }
    }
    const bw = x1 - x0;
    const bh = y1 - y0;
    const z = Math.min(1, Math.max(minZ, Math.min((W - 2 * pad) / bw, (H - 2 * pad) / bh)));
    let cx = (x0 + x1) / 2;
    let cy = (y0 + y1) / 2;
    if (bw * z > W - 2 * pad) cx = this.dir === "RIGHT" ? x0 - pad / z + W / 2 / z : this.nodeX(n) + L.w[n] / 2;
    if (bh * z > H - 2 * pad) cy = this.dir === "RIGHT" ? this.nodeY(n) + Math.min(L.h[n], 300) / 2 : y0 - pad / z + H / 2 / z;
    this.lookAt(cx, cy, z, animate);
  }

  select(n: number, row = -1) {
    this.selected = n;
    this.selectedRow = row;
    this.requestDraw();
  }

  // ---------------------------------------------------------------- cámara

  private resize() {
    const r = this.wrap.getBoundingClientRect();
    this.cssW = Math.max(1, r.width);
    this.cssH = Math.max(1, r.height);
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(this.cssW * this.dpr);
    this.canvas.height = Math.round(this.cssH * this.dpr);
    this.canvas.style.width = `${this.cssW}px`;
    this.canvas.style.height = `${this.cssH}px`;
    this.updateMinZoom();
    this.lastGridKey = "";
    this.drawNow();
    this.events.onViewChange?.();
  }

  get viewportSize() {
    return { w: this.cssW, h: this.cssH };
  }

  private clampZoom(z: number) {
    return Math.min(this.maxZoom, Math.max(this.minZoom, z));
  }

  zoomAt(factor: number, sx = this.cssW / 2, sy = this.cssH / 2) {
    const z = this.clampZoom(this.zoom * factor);
    const wx = this.camX + sx / this.zoom;
    const wy = this.camY + sy / this.zoom;
    this.zoom = z;
    this.camX = wx - sx / z;
    this.camY = wy - sy / z;
    this.anim = null;
    this.requestDraw();
    this.events.onViewChange?.();
  }

  setZoom(z: number) {
    this.zoomAt(z / this.zoom);
  }

  panBy(dx: number, dy: number) {
    this.camX -= dx / this.zoom;
    this.camY -= dy / this.zoom;
    this.anim = null;
    this.requestDraw();
    this.events.onViewChange?.();
  }

  lookAt(wx: number, wy: number, zoom = this.zoom, animate = false) {
    const z = this.clampZoom(zoom);
    this.moveCamera(wx - this.cssW / 2 / z, wy - this.cssH / 2 / z, z, animate);
  }

  private moveCamera(x: number, y: number, z: number, animate: boolean) {
    if (!animate) {
      this.camX = x;
      this.camY = y;
      this.zoom = z;
      this.anim = null;
      this.requestDraw();
      this.events.onViewChange?.();
      return;
    }
    this.anim = { from: [this.camX, this.camY, this.zoom], to: [x, y, z], t0: performance.now(), dur: 320 };
    this.requestDraw();
  }

  fitView(animate = true) {
    const L = this.layout;
    if (!L) return;
    const w = this.dir === "RIGHT" ? L.depthExtent : L.stackExtent;
    const h = this.dir === "RIGHT" ? L.stackExtent : L.depthExtent;
    const pad = 40;
    const z = this.clampZoom(Math.min(1.2, Math.min((this.cssW - pad * 2) / Math.max(1, w), (this.cssH - pad * 2) / Math.max(1, h))));
    this.lookAt(w / 2, h / 2, z, animate);
  }

  focusRoot(animate = true) {
    if (!this.layout) return;
    this.centerOn(this.stageRoot, -1, Math.max(this.zoom, 0.9), animate, true);
  }

  centerOn(n: number, row = -1, zoom = this.zoom, animate = true, alignStart = false) {
    const L = this.layout;
    if (!L || !this.isVisible(n)) return;
    const x = this.nodeX(n);
    const y = this.nodeY(n);
    const z = this.clampZoom(zoom);
    let cy = y + Math.min(L.h[n], this.cssH / z * 0.8) / 2;
    if (row >= 0) cy = y + HEADER_H + row * ROW_H + ROW_H / 2;
    let cx = x + L.w[n] / 2;
    if (alignStart) {
      if (this.dir === "RIGHT") {
        cx = x - 60 / z + this.cssW / 2 / z;
        if (row < 0) cy = y - 70 / z + this.cssH / 2 / z;
      } else cy = y - 60 / z + this.cssH / 2 / z;
    }
    this.lookAt(cx, cy, z, animate);
  }

  // ---------------------------------------------------------------- hit test

  hitTest(sx: number, sy: number): Hit | null {
    const L = this.layout;
    if (!this.escena || !L) return null;
    const wx = this.camX + sx / this.zoom;
    const wy = this.camY + sy / this.zoom;
    const isRight = L.dir === "RIGHT";
    const da = isRight ? wx : wy;
    const sa = isRight ? wy : wx;
    const depthSize = isRight ? L.w : L.h;
    const stackSize = isRight ? L.h : L.w;
    let lo = 0;
    let hi = L.maxDepth + 1;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (L.colPos[mid] <= da) lo = mid + 1;
      else hi = mid;
    }
    const d = lo - 1;
    if (d < 0 || da > L.colPos[d] + L.colSize[d]) return null;
    const list = L.lists[d];
    const i = upperBoundStart(list, L.pos, sa) - 1;
    if (i < 0) return null;
    const n = list[i];
    if (sa > L.pos[n] + stackSize[n] || da > L.colPos[d] + depthSize[n]) return null;
    const x0 = this.nodeX(n);
    const y0 = this.nodeY(n);
    if (wy < y0 + HEADER_H) return { n, row: -1, button: false };
    const filas = this.filasDe(n);
    const row = Math.min(filas.length - 1, Math.max(0, Math.floor((wy - y0 - HEADER_H) / ROW_H)));
    const rowCy = y0 + HEADER_H + row * ROW_H + ROW_H / 2;
    let button = false;
    if (wx >= x0 + PAD_X - 4 && wx <= x0 + PAD_X + 18 && Math.abs(wy - rowCy) <= 10) button = !!filas[row]?.destinos.length;
    return { n, row: filas.length ? row : -1, button };
  }

  // ---------------------------------------------------------------- entrada

  private bindInput() {
    const el = this.canvas;
    const pointers = new Map<number, { x: number; y: number }>();
    let downX = 0;
    let downY = 0;
    let moved = false;
    let pinchDist = 0;
    const local = (e: PointerEvent | MouseEvent | WheelEvent) => {
      const r = el.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };
    el.addEventListener("pointerdown", e => {
      if (e.button !== 0 && e.pointerType === "mouse") return;
      el.setPointerCapture(e.pointerId);
      const p = local(e);
      pointers.set(e.pointerId, p);
      if (pointers.size === 1) {
        downX = p.x;
        downY = p.y;
        moved = false;
      } else if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinchDist = Math.hypot(a.x - b.x, a.y - b.y);
        moved = true;
      }
      this.anim = null;
    });
    el.addEventListener("pointermove", e => {
      const p = local(e);
      if (!pointers.has(e.pointerId)) {
        this.updateHover(p.x, p.y);
        return;
      }
      const prev = pointers.get(e.pointerId)!;
      pointers.set(e.pointerId, p);
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        if (pinchDist > 0) this.zoomAt(dist / pinchDist, (a.x + b.x) / 2, (a.y + b.y) / 2);
        pinchDist = dist;
        this.panBy((p.x - prev.x) / 2, (p.y - prev.y) / 2);
        return;
      }
      if (!moved && Math.hypot(p.x - downX, p.y - downY) > 3) {
        moved = true;
        this.wrap.classList.add("dragging");
      }
      if (moved) this.panBy(p.x - prev.x, p.y - prev.y);
    });
    const end = (e: PointerEvent) => {
      if (!pointers.has(e.pointerId)) return;
      pointers.delete(e.pointerId);
      if (pointers.size > 0) return;
      this.wrap.classList.remove("dragging");
      if (!moved && e.type === "pointerup") {
        const p = local(e);
        const hit = this.hitTest(p.x, p.y);
        if (hit?.button) {
          this.toggleRow(hit.n, hit.row);
          this.updateHover(p.x, p.y);
        } else if (hit) {
          this.select(hit.n, hit.row);
          this.events.onSelect?.(hit.n, hit.row);
        } else if (this.selected !== -1) {
          this.select(-1);
          this.events.onSelect?.(-1, -1);
        }
      }
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("pointerleave", () => {
      if (pointers.size === 0 && this.hover !== -1) {
        this.hover = -1;
        this.requestDraw();
      }
    });
    el.addEventListener("dblclick", e => {
      const p = local(e);
      const hit = this.hitTest(p.x, p.y);
      if (hit && !hit.button) this.centerOn(hit.n, hit.row, Math.max(1, this.zoom), true);
    });
    el.addEventListener(
      "wheel",
      e => {
        e.preventDefault();
        const p = local(e);
        let dx = e.deltaX;
        let dy = e.deltaY;
        if (e.deltaMode === 1) {
          dx *= 16;
          dy *= 16;
        } else if (e.deltaMode === 2) {
          dx *= this.cssW;
          dy *= this.cssH;
        }
        if (e.ctrlKey || e.metaKey) this.zoomAt(Math.exp(-dy * 0.01), p.x, p.y);
        else if (this.wheelMode === "zoom") {
          if (e.shiftKey) this.panBy(-dy, 0);
          else if (Math.abs(dx) > Math.abs(dy)) this.panBy(-dx, 0);
          else this.zoomAt(Math.exp(-dy * 0.0018), p.x, p.y);
        } else if (e.shiftKey) this.panBy(-dy, -dx);
        else this.panBy(-dx, -dy);
        this.updateHover(p.x, p.y);
      },
      { passive: false }
    );
  }

  /** Texto de ayuda de una fila (tooltip). */
  tooltip: (n: number, row: number, button: boolean) => string = () => "";

  private updateHover(sx: number, sy: number) {
    const hit = this.hitTest(sx, sy);
    const n = hit ? hit.n : -1;
    const row = hit ? hit.row : -2;
    const btn = !!hit?.button;
    this.canvas.style.cursor = n >= 0 ? "pointer" : "";
    const title = n >= 0 ? this.tooltip(n, row, btn) : "";
    if (this.canvas.title !== title) this.canvas.title = title;
    if (n !== this.hover || row !== this.hoverRow || btn !== this.hoverBtn) {
      this.hover = n;
      this.hoverRow = row;
      this.hoverBtn = btn;
      this.requestDraw();
    }
  }

  // ---------------------------------------------------------------- dibujo

  requestDraw() {
    if (this.raf) return;
    this.raf = requestAnimationFrame(() => {
      this.raf = 0;
      this.drawNow();
    });
  }

  private stepAnimation() {
    const a = this.anim;
    if (!a) return;
    const t = Math.min(1, (performance.now() - a.t0) / a.dur);
    const k = 1 - Math.pow(1 - t, 3);
    const lz0 = Math.log(a.from[2]);
    const lz1 = Math.log(a.to[2]);
    const z = Math.exp(lz0 + (lz1 - lz0) * k);
    const c0x = a.from[0] + this.cssW / 2 / a.from[2];
    const c0y = a.from[1] + this.cssH / 2 / a.from[2];
    const c1x = a.to[0] + this.cssW / 2 / a.to[2];
    const c1y = a.to[1] + this.cssH / 2 / a.to[2];
    this.zoom = z;
    this.camX = c0x + (c1x - c0x) * k - this.cssW / 2 / z;
    this.camY = c0y + (c1y - c0y) * k - this.cssH / 2 / z;
    if (t >= 1) {
      this.camX = a.to[0];
      this.camY = a.to[1];
      this.zoom = a.to[2];
      this.anim = null;
    }
    this.events.onViewChange?.();
  }

  private updateGrid() {
    const z = this.zoom;
    let minor = 20 * z;
    while (minor < 10) minor *= 5;
    const major = minor * 5;
    const ox = -this.camX * z;
    const oy = -this.camY * z;
    const mx = ((ox % major) + major) % major;
    const my = ((oy % major) + major) % major;
    const nx = ((ox % minor) + minor) % minor;
    const ny = ((oy % minor) + minor) % minor;
    const key = `${this.showGrid}|${this.themeMode}|${minor.toFixed(3)}|${mx.toFixed(1)}|${my.toFixed(1)}|${nx.toFixed(1)}|${ny.toFixed(1)}`;
    if (key === this.lastGridKey) return;
    this.lastGridKey = key;
    const s = this.wrap.style;
    s.backgroundColor = this.theme.bg;
    if (!this.showGrid) {
      s.backgroundImage = "none";
      return;
    }
    const p = this.theme.gridPrimary;
    const q = this.theme.gridSecondary;
    s.backgroundImage = `linear-gradient(${p} 1.5px, transparent 1.5px), linear-gradient(90deg, ${p} 1.5px, transparent 1.5px), linear-gradient(${q} 1px, transparent 1px), linear-gradient(90deg, ${q} 1px, transparent 1px)`;
    s.backgroundSize = `${major}px ${major}px, ${major}px ${major}px, ${minor}px ${minor}px, ${minor}px ${minor}px`;
    s.backgroundPosition = `${mx - 1.5}px ${my - 1.5}px, ${mx - 1.5}px ${my - 1.5}px, ${nx - 1}px ${ny - 1}px, ${nx - 1}px ${ny - 1}px`;
  }

  drawNow() {
    const t0 = performance.now();
    if (this.anim) {
      this.stepAnimation();
      if (this.anim) this.requestDraw();
    }
    this.updateGrid();
    const ctx = this.ctx;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.cssW, this.cssH);
    if (this.escena && this.layout) this.drawGraph();
    this.lastDrawMs = performance.now() - t0;
  }

  /** Dibuja la vista actual sobre un canvas (para exportar PNG). */
  renderToCanvas(): HTMLCanvasElement {
    const out = document.createElement("canvas");
    out.width = this.canvas.width;
    out.height = this.canvas.height;
    const c = out.getContext("2d")!;
    c.fillStyle = this.theme.bg;
    c.fillRect(0, 0, out.width, out.height);
    this.drawNow();
    c.drawImage(this.canvas, 0, 0);
    return out;
  }

  private color(k: ColorKey): string {
    const th = this.theme;
    if (k.startsWith("ext:")) return th.ext[k.slice(4)] ?? th.verbo;
    return (th as unknown as Record<string, string>)[k] ?? th.value;
  }

  private visibleNodes(): number[] {
    const L = this.layout!;
    const isRight = L.dir === "RIGHT";
    const stackSize = isRight ? L.h : L.w;
    const camD = isRight ? this.camX : this.camY;
    const camS = isRight ? this.camY : this.camX;
    const vd0 = camD;
    const vd1 = camD + (isRight ? this.cssW : this.cssH) / this.zoom;
    const vs0 = camS;
    const vs1 = camS + (isRight ? this.cssH : this.cssW) / this.zoom;
    const out: number[] = [];
    for (let d = 0; d <= L.maxDepth; d++) {
      const c0 = L.colPos[d];
      if (c0 > vd1 || c0 + L.colSize[d] < vd0) continue;
      const list = L.lists[d];
      const lo = lowerBoundEnd(list, L.pos, stackSize, vs0);
      const hi = upperBoundStart(list, L.pos, vs1);
      for (let i = lo; i < hi; i++) out.push(list[i]);
    }
    return out;
  }

  private drawGraph() {
    const L = this.layout!;
    const esc = this.escena!;
    const a = esc.arbol;
    const ctx = this.ctx;
    const th = this.theme;
    const z = this.zoom;
    const textMode = 12 * z >= 4.5;
    const skeleton = !textMode && ROW_H * z >= 2.5;
    const isRight = L.dir === "RIGHT";

    // ---- aristas (todas las visibles: los árboles son de cientos o pocos miles de nodos)
    const caminos = new Map<Arista, Path2D>();
    const flechas: { x: number; y: number; arista: Arista; inicio?: boolean }[] = [];
    const activas = new Path2D();
    const sel = this.selected;
    for (let d = 1; d <= L.maxDepth; d++) {
      const list = L.lists[d];
      for (let i = 0; i < list.length; i++) {
        const c = list[i];
        const p = a.padre[c];
        if (p < 0) continue;
        const f = this.filasDe(p)[L.rowIdx[c]];
        const arista: Arista = f?.arista ?? "llamada";
        let path = caminos.get(arista);
        if (!path) caminos.set(arista, (path = new Path2D()));
        const pts = this.edgePoints(c, p, d);
        if (!pts) continue;
        const [x1, y1, x2, y2] = pts;
        const ruta = c === sel || p === sel ? activas : path;
        if (isRight) {
          const mx = (x1 + x2) / 2;
          ruta.moveTo(x1, y1);
          ruta.bezierCurveTo(mx, y1, mx, y2, x2, y2);
        } else {
          const my = (y1 + y2) / 2;
          ruta.moveTo(x1, y1);
          ruta.bezierCurveTo(x1, my, x2, my, x2, y2);
        }
        if (arista === "subclase") flechas.push({ x: x1, y: y1, arista, inicio: true });
        else if (arista !== "miembro") flechas.push({ x: x2, y: y2, arista });
      }
    }
    const lw = Math.max(0.6, Math.min(1.5, 1.5 * z));
    ctx.lineWidth = lw;
    for (const [arista, path] of caminos) {
      ctx.strokeStyle = arista === "lanza" ? th.lanza : th.edge;
      ctx.globalAlpha = arista === "miembro" ? 0.6 : 1;
      ctx.setLineDash(DASH[arista].map(x => x * Math.max(0.5, z)));
      ctx.stroke(path);
    }
    ctx.globalAlpha = 1;
    ctx.setLineDash([]);
    ctx.strokeStyle = th.edgeActive;
    ctx.lineWidth = Math.max(1, Math.min(2.2, 2 * z));
    ctx.stroke(activas);
    if (z > 0.25) this.drawArrows(flechas, isRight);

    // ---- nodos
    const vis = this.visibleNodes();
    const radius = Math.min(5, 4 * z);
    for (const n of vis) {
      const sx = (this.nodeX(n) - this.camX) * z;
      const sy = (this.nodeY(n) - this.camY) * z;
      const sw = L.w[n] * z;
      const sh = L.h[n] * z;
      ctx.beginPath();
      if (radius > 0.5 && sw > 4) ctx.roundRect(sx, sy, sw, sh, radius);
      else ctx.rect(sx, sy, sw, sh);
      ctx.fillStyle = th.nodeFill;
      ctx.fill();
      // cabecera
      const hh = HEADER_H * z;
      ctx.beginPath();
      if (radius > 0.5 && sw > 4) ctx.roundRect(sx, sy, sw, hh, [radius, radius, 0, 0]);
      else ctx.rect(sx, sy, sw, hh);
      ctx.fillStyle = th.headerFill;
      ctx.fill();
      if (n === this.selected) {
        ctx.fillStyle = th.selectedFill;
        ctx.fillRect(sx, sy, sw, sh);
      }
      ctx.beginPath();
      if (radius > 0.5 && sw > 4) ctx.roundRect(sx, sy, sw, sh, radius);
      else ctx.rect(sx, sy, sw, sh);
      ctx.lineWidth = this.themeMode === "plantuml" ? 1.2 : 1;
      ctx.strokeStyle = th.nodeStroke;
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(sx, sy + hh);
      ctx.lineTo(sx + sw, sy + hh);
      ctx.stroke();
      if (textMode) this.drawHeader(n, sx, sy, sw);
      else if (skeleton) {
        ctx.globalAlpha = 0.5;
        ctx.fillStyle = th.title;
        ctx.fillRect(sx + 30 * z, sy + 12 * z, Math.min(sw - 40 * z, 120 * z), 8 * z);
        ctx.globalAlpha = 1;
      }
      if (textMode || skeleton) this.drawRows(n, sx, sy, sw, textMode);
    }

    // contornos de hover / selección
    if (this.hover >= 0 && this.isVisible(this.hover) && this.hover !== this.selected) this.outline(this.hover, th.nodeHover, 1, radius);
    if (this.selected >= 0 && this.isVisible(this.selected)) this.outline(this.selected, th.nodeHover, 2, radius);

    // rótulo con el camino sobre la raíz de una etapa interior
    if (textMode && this.stages.length > 1 && this.stageCaption) {
      const r = L.root;
      const sx = (this.nodeX(r) - this.camX) * z;
      const sy = (this.nodeY(r) - this.camY) * z;
      ctx.font = `500 ${11 * z}px ${NODE_FONT_FAMILY}`;
      ctx.textBaseline = "bottom";
      ctx.fillStyle = th.interactive;
      ctx.globalAlpha = 0.85;
      ctx.fillText(this.stageCaption, sx, sy - 6 * z, Math.max(260 * z, L.w[r] * z * 1.8));
      ctx.globalAlpha = 1;
      ctx.textBaseline = "middle";
    }
  }

  private edgePoints(c: number, p: number, d: number): [number, number, number, number] | null {
    const L = this.layout!;
    const z = this.zoom;
    const row = L.rowIdx[c];
    if (L.dir === "RIGHT") {
      const x1 = (L.colPos[d - 1] + L.w[p] - this.camX) * z;
      const y1 = (L.pos[p] + (row >= 0 ? HEADER_H + row * ROW_H + ROW_H / 2 : HEADER_H / 2) - this.camY) * z;
      const x2 = (L.colPos[d] - this.camX) * z;
      const y2 = (L.pos[c] + HEADER_H / 2 - this.camY) * z;
      return [x1, y1, x2, y2];
    }
    const x1 = (L.pos[p] + L.w[p] / 2 - this.camX) * z;
    const y1 = (L.colPos[d - 1] + L.h[p] - this.camY) * z;
    const x2 = (L.pos[c] + L.w[c] / 2 - this.camX) * z;
    const y2 = (L.colPos[d] - this.camY) * z;
    return [x1, y1, x2, y2];
  }

  private drawArrows(flechas: { x: number; y: number; arista: Arista; inicio?: boolean }[], isRight: boolean) {
    const ctx = this.ctx;
    const th = this.theme;
    const s = Math.max(4, Math.min(9, 8 * this.zoom));
    const llenas = new Path2D();
    const huecas = new Path2D();
    const rojas = new Path2D();
    for (const f of flechas) {
      const hueca = f.arista === "herencia" || f.arista === "subclase";
      const p = f.arista === "lanza" ? rojas : hueca ? huecas : llenas;
      const k = hueca ? 1.25 : 1;
      // punta hacia la derecha (RIGHT) o hacia abajo (DOWN); en "subclase" la punta va al padre
      const dir = f.inicio ? -1 : 1;
      if (isRight) {
        const x = f.inicio ? f.x : f.x;
        p.moveTo(x, f.y);
        p.lineTo(x - dir * s * k, f.y - (s * k) / 2);
        p.lineTo(x - dir * s * k, f.y + (s * k) / 2);
        p.closePath();
      } else {
        p.moveTo(f.x, f.y);
        p.lineTo(f.x - (s * k) / 2, f.y - dir * s * k);
        p.lineTo(f.x + (s * k) / 2, f.y - dir * s * k);
        p.closePath();
      }
    }
    ctx.fillStyle = th.edge;
    ctx.fill(llenas);
    ctx.fillStyle = th.lanza;
    ctx.fill(rojas);
    ctx.fillStyle = th.nodeFill;
    ctx.fill(huecas);
    ctx.strokeStyle = th.edge;
    ctx.lineWidth = 1.2;
    ctx.stroke(huecas);
  }

  private drawHeader(n: number, sx: number, sy: number, sw: number) {
    const ctx = this.ctx;
    const th = this.theme;
    const z = this.zoom;
    const esc = this.escena!;
    const cab = esc.modelo.cabecera(esc.arbol.def[n]);
    const cx = sx + 17 * z;
    const cy = sy + 15 * z;
    const r = 9 * z;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = th.stereo[cab.estereo] ?? th.stereo.clase;
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = this.themeMode === "plantuml" ? th.nodeStroke : "rgba(0,0,0,0.35)";
    ctx.stroke();
    ctx.fillStyle = th.stereoText;
    ctx.font = `700 ${11 * z}px ${NODE_FONT_FAMILY}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(cab.letra, cx, cy + 0.5 * z);
    ctx.textAlign = "left";
    let x = sx + 32 * z;
    const maxX = sx + sw - 8 * z;
    ctx.font = `600 ${12.5 * z}px ${NODE_FONT_FAMILY}`;
    if (cab.prefijo) {
      ctx.fillStyle = th.sub;
      ctx.fillText(cab.prefijo, x, cy, Math.max(1, maxX - x));
      x += ctx.measureText(cab.prefijo).width;
    }
    ctx.fillStyle = th.title;
    const ciclo = esc.arbol.ciclo[n];
    const titulo = cab.titulo + (ciclo ? "  ↻" : "");
    ctx.fillText(titulo, x, cy, Math.max(1, maxX - x));
    ctx.font = `400 ${10 * z}px ${NODE_FONT_FAMILY}`;
    ctx.fillStyle = th.sub;
    ctx.fillText(cab.sub, sx + 32 * z, sy + 31 * z, Math.max(1, maxX - sx - 32 * z));
  }

  private drawRows(n: number, sx: number, sy: number, sw: number, textMode: boolean) {
    const ctx = this.ctx;
    const th = this.theme;
    const z = this.zoom;
    const esc = this.escena!;
    const a = esc.arbol;
    const filas = this.filasDe(n);
    const y0 = sy + HEADER_H * z;
    const rh = ROW_H * z;
    const r0 = Math.max(0, Math.floor(-y0 / rh));
    const r1 = Math.min(filas.length, Math.ceil((this.cssH - y0) / rh));
    const cw = this.charW * z;
    const drill = this.isDrillNode(n);
    const q = this.consulta;
    if (!filas.length && textMode) {
      ctx.font = `italic 400 ${11 * z}px ${NODE_FONT_FAMILY}`;
      ctx.fillStyle = th.muted;
      ctx.fillText("(sin filas con los filtros actuales)", sx + PAD_X * z, y0 + rh / 2, sw - 2 * PAD_X * z);
      return;
    }
    for (let r = r0; r < r1; r++) {
      const f = filas[r];
      const ry = y0 + r * rh;
      const cy = ry + rh / 2;
      // separadores (más marcados entre compartimentos)
      if (r > 0) {
        const nuevaSeccion = filas[r - 1].seccion !== f.seccion;
        ctx.strokeStyle = nuevaSeccion ? th.section : th.divider;
        ctx.lineWidth = nuevaSeccion ? 1 : 0.8;
        ctx.beginPath();
        ctx.moveTo(sx, ry);
        ctx.lineTo(sx + sw, ry);
        ctx.stroke();
      }
      if (n === this.selected && r === this.selectedRow) {
        ctx.fillStyle = th.selectedRow;
        ctx.fillRect(sx, ry, sw, rh);
      } else if (q && textoFila(f).includes(q)) {
        ctx.fillStyle = th.searchRow;
        ctx.fillRect(sx, ry, sw, rh);
        ctx.fillStyle = th.searchRowBar;
        ctx.fillRect(sx, ry, Math.max(1, 3 * z), rh);
      }
      let x = sx + PAD_X * z;
      const right = sx + sw - PAD_X * z;
      const tiene = f.destinos.length > 0;
      if (tiene) {
        if (textMode) {
          const bs = 14 * z;
          const by = cy - bs / 2;
          const abierta = !drill && a.abierta(n, f.key);
          const hov = this.hover === n && this.hoverBtn && this.hoverRow === r;
          if (hov) {
            ctx.fillStyle = th.divider;
            ctx.fillRect(x, by, bs, bs);
          }
          ctx.globalAlpha = hov ? 1 : drill ? 0.9 : 0.7;
          ctx.strokeStyle = drill ? th.edgeActive : th.section;
          ctx.lineWidth = 1;
          ctx.strokeRect(x + 0.5, by + 0.5, bs - 1, bs - 1);
          ctx.strokeStyle = drill ? th.edgeActive : th.interactive;
          ctx.lineWidth = Math.max(1, 1.3 * z);
          ctx.beginPath();
          const m = bs * 0.28;
          if (drill) {
            ctx.moveTo(x + m, cy);
            ctx.lineTo(x + bs - m, cy);
            ctx.moveTo(x + bs / 2, by + m);
            ctx.lineTo(x + bs - m, cy);
            ctx.lineTo(x + bs / 2, by + bs - m);
          } else {
            ctx.moveTo(x + m, cy);
            ctx.lineTo(x + bs - m, cy);
            if (!abierta) {
              ctx.moveTo(x + bs / 2, by + m);
              ctx.lineTo(x + bs / 2, by + bs - m);
            }
          }
          ctx.stroke();
          ctx.globalAlpha = 1;
        }
        x += COLLAPSE_W * z;
      }
      if (!textMode) {
        const barH = Math.max(1, 6 * z);
        ctx.globalAlpha = 0.5;
        ctx.fillStyle = this.color(f.cIzq);
        ctx.fillRect(x, cy - barH / 2, Math.min(right - x, f.izq.length * cw), barH);
        ctx.fillStyle = this.color(f.cDer);
        const x2 = x + (f.izq.length + 1) * cw;
        if (x2 < right) ctx.fillRect(x2, cy - barH / 2, Math.min(right - x2, Math.min(40, f.der.length) * cw), barH);
        ctx.globalAlpha = 1;
        continue;
      }
      // marca a la derecha (confianza, «create», inferido...)
      let limite = right;
      if (f.marca) {
        ctx.font = `400 ${10 * z}px ${NODE_FONT_FAMILY}`;
        const mw = ctx.measureText(f.marca).width;
        if (right - mw > x + 12 * cw) {
          ctx.fillStyle = th.muted;
          ctx.textBaseline = "middle";
          ctx.fillText(f.marca, right - mw, cy);
          limite = right - mw - 6 * z;
        }
      }
      ctx.textBaseline = "middle";
      ctx.font = `500 ${12 * z}px ${NODE_FONT_FAMILY}`;
      ctx.fillStyle = this.color(f.cIzq);
      const izq = f.der && CON_DOS_PUNTOS.has(f.kind) && !f.der.startsWith("= ") ? f.izq + ":" : f.izq;
      ctx.fillText(izq, x, cy, Math.max(1, limite - x));
      x += ctx.measureText(izq).width + cw;
      if (x >= limite || !f.der) continue;
      let der = f.der;
      if (f.kind === "externo" && f.llamada) {
        const e = f.llamada.externo!;
        der = `${e.tipo}${e.verbo ? " " + e.verbo : ""}  ${destinoEnCamino(esc.modelo, a, n, f.llamada)}`;
      }
      const disp = Math.max(0, Math.floor((limite - x) / cw));
      if (der.length > disp) der = disp > 1 ? der.slice(0, disp - 1) + "…" : "…";
      ctx.font = `${f.cursiva ? "italic " : ""}500 ${12 * z}px ${NODE_FONT_FAMILY}`;
      ctx.fillStyle = this.color(f.cDer);
      ctx.fillText(der, x, cy, Math.max(1, limite - x));
    }
  }

  private outline(n: number, color: string, width: number, radius: number) {
    const ctx = this.ctx;
    const L = this.layout!;
    const z = this.zoom;
    const sx = (this.nodeX(n) - this.camX) * z;
    const sy = (this.nodeY(n) - this.camY) * z;
    ctx.beginPath();
    if (radius > 0.5) ctx.roundRect(sx, sy, L.w[n] * z, L.h[n] * z, radius);
    else ctx.rect(sx, sy, L.w[n] * z, L.h[n] * z);
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.stroke();
  }
}
