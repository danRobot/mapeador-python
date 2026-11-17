from decimal import Decimal

from pagos.servicios.validaciones import es_monto_valido


def test_monto():
    assert es_monto_valido(Decimal("10"))
