"""Arma el grafo de objetos (inyección de dependencias manual)."""

from .config import Configuracion
from .integraciones.core_bancario import ClienteCoreBancario
from .integraciones.pasarela import PasarelaPagos
from .repositorios import RepositorioPagosSqlite
from .repositorios.clientes import RepositorioClientes
from .servicios import NotificadorCorreo, ServicioPagos


def crear_servicio(config: Configuracion) -> ServicioPagos:
    return ServicioPagos(
        repositorio=RepositorioPagosSqlite(config.ruta_bd),
        clientes=RepositorioClientes("clientes.json"),
        pasarela=PasarelaPagos(config.url_pasarela, config.reintentos),
        core=ClienteCoreBancario(config.wsdl_core),
        notificador=NotificadorCorreo(config.servidor_correo),
    )
