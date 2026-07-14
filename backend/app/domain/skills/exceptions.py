from __future__ import annotations


class SkillsError(Exception):
    """Base exception for the Skills domain."""

    status_code = 400
    error_code = "skills_error"

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class SkillNotFoundError(SkillsError):
    status_code = 404
    error_code = "skill_not_found"


class SkillConflictError(SkillsError):
    status_code = 409
    error_code = "skill_conflict"


class SkillSourceConflictError(SkillsError):
    status_code = 409
    error_code = "skill_source_conflict"


class SkillValidationError(SkillsError):
    status_code = 422
    error_code = "skill_validation_error"


class PayloadTooLargeError(SkillsError):
    status_code = 413
    error_code = "payload_too_large"


class SkillsConfigurationError(SkillsError):
    status_code = 503
    error_code = "skills_configuration_error"


class SkillsGatewayError(SkillsError):
    status_code = 502
    error_code = "skills_gateway_error"


class SkillsGatewayTimeoutError(SkillsError):
    status_code = 504
    error_code = "skills_gateway_timeout"
