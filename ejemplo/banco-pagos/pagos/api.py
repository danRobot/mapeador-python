"""API web de pagos (FastAPI)."""

from decimal import Decimal

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from .config import cargar_configuracion
from .fabrica import crear_servicio
from .modelos import Pago, PagoRechazado

app = FastAPI(title="Pagos")
router = APIRouter(prefix="/pagos")
servicio = crear_servicio(cargar_configuracion())


class SolicitudPago(BaseModel):
    referencia: str
    cliente_id: str
    monto: Decimal


class RespuestaPago(BaseModel):
    referencia: str
    aprobado: bool
    autorizacion: str | None = None


@router.post("/", response_model=RespuestaPago)
def crear_pago(solicitud: SolicitudPago) -> RespuestaPago:
    pago = Pago(referencia=solicitud.referencia, cliente_id=solicitud.cliente_id, monto=solicitud.monto)
    try:
        resultado = servicio.procesar(pago)
    except PagoRechazado as error:
        raise HTTPException(status_code=422, detail=error.motivos)
    return RespuestaPago(referencia=pago.referencia, aprobado=resultado.aprobado,
                         autorizacion=resultado.autorizacion)


@router.get("/{referencia}")
async def consultar_pago(referencia: str):
    return servicio.consultar(referencia)


@app.get("/salud")
def salud() -> dict:
    return {"ok": True}


app.include_router(router)
