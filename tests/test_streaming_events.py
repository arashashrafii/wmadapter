import unittest

from wmadapter.providers.contract import ResponseId
from wmadapter.providers.streaming import (
    ObservedDelta,
    StreamChunk,
    error_chunk,
    project_observed_deltas,
)


class StreamingEventTests(unittest.TestCase):
    def test_ordered_content_deltas_reconstruct_exactly(self):
        chunks = list(project_observed_deltas(
            ResponseId("resp_1"), "deepseek-chat", 1,
            [ObservedDelta(sequence=0, content="hel"), ObservedDelta(sequence=1, content="lo")],
        ))

        self.assertEqual([chunk.sequence for chunk in chunks], [1, 2, 3])
        self.assertEqual("".join(chunk.delta.get("content", "") for chunk in chunks), "hello")
        self.assertEqual(chunks[-1].finish_reason, "stop")
        self.assertIsNone(chunks[-1].delta.get("content"))

    def test_tool_argument_deltas_preserve_id_order_and_terminal_reason(self):
        chunks = list(project_observed_deltas(
            ResponseId("resp_2"), "deepseek-chat", 1,
            [
                ObservedDelta(sequence=0, tool_call_id="call_1", tool_name="lookup", tool_arguments='{"q":'),
                ObservedDelta(sequence=1, tool_call_id="call_1", tool_arguments='"x"}'),
            ],
            finish_reason="tool_calls",
        ))

        calls = [chunk.delta["tool_calls"][0] for chunk in chunks[:-1]]
        self.assertEqual([call["id"] for call in calls], ["call_1", "call_1"])
        self.assertEqual("".join(call["function"]["arguments"] for call in calls), '{"q":"x"}')
        self.assertEqual(chunks[-1].finish_reason, "tool_calls")

    def test_reordered_or_missing_sequence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "contiguous and ordered"):
            list(project_observed_deltas(
                ResponseId("resp_3"), "deepseek-chat", 1,
                [ObservedDelta(sequence=1, content="late")],
            ))

    def test_terminal_event_is_emitted_once(self):
        chunks = list(project_observed_deltas(
            ResponseId("resp_4"), "deepseek-chat", 1,
            [ObservedDelta(sequence=0, content="done")],
        ))

        self.assertEqual(sum(chunk.finish_reason is not None for chunk in chunks), 1)
        self.assertEqual(chunks[-1].sequence, 2)

    def test_error_event_is_safe_and_explicit(self):
        chunk = error_chunk(ResponseId("resp_5"), "deepseek-chat", 1, 2, "provider_timeout", "Provider request timed out")

        self.assertIsInstance(chunk, StreamChunk)
        self.assertEqual(chunk.error, {"code": "provider_timeout", "message": "Provider request timed out"})
        self.assertNotIn("prompt", chunk.model_dump())
        self.assertNotIn("credential", chunk.model_dump())

    def test_invalid_delta_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            ObservedDelta(sequence=0)
        with self.assertRaisesRegex(ValueError, "Tool argument delta"):
            ObservedDelta(sequence=0, tool_arguments="{}")


if __name__ == "__main__":
    unittest.main()
