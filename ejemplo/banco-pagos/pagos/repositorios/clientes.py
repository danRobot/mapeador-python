"""Repositorio heredado, sin anotaciones de tipo (los tipos se infieren de las llamadas)."""

import json


class RepositorioClientes:
    def __init__(self, ruta_archivo):
        self.ruta_archivo = ruta_archivo
        self._cache = {}

    def obtener(self, cliente_id):
        """Busca un cliente por id.

        :param str cliente_id: identificador del cliente
        :rtype: Cliente
        """
        if not self._cache:
            self._cargar()
        return self._cache.get(cliente_id)

    def _cargar(self):
        from ..modelos import Cliente

        with open(self.ruta_archivo, encoding="utf-8") as archivo:
            for dato in json.load(archivo):
                self._cache[dato["id"]] = Cliente(**dato)
