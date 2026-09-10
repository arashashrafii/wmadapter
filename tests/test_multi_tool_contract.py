import unittest

from wmadapter.providers.contract import (
    CanonicalToolCall,
    CanonicalToolResult,
    normalize_tool_calls,
    normalize_tool_results,
)


class MultiToolContractTests(unittest.TestCase):
    def setUp(self):
        self.calls = [
            {"id": "call-a", "type": "function", "function": {"name": "first", "arguments": "{}"}},
            {"id": "call-b", "type": "function", "function": {"name": "second", "arguments": '{"n":2}'}},
        ]

    def test_multiple_calls_are_typed_and_ordered(self):
        normalized = normalize_tool_calls(self.calls)

        self.assertEqual([call["id"] for call in normalized], ["call-a", "call-b"])
        self.assertIsInstance(CanonicalToolCall.model_validate(normalized[0]), CanonicalToolCall)

    def test_multiple_results_preserve_result_order_and_ids(self):
        normalized = normalize_tool_results(
            [
                {"tool_call_id": "call-b", "content": "second result"},
                {"tool_call_id": "call-a", "content": "first result"},
            ],
            ["call-a", "call-b"],
        )

        self.assertEqual([result["tool_call_id"] for result in normalized], ["call-b", "call-a"])
        self.assertIsInstance(CanonicalToolResult.model_validate(normalized[0]), CanonicalToolResult)

    def test_duplicate_call_and_result_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate tool call id"):
            normalize_tool_calls(self.calls + [self.calls[0]])
        with self.assertRaisesRegex(ValueError, "Duplicate tool result id"):
            normalize_tool_results(
                [{"tool_call_id": "call-a"}, {"tool_call_id": "call-a"}],
                ["call-a"],
            )

    def test_missing_and_unknown_results_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Missing tool result"):
            normalize_tool_results([{"tool_call_id": "call-a"}], ["call-a", "call-b"])
        with self.assertRaisesRegex(ValueError, "Unknown tool result id"):
            normalize_tool_results([{"tool_call_id": "call-x"}], ["call-a"])

    def test_malformed_calls_and_results_are_rejected(self):
        malformed_call = {"id": "call-a", "type": "function", "function": {"name": "first", "arguments": {}}}
        with self.assertRaisesRegex(ValueError, "string arguments"):
            normalize_tool_calls([malformed_call])
        with self.assertRaisesRegex(ValueError, "Malformed tool result"):
            normalize_tool_results([{"content": "missing id"}], ["call-a"])


if __name__ == "__main__":
    unittest.main()
