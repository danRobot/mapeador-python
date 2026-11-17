from decimal import Decimal

from ..modelos import Cliente, Pago

MONTO_MAXIMO = Decimal("50000000")


def es_monto_valido(monto: Decimal) -> bool:
    return Decimal("0") < monto <= MONTO_MAXIMO


def validar_pago(pago: Pago, cliente: Cliente | None) -> list[str]:
    """Reglas de negocio del pago. Devuelve la lista de errores."""
    errores = []
    if cliente is None:
        errores.append("cliente inexistente")
    elif cliente.saldo < pago.monto:
        errores.append("saldo insuficiente")
    if not es_monto_valido(pago.monto):
        errores.append("monto fuera de rango")
    return errores
