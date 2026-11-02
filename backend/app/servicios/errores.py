"""Error de regla de negocio: la API lo convierte en una respuesta HTTP con el mensaje en español."""


class ReglaDeNegocio(Exception):
    def __init__(self, detail: str, status_code: int = 409, headers: dict | None = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.headers = headers
