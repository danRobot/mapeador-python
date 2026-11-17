import logging

from ..integraciones.core_bancario import ClienteCoreBancario
from ..integraciones.pasarela import PasarelaPagos
from ..modelos import EstadoPago, Pago, PagoRechazado, ResultadoPago
from ..repositorios import RepositorioPagos
from ..repositorios.clientes import RepositorioClientes
from .notificaciones import Notificador
from .validaciones import validar_pago

log = logging.getLogger(__name__)


class ServicioPagos:
    """Orquesta el flujo de un pago: validar, cobrar, debitar, guardar y notificar."""

    def __init__(self, repositorio: RepositorioPagos, clientes: RepositorioClientes, pasarela: PasarelaPagos,
                 core: ClienteCoreBancario, notificador: Notificador | None = None):
        self.repositorio = repositorio
        self.clientes = clientes
        self.pasarela = pasarela
        self.core = core
        self.notificador = notificador

    def procesar(self, pago: Pago) -> ResultadoPago:
        cliente = self.clientes.obtener(pago.cliente_id)
        errores = validar_pago(pago, cliente)
        if errores:
            pago.estado = EstadoPago.RECHAZADO
            raise PagoRechazado(pago.referencia, errores)
        autorizacion = self.pasarela.cobrar(pago)
        self.core.debitar(pago.cliente_id, pago.monto)
        pago.estado = EstadoPago.APROBADO
        self.repositorio.guardar(pago)
        resultado = ResultadoPago(pago=pago, aprobado=True, autorizacion=autorizacion)
        if self.notificador:
            self.notificador.notificar(cliente, resultado)
        log.info("pago %s aprobado", pago.referencia)
        return resultado

    def consultar(self, referencia: str):
        pago = self.repositorio.buscar(referencia)
        if pago is None:
            return None
        return self.pasarela.consultar_estado(pago.referencia)
