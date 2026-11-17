import type { Documento } from "./tipos";

/**
 * Carga de proyectos.
 *
 *  - Carpeta elegida con el selector o arrastrada: el navegador lee solo los .py (y
 *    pyproject.toml / setup.cfg), saltándose venv, node_modules, .git, etc., y los manda
 *    al servidor, que los analiza en memoria (no se guardan ni se ejecutan).
 *  - Carpeta del servidor (montada en Docker): la lee el propio servidor.
 *  - JSON generado con `mapeador-py analizar`: se abre directo, sin servidor.
 */

const EXCLUIDOS = new Set([
  ".git", ".hg", ".svn", ".idea", ".vscode", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
  ".venv", "venv", "env", "virtualenv", ".virtualenv", "site-packages", "dist-packages", "node_modules", "build", "dist",
  ".eggs", "htmlcov", ".ipynb_checkpoints", "migrations", "docs", "doc", "_build", ".git-rewrite", "bower_components",
]);
const CONFIG = new Set(["pyproject.toml", "setup.cfg"]);
const MAX_ARCHIVOS = 20000;

export interface ArchivoLocal {
  ruta: string;
  file: File;
}

export interface Seleccion {
  nombre: string;
  archivos: ArchivoLocal[];
  /** Archivos vistos en total (para el mensaje de progreso). */
  vistos: number;
}

const util = (ruta: string) => {
  const partes = ruta.split("/");
  const nombre = partes[partes.length - 1];
  if (partes.slice(0, -1).some(p => EXCLUIDOS.has(p) || p.endsWith(".egg-info"))) return false;
  return nombre.endsWith(".py") || nombre.endsWith(".pyw") || CONFIG.has(nombre);
};

/** Archivos del `<input type="file" webkitdirectory>`. */
export function desdeInput(files: FileList): Seleccion {
  const lista = [...files];
  const primero = (lista[0] as File & { webkitRelativePath?: string })?.webkitRelativePath || "";
  const nombre = primero.split("/")[0] || "proyecto";
  const archivos: ArchivoLocal[] = [];
  for (const f of lista) {
    const rel = ((f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name).split("/").slice(1).join("/") || f.name;
    if (util(rel) && archivos.length < MAX_ARCHIVOS) archivos.push({ ruta: rel, file: f });
  }
  return { nombre, archivos, vistos: lista.length };
}

type Entry = FileSystemEntry;

/** Carpeta arrastrada (recorre el árbol podando las carpetas excluidas). Si es un .json, lo devuelve. */
export async function desdeArrastre(dt: DataTransfer, progreso: (n: number) => void): Promise<Seleccion | { json: File } | null> {
  const items = [...dt.items].filter(i => i.kind === "file");
  const entradas = items.map(i => i.webkitGetAsEntry?.()).filter((e): e is Entry => !!e);
  if (!entradas.length) {
    const f = dt.files[0];
    return f && /\.json$/i.test(f.name) ? { json: f } : null;
  }
  if (entradas.length === 1 && entradas[0].isFile) {
    const f = await archivoDe(entradas[0] as FileSystemFileEntry);
    if (/\.json$/i.test(f.name)) return { json: f };
    if (!/\.py$/i.test(f.name)) return null;
    return { nombre: f.name.replace(/\.py$/i, ""), archivos: [{ ruta: f.name, file: f }], vistos: 1 };
  }
  const raiz = entradas.length === 1 && entradas[0].isDirectory ? (entradas[0] as FileSystemDirectoryEntry) : null;
  const nombre = raiz ? raiz.name : "proyecto";
  const archivos: ArchivoLocal[] = [];
  let vistos = 0;
  const pila: [Entry, string][] = raiz ? [[raiz, ""]] : entradas.map(e => [e, ""] as [Entry, string]);
  while (pila.length && archivos.length < MAX_ARCHIVOS) {
    const [e, base] = pila.pop()!;
    if (e.isDirectory) {
      if (e !== raiz && (EXCLUIDOS.has(e.name) || e.name.endsWith(".egg-info"))) continue;
      const hijos = await leerDirectorio(e as FileSystemDirectoryEntry);
      const prefijo = e === raiz ? "" : `${base}${e.name}/`;
      for (const h of hijos) pila.push([h, prefijo]);
    } else {
      vistos++;
      if (vistos % 200 === 0) progreso(vistos);
      const rel = `${base}${e.name}`;
      if (util(rel)) archivos.push({ ruta: rel, file: await archivoDe(e as FileSystemFileEntry) });
    }
  }
  return { nombre, archivos, vistos };
}

function leerDirectorio(d: FileSystemDirectoryEntry): Promise<Entry[]> {
  const lector = d.createReader();
  const todos: Entry[] = [];
  return new Promise((ok, mal) => {
    const lote = () =>
      lector.readEntries(es => {
        if (!es.length) ok(todos);
        else {
          todos.push(...es);
          lote();
        }
      }, mal);
    lote();
  });
}

const archivoDe = (e: FileSystemFileEntry) => new Promise<File>((ok, mal) => e.file(ok, mal));

const utf8 = new TextDecoder("utf-8", { fatal: true });
const latin = new TextDecoder("windows-1252");

/** Lee los textos (UTF-8; si no lo es, Windows-1252/Latin-1, común en código heredado). */
export async function leerTextos(archivos: ArchivoLocal[], progreso: (hechos: number) => void): Promise<Record<string, string>> {
  const r: Record<string, string> = {};
  let i = 0;
  let hechos = 0;
  const trabajador = async () => {
    while (i < archivos.length) {
      const a = archivos[i++];
      const buf = await a.file.arrayBuffer();
      let texto: string;
      try {
        texto = utf8.decode(buf);
      } catch {
        texto = latin.decode(buf);
      }
      r[a.ruta] = texto.charCodeAt(0) === 0xfeff ? texto.slice(1) : texto;
      if (++hechos % 100 === 0) progreso(hechos);
    }
  };
  await Promise.all(Array.from({ length: 12 }, trabajador));
  return r;
}

export class ErrorServidor extends Error {}

async function respuesta(res: Response): Promise<Documento> {
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j?.error) msg = j.error;
    } catch {
      /* sin cuerpo JSON */
    }
    throw new ErrorServidor(msg);
  }
  return (await res.json()) as Documento;
}

export interface Opciones {
  incluir_pruebas?: boolean;
  rondas?: number;
}

/** Manda los archivos al servidor (comprimidos con gzip si el navegador sabe) y devuelve el análisis. */
export async function analizarEnServidor(nombre: string, archivos: Record<string, string>, opciones: Opciones): Promise<Documento> {
  const cuerpo = JSON.stringify({ nombre, archivos, opciones });
  let body: BodyInit = cuerpo;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (typeof CompressionStream !== "undefined") {
    const stream = new Blob([cuerpo]).stream().pipeThrough(new CompressionStream("gzip"));
    body = await new Response(stream).blob();
    headers["Content-Encoding"] = "gzip";
  }
  let res: Response;
  try {
    res = await fetch("api/analizar", { method: "POST", body, headers });
  } catch {
    throw new ErrorServidor("El servidor de análisis no responde. ¿Está corriendo «mapeador-py servir» (o el contenedor)?");
  }
  return respuesta(res);
}

export async function analizarCarpetaServidor(ruta: string, opciones: Opciones): Promise<Documento> {
  let res: Response;
  try {
    res = await fetch("api/analizar-carpeta", { method: "POST", body: JSON.stringify({ ruta, opciones }), headers: { "Content-Type": "application/json" } });
  } catch {
    throw new ErrorServidor("El servidor de análisis no responde.");
  }
  return respuesta(res);
}

export interface CarpetaServidor {
  nombre: string;
  ruta: string;
  py: number;
}

export async function listarProyectos(ruta = ""): Promise<{ disponible: boolean; carpetas: CarpetaServidor[]; py_aqui?: number } | null> {
  try {
    const res = await fetch(`api/proyectos?ruta=${encodeURIComponent(ruta)}`, { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function fuenteServidor(proyecto: string, archivo: string): Promise<string | null> {
  try {
    const res = await fetch(`api/fuente?proyecto=${encodeURIComponent(proyecto)}&archivo=${encodeURIComponent(archivo)}`, { cache: "no-store" });
    return res.ok ? await res.text() : null;
  } catch {
    return null;
  }
}
