class ApiError(Exception):
    def __init__(self, status, code, message, **details):
        super().__init__(code)
        self.status = status
        self.code = code
        self.body = {"error": code, "message": message, **details}


def warehouse_unavailable():
    return ApiError(503, "report_storage_unavailable",
                    "Хранилище отчётов временно недоступно. Повторите запрос позже.")
