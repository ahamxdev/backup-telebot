class PipelineError(RuntimeError):
    def __init__(self, code: object, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.code = str(code)
        self.reason = reason
