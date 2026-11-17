/** Documento que produce el analizador (mapeador_py/documento.py). */

export type TipoDef = "proyecto" | "paquete" | "modulo" | "clase" | "funcion" | "metodo" | "main" | "nivel" | "app";

export type Especie =
  | "llamada"
  | "crea"
  | "encola"
  | "referencia"
  | "lanza"
  | "ruta"
  | "externo"
  | "libreria"
  | "no-resuelta";

export type Confianza = "alta" | "media" | "baja";

export interface Param {
  nombre: string;
  especie?: string;
  tipo?: string;
  refs?: number[];
  origen?: string;
  defecto?: string;
  valor?: string;
  /** Valor con que se construye la clase en el proyecto (si es uno solo); solo en __init__. */
  valor_construccion?: string;
  doc?: string;
}

export interface Retorno {
  tipo: string;
  refs?: number[];
  origen?: string;
}

export interface Atributo {
  nombre: string;
  tipo?: string;
  refs?: number[];
  origen?: string;
  defecto?: string;
  especie?: string;
  linea?: number;
}

export interface Externo {
  tipo: string;
  verbo?: string;
  destino?: string;
  destino_defecto?: string;
  regla?: string;
  tablas?: string[];
  notas?: string[];
}

export interface Llamada {
  linea: number;
  texto: string;
  especie: Especie;
  destinos?: number[];
  confianza?: Confianza;
  mecanismo?: string;
  mecanismos?: string[];
  qual?: string;
  externo?: Externo;
  args?: string[];
  valores?: Record<string, string>;
  motivo?: string;
  candidatos?: number[];
  nota?: string;
  ruta?: string;
  verbo?: string;
  archivo?: string;
}

export interface Def {
  id: number;
  tipo: TipoDef;
  nombre: string;
  qual: string;
  modulo?: number;
  clase?: number;
  padre?: number;
  archivo?: string;
  linea?: number;
  fin?: number;
  marcas?: string[];
  doc?: string;
  decoradores?: string[];
  firma?: string;
  params?: Param[];
  retorno?: Retorno;
  bases?: { tipo: string; refs?: number[] }[];
  atributos?: Atributo[];
  subclases?: number[];
  init?: number;
  hijos?: number[];
  llamadas?: Llamada[];
  error?: string;
  clase_app?: string;
  prefijo?: string;
  lineas?: number;
}

export interface Entrada {
  def: number;
  tipo: string;
  etiqueta: string;
  puntaje: number;
  evidencia: string;
  grupo: string;
}

export interface Servicio {
  tipo: string;
  verbo?: string;
  destino?: string;
  tablas?: string[];
  regla?: string;
  desde: number[];
}

export interface Totales {
  archivos: number;
  lineas: number;
  paquetes: number;
  modulos: number;
  clases: number;
  funciones: number;
  metodos: number;
  llamadas: number;
  especies: Record<string, number>;
  resueltas_pct: number;
  errores_sintaxis: number;
}

export interface Documento {
  formato: string;
  proyecto: {
    nombre: string;
    raiz?: string;
    version?: string;
    generado?: string;
    totales: Totales;
    tiempos_ms?: Record<string, number>;
    omitidos?: Record<string, number>;
    servidor?: string;
  };
  entradas: Entrada[];
  defs: Def[];
  servicios: Servicio[];
  advertencias: string[];
  fuentes?: Record<string, string>;
}
