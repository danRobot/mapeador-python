// Colores de los temas oscuro y claro tomados de jsoncrack-react (packages/jsoncrack-react/src/theme.ts),
// Copyright Aykut Saraç, licencia Apache-2.0. El tema "plantuml" imita el estilo clásico de PlantUML.

export type ThemeMode = "dark" | "light" | "plantuml";

export interface CanvasTheme {
  bg: string;
  gridPrimary: string;
  gridSecondary: string;
  edge: string;
  edgeActive: string;
  nodeFill: string;
  nodeStroke: string;
  headerFill: string;
  nodeHover: string;
  title: string;
  sub: string;
  key: string;
  value: string;
  tipo: string;
  tipoProyecto: string;
  muted: string;
  linea: string;
  crea: string;
  cola: string;
  ref: string;
  lanza: string;
  verbo: string;
  str: string;
  num: string;
  divider: string;
  section: string;
  interactive: string;
  searchRow: string;
  searchRowBar: string;
  selectedFill: string;
  selectedRow: string;
  skeleton: string;
  /** Color por categoría de servicio externo. */
  ext: Record<string, string>;
  /** Círculo del estereotipo (letra) por tipo de definición. */
  stereo: Record<string, string>;
  stereoText: string;
}

const EXT_OSCURO = {
  HTTP: "#ff8a65",
  SOAP: "#ffab91",
  BD: "#ffd166",
  CACHE: "#ffd166",
  ARCHIVO: "#9ccc65",
  COLA: "#ce93d8",
  NUBE: "#4fc3f7",
  PROCESO: "#4dd0e1",
  CORREO: "#f48fb1",
  GRPC: "#ff8a65",
  SOCKET: "#ff8a65",
  FTP: "#9ccc65",
  SSH: "#4dd0e1",
  LDAP: "#b0bec5",
};

const EXT_CLARO = {
  HTTP: "#d84315",
  SOAP: "#bf360c",
  BD: "#b28704",
  CACHE: "#b28704",
  ARCHIVO: "#558b2f",
  COLA: "#8e24aa",
  NUBE: "#0277bd",
  PROCESO: "#00838f",
  CORREO: "#c2185b",
  GRPC: "#d84315",
  SOCKET: "#d84315",
  FTP: "#558b2f",
  SSH: "#00838f",
  LDAP: "#546e7a",
};

// Colores de los círculos de estereotipo de PlantUML (C verde, A celeste, I lila, E naranja...).
const ESTEREOTIPOS = {
  clase: "#ADD1B2",
  abstracta: "#A9DCDF",
  protocolo: "#B4A7E5",
  enum: "#EB937F",
  excepcion: "#F4A6A6",
  datos: "#C5E1A5",
  funcion: "#FFD27F",
  metodo: "#A9C8F5",
  modulo: "#D9C2EE",
  paquete: "#E8D9A8",
  proyecto: "#E8D9A8",
  main: "#7EE2A8",
  nivel: "#CFD8DC",
  app: "#FFAB91",
};

export const themes: Record<ThemeMode, CanvasTheme> = {
  dark: {
    bg: "#141414",
    gridPrimary: "#1c1b1b",
    gridSecondary: "#191919",
    edge: "#4a4a4a",
    edgeActive: "#3B82F6",
    nodeFill: "#262626",
    nodeStroke: "#424242",
    headerFill: "#2e2e2e",
    nodeHover: "#3B82F6",
    title: "#eef3f5",
    sub: "#8b9096",
    key: "#59b8ff",
    value: "#DCE5E7",
    tipo: "#e8c479",
    tipoProyecto: "#00DC7D",
    muted: "#7d8187",
    linea: "#6b7075",
    crea: "#c792ea",
    cola: "#ce93d8",
    ref: "#4dd0e1",
    lanza: "#F85C50",
    verbo: "#ff8a65",
    str: "#c3e88d",
    num: "#e8c479",
    divider: "#343434",
    section: "#4a4a4a",
    interactive: "#b9bbbe",
    searchRow: "rgba(34, 197, 94, 0.18)",
    searchRowBar: "rgba(34, 197, 94, 0.7)",
    selectedFill: "rgba(59, 130, 246, 0.10)",
    selectedRow: "rgba(59, 130, 246, 0.22)",
    skeleton: "rgba(220, 229, 231, 0.18)",
    ext: EXT_OSCURO,
    stereo: ESTEREOTIPOS,
    stereoText: "#1b1b1b",
  },
  light: {
    bg: "#f7f7f7",
    gridPrimary: "#ebe8e8",
    gridSecondary: "#f2eeee",
    edge: "#b9bbbd",
    edgeActive: "#3B82F6",
    nodeFill: "#ffffff",
    nodeStroke: "#BCBEC0",
    headerFill: "#f3f4f6",
    nodeHover: "#3B82F6",
    title: "#1f2328",
    sub: "#6e7781",
    key: "#761CEA",
    value: "#3d3d3d",
    tipo: "#b5620b",
    tipoProyecto: "#118a3e",
    muted: "#8c959f",
    linea: "#a0a7af",
    crea: "#8e24aa",
    cola: "#8e24aa",
    ref: "#00838f",
    lanza: "#d32f2f",
    verbo: "#d84315",
    str: "#2e7d32",
    num: "#FD0079",
    divider: "#ececec",
    section: "#c9ccd0",
    interactive: "#4f5660",
    searchRow: "rgba(34, 197, 94, 0.18)",
    searchRowBar: "rgba(34, 197, 94, 0.7)",
    selectedFill: "rgba(59, 130, 246, 0.08)",
    selectedRow: "rgba(59, 130, 246, 0.16)",
    skeleton: "rgba(83, 83, 83, 0.18)",
    ext: EXT_CLARO,
    stereo: ESTEREOTIPOS,
    stereoText: "#1b1b1b",
  },
  plantuml: {
    bg: "#ffffff",
    gridPrimary: "#f1f1f1",
    gridSecondary: "#f8f8f8",
    edge: "#A80036",
    edgeActive: "#1E5AA8",
    nodeFill: "#FEFECE",
    nodeStroke: "#A80036",
    headerFill: "#FEFECE",
    nodeHover: "#1E5AA8",
    title: "#000000",
    sub: "#6a5d4d",
    key: "#1d2a73",
    value: "#000000",
    tipo: "#6b2c91",
    tipoProyecto: "#0b7a3b",
    muted: "#8a8a7a",
    linea: "#9a9a8a",
    crea: "#6b2c91",
    cola: "#6b2c91",
    ref: "#00747a",
    lanza: "#c62828",
    verbo: "#b33c00",
    str: "#2e6b1f",
    num: "#b0005a",
    divider: "#ece6b4",
    section: "#A80036",
    interactive: "#5a4a3a",
    searchRow: "rgba(34, 197, 94, 0.22)",
    searchRowBar: "rgba(22, 128, 61, 0.8)",
    selectedFill: "rgba(30, 90, 168, 0.08)",
    selectedRow: "rgba(30, 90, 168, 0.16)",
    skeleton: "rgba(80, 60, 40, 0.18)",
    ext: EXT_CLARO,
    stereo: ESTEREOTIPOS,
    stereoText: "#000000",
  },
};

export const NODE_FONT_FAMILY =
  'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace';
