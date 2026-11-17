/**
 * Valores por defecto. Se cambian en la propia página (botón de ajustes, se guardan en el
 * navegador) o por URL: ?niveles=4
 *
 * Para otros valores por defecto para todos, cámbialos aquí y reconstruye la interfaz.
 */
export const DEFAULT_CONFIG = {
  /**
   * Niveles que se muestran en cada etapa de navegación, contando la raíz de la etapa.
   * Al abrir una fila del último nivel se abre una etapa nueva con ese nodo como raíz.
   * Mínimo 2. 0 = sin límite.
   */
  stageLevels: 4,

  /** Ajustar automáticamente el zoom y la posición al expandir o colapsar. */
  autoFit: true,

  /** Zoom mínimo al que llega el ajuste automático (por debajo el texto ya no se lee bien). */
  autoFitMinZoom: 0.6,
};

export type AppConfig = typeof DEFAULT_CONFIG;
