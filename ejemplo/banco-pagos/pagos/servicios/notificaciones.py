import smtplib
from email.message import EmailMessage

from ..modelos import Cliente, ResultadoPago


class Notificador:
    """Notifica al cliente el resultado de su pago."""

    def notificar(self, cliente: Cliente, resultado: ResultadoPago) -> None:
        raise NotImplementedError


class NotificadorCorreo(Notificador):
    def __init__(self, servidor: str):
        self.servidor = servidor

    def notificar(self, cliente: Cliente, resultado: ResultadoPago) -> None:
        mensaje = self._armar_mensaje(cliente, resultado)
        with smtplib.SMTP(self.servidor) as smtp:
            smtp.send_message(mensaje)

    def _armar_mensaje(self, cliente: Cliente, resultado: ResultadoPago) -> EmailMessage:
        mensaje = EmailMessage()
        mensaje["To"] = cliente.correo
        mensaje["Subject"] = f"Pago {resultado.pago.referencia}"
        return mensaje


class NotificadorConsola(Notificador):
    def notificar(self, cliente: Cliente, resultado: ResultadoPago) -> None:
        print(cliente.nombre, resultado.aprobado)
