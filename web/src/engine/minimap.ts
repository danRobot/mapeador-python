import type { GraphView } from "./view";

/**
 * Minimapa: dibuja una vez el layout completo (fusionando nodos por píxel, así cuesta
 * O(nodos visibles) por relayout) y encima el rectángulo de la vista actual.
 */
export class Minimap {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private base: HTMLCanvasElement;
  private view: GraphView;
  private w = 220;
  private h = 150;
  private dpr = 1;
  // transformación mundo -> minimapa
  private sx = 1;
  private sy = 1;
  private ox = 0;
  private oy = 0;
  private dirty = true;

  constructor(canvas: HTMLCanvasElement, view: GraphView) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d")!;
    this.base = document.createElement("canvas");
    this.view = view;
    this.bind();
  }

  invalidate() {
    this.dirty = true;
    this.update();
  }

  private rebuild() {
    const L = this.view.layout;
    const doc = this.view.escena;
    const r = this.canvas.getBoundingClientRect();
    this.w = Math.max(40, r.width || 220);
    this.h = Math.max(40, r.height || 150);
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    for (const c of [this.canvas, this.base]) {
      c.width = Math.round(this.w * this.dpr);
      c.height = Math.round(this.h * this.dpr);
    }
    const ctx = this.base.getContext("2d")!;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);
    if (!L || !doc) return;
    const isRight = L.dir === "RIGHT";
    const ew = Math.max(1, isRight ? L.depthExtent : L.stackExtent);
    const eh = Math.max(1, isRight ? L.stackExtent : L.depthExtent);
    const pad = 6;
    const aw = this.w - pad * 2;
    const ah = this.h - pad * 2;
    let sx = aw / ew;
    let sy = ah / eh;
    // escala uniforme salvo que la forma sea extremadamente alargada
    const ratio = Math.max(sx, sy) / Math.min(sx, sy);
    if (ratio < 6) sx = sy = Math.min(sx, sy);
    this.sx = sx;
    this.sy = sy;
    this.ox = pad + (aw - ew * sx) / 2;
    this.oy = pad + (ah - eh * sy) / 2;

    const th = this.view.theme;
    ctx.fillStyle = th.nodeStroke;
    const depthSize = isRight ? L.w : L.h;
    const stackSize = isRight ? L.h : L.w;
    const ss = isRight ? sy : sx;
    const ds = isRight ? sx : sy;
    const so = isRight ? this.oy : this.ox;
    const dOff = isRight ? this.ox : this.oy;
    for (let d = 0; d <= L.maxDepth; d++) {
      const list = L.lists[d];
      const dpos = dOff + L.colPos[d] * ds;
      let runS = 0;
      let runE = -Infinity;
      let runD = 0;
      const flush = () => {
        if (runE === -Infinity) return;
        if (isRight) ctx.fillRect(dpos, runS, Math.max(1, runD), Math.max(1, runE - runS));
        else ctx.fillRect(runS, dpos, Math.max(1, runE - runS), Math.max(1, runD));
      };
      for (let i = 0; i < list.length; i++) {
        const n = list[i];
        const s = so + L.pos[n] * ss;
        const e = s + stackSize[n] * ss;
        const dd = depthSize[n] * ds;
        if (s <= runE + 0.75) {
          if (e > runE) runE = e;
          if (dd > runD) runD = dd;
        } else {
          flush();
          runS = s;
          runE = e;
          runD = dd;
        }
      }
      flush();
    }
  }

  update() {
    if (this.canvas.offsetParent === null) return;
    if (this.dirty) {
      this.rebuild();
      this.dirty = false;
    }
    const ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.drawImage(this.base, 0, 0);
    if (!this.view.layout) return;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const v = this.view;
    const { w, h } = v.viewportSize;
    let x = this.ox + v.camX * this.sx;
    let y = this.oy + v.camY * this.sy;
    let rw = (w / v.zoom) * this.sx;
    let rh = (h / v.zoom) * this.sy;
    // tamaño mínimo visible
    if (rw < 4) {
      x -= (4 - rw) / 2;
      rw = 4;
    }
    if (rh < 4) {
      y -= (4 - rh) / 2;
      rh = 4;
    }
    ctx.fillStyle = "rgba(59,130,246,0.12)";
    ctx.fillRect(x, y, rw, rh);
    ctx.strokeStyle = "#3B82F6";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x, y, rw, rh);
  }

  private bind() {
    const go = (e: PointerEvent) => {
      const r = this.canvas.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;
      const wx = (mx - this.ox) / this.sx;
      const wy = (my - this.oy) / this.sy;
      this.view.lookAt(wx, wy, this.view.zoom, false);
    };
    let down = false;
    this.canvas.addEventListener("pointerdown", e => {
      down = true;
      this.canvas.setPointerCapture(e.pointerId);
      go(e);
    });
    this.canvas.addEventListener("pointermove", e => {
      if (down) go(e);
    });
    const up = () => (down = false);
    this.canvas.addEventListener("pointerup", up);
    this.canvas.addEventListener("pointercancel", up);
    this.canvas.addEventListener("wheel", e => {
      e.preventDefault();
      this.view.zoomAt(Math.exp(-e.deltaY * 0.0018));
    }, { passive: false });
  }
}
