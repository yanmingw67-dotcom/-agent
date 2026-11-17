class AppError(Exception):
    def __init__(self, status_code: int, error_code: str, message: str):
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


class ArchiveRejected(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
