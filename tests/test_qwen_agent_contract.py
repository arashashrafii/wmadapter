import unittest

from pydantic import ValidationError

from wmadapter.providers.contract import (
    GeneratedFile,
    ModelCapabilities,
    ProviderArtifact,
    ProviderCitation,
    ProviderEvent,
    ProviderResult,
)


class QwenAgentContractTests(unittest.TestCase):
    def test_unverified_agent_capabilities_are_not_advertised(self):
        capabilities = ModelCapabilities()
        for name in ("web_search", "code_interpreter", "artifacts", "citations", "generated_files"):
            self.assertFalse(getattr(capabilities, name))

    def test_provider_result_has_data_only_metadata(self):
        result = ProviderResult(
            content="answer",
            citations=[ProviderCitation(url="https://example.test/source", title="Source")],
            generated_files=[GeneratedFile(id="file-1", mime_type="text/csv", size_bytes=3)],
            artifacts=[ProviderArtifact(id="artifact-1", kind="chart", mime_type="image/svg+xml")],
            events=[ProviderEvent(type="code_interpreter", status="completed", detail="result available")],
        )
        self.assertEqual(result.events[0].type, "code_interpreter")
        self.assertNotIn("content", result.generated_files[0].model_dump())

    def test_metadata_rejects_non_https_references(self):
        for factory, field in (
            (ProviderCitation, "url"),
            (GeneratedFile, "download_url"),
            (ProviderArtifact, "preview_url"),
        ):
            values = {"url": "http://localhost/secret"} if field == "url" else {
                "id": "fixture", "mime_type": "text/plain", field: "http://localhost/secret"
            }
            with self.subTest(factory=factory.__name__):
                with self.assertRaises(ValidationError):
                    factory(**values)

    def test_metadata_rejects_payload_fields(self):
        with self.assertRaises(ValidationError):
            ProviderArtifact(id="a", kind="html", content="<script>run()</script>")


if __name__ == "__main__":
    unittest.main()
