class ThumblyError(Exception):
    def __init__(self, code: str, message: str = "", status: int = 409):
        self.code, self.message, self.status = code, message or code.replace("_", " ").lower(), status
        super().__init__(self.message)

    def as_dict(self):
        return {"code": self.code, "message": self.message}
