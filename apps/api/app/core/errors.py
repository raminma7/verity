from fastapi import HTTPException


class ApiError(HTTPException):
    """An error with a stable machine-readable code the web app can switch on."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(status_code=status, detail={"code": code, "message": message, **extra})


def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


def forbidden(message: str = "You don't have permission to do that.", code: str = "forbidden") -> ApiError:
    return ApiError(403, code, message)
