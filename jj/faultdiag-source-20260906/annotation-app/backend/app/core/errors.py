from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, error_code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code


class InvalidArchiveTypeError(AppError):
    def __init__(self, message: str = "Invalid archive type") -> None:
        super().__init__(
            error_code="INVALID_ARCHIVE_TYPE",
            message=message,
            status_code=400,
        )


class FileTooLargeError(AppError):
    def __init__(self, message: str = "File exceeds allowed size") -> None:
        super().__init__(
            error_code="FILE_TOO_LARGE",
            message=message,
            status_code=413,
        )


class PackageInUseError(AppError):
    def __init__(self, message: str = "Package is in use") -> None:
        super().__init__(
            error_code="PACKAGE_IN_USE",
            message=message,
            status_code=409,
        )


class PackageNameConfirmMismatchError(AppError):
    def __init__(self, message: str = "Package name confirmation does not match") -> None:
        super().__init__(
            error_code="PACKAGE_NAME_CONFIRM_MISMATCH",
            message=message,
            status_code=409,
        )


class SliceTaskValidationError(AppError):
    def __init__(self, message: str = "Invalid slice task payload") -> None:
        super().__init__(
            error_code="SLICE_TASK_VALIDATION_ERROR",
            message=message,
            status_code=400,
        )


class SliceTaskNotAllowedError(AppError):
    def __init__(self, message: str = "Package is not ready for slicing") -> None:
        super().__init__(
            error_code="SLICE_TASK_NOT_ALLOWED",
            message=message,
            status_code=409,
        )


class InvalidPaginationParamsError(AppError):
    def __init__(self, message: str = "Invalid pagination parameters") -> None:
        super().__init__(
            error_code="INVALID_PAGINATION_PARAMS",
            message=message,
            status_code=400,
        )


class InvalidRelativePathError(AppError):
    def __init__(self, message: str = "Invalid relative path") -> None:
        super().__init__(
            error_code="INVALID_RELATIVE_PATH",
            message=message,
            status_code=400,
        )


class WindowNotFoundError(AppError):
    def __init__(self, message: str = "Slice window not found") -> None:
        super().__init__(
            error_code="WINDOW_NOT_FOUND",
            message=message,
            status_code=404,
        )


class WindowHasAnnotationError(AppError):
    """Block deleting a window that already carries an annotation. Forces the
    user to remove the annotation first so we never silently lose human work."""

    def __init__(self, message: str = "Window already has an annotation; remove it first") -> None:
        super().__init__(
            error_code="WINDOW_HAS_ANNOTATION",
            message=message,
            status_code=409,
        )


class AnnotationConflictError(AppError):
    def __init__(self, message: str = "Annotation already exists for this window") -> None:
        super().__init__(
            error_code="ANNOTATION_CONFLICT",
            message=message,
            status_code=409,
        )


class AnnotationNotFoundError(AppError):
    def __init__(self, message: str = "Annotation not found") -> None:
        super().__init__(
            error_code="ANNOTATION_NOT_FOUND",
            message=message,
            status_code=404,
        )


class AnnotationValidationError(AppError):
    def __init__(self, message: str = "Invalid annotation payload") -> None:
        super().__init__(
            error_code="ANNOTATION_VALIDATION_ERROR",
            message=message,
            status_code=400,
        )


class AnnotationExportFileNotFoundError(AppError):
    def __init__(self, message: str = "Annotation export file not found") -> None:
        super().__init__(
            error_code="ANNOTATION_EXPORT_FILE_NOT_FOUND",
            message=message,
            status_code=404,
        )


class RecommendationNotFoundError(AppError):
    def __init__(self, message: str = "Recommendation not found for this window") -> None:
        super().__init__(
            error_code="RECOMMENDATION_NOT_FOUND",
            message=message,
            status_code=404,
        )


class FaultTypeNotFoundError(AppError):
    def __init__(self, message: str = "Fault type not found") -> None:
        super().__init__(
            error_code="FAULT_TYPE_NOT_FOUND",
            message=message,
            status_code=404,
        )


class FaultTypeNameConflictError(AppError):
    def __init__(self, message: str = "Fault type name already exists") -> None:
        super().__init__(
            error_code="FAULT_TYPE_NAME_CONFLICT",
            message=message,
            status_code=409,
        )


class FaultTypeSuggestionNotFoundError(AppError):
    def __init__(self, message: str = "Fault type suggestion not found") -> None:
        super().__init__(
            error_code="FAULT_TYPE_SUGGESTION_NOT_FOUND",
            message=message,
            status_code=404,
        )


class FaultTypeSuggestionInvalidStateError(AppError):
    def __init__(self, message: str = "Fault type suggestion is not pending") -> None:
        super().__init__(
            error_code="FAULT_TYPE_SUGGESTION_INVALID_STATE",
            message=message,
            status_code=409,
        )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message, "detail": exc.message},
        )
