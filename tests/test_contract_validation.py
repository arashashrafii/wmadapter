import unittest

from pydantic import ValidationError

from wmadapter.providers.contract import (
    OpenAIError,
    OpenAIErrorResponse,
    ValidationIssue,
)


class ContractValidationTests(unittest.TestCase):
    def test_openai_error_supports_standard_error_taxonomy(self):
        for error_type in (
            "invalid_request_error",
            "authentication_error",
            "permission_error",
            "not_found_error",
            "rate_limit_error",
            "server_error",
            "api_error",
        ):
            with self.subTest(error_type=error_type):
                error = OpenAIError(
                    message="Request could not be completed",
                    type=error_type,
                    param="messages",
                    code="invalid_request",
                )
                self.assertEqual(error.type, error_type)

    def test_error_response_round_trips_openai_shape(self):
        response = OpenAIErrorResponse(
            error=OpenAIError(
                message="Invalid request body",
                code="invalid_request",
            )
        )

        self.assertEqual(
            response.model_dump(),
            {
                "error": {
                    "message": "Invalid request body",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_request",
                }
            },
        )

    def test_validation_issue_identifies_location_without_raw_input(self):
        issue = ValidationIssue(
            loc=("messages", 0, "role"),
            message="Unsupported message role",
            type="value_error.role",
        )

        self.assertEqual(issue.loc, ("messages", 0, "role"))
        self.assertNotIn("input", issue.model_dump())
        self.assertNotIn("content", issue.model_dump())
        self.assertNotIn("credential", issue.model_dump())

    def test_error_type_is_validated_but_error_codes_remain_opaque(self):
        OpenAIError(message="Provider failed", code="provider_specific_code")
        with self.assertRaises(ValidationError):
            OpenAIError(message="bad", type="unsupported_error")


if __name__ == "__main__":
    unittest.main()
