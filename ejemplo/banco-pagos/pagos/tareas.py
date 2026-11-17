from celery import shared_task

from .config import cargar_configuracion
from .fabrica import crear_servicio


@shared_task
def conciliar_pagos(referencias):
    servicio = crear_servicio(cargar_configuracion())
    for referencia in referencias:
        servicio.consultar(referencia)


def programar_conciliacion(referencias: list[str]) -> None:
    conciliar_pagos.delay(referencias)
