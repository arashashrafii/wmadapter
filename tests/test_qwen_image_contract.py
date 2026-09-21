import unittest

from wmadapter.providers.qwen.image_contract import (
    ASPECT_RATIO_SIZES,
    normalize_qwen_image_size,
)


class QwenImageContractTests(unittest.TestCase):
    def test_named_presets_map_to_deterministic_openai_sizes(self):
        expected = {
            "auto": "auto",
            "1:1": "1024x1024",
            "3:4": "960x1280",
            "4:3": "1280x960",
            "16:9": "1280x720",
            "9:16": "720x1280",
        }
        self.assertEqual(dict(ASPECT_RATIO_SIZES), {key: value for key, value in expected.items() if key != "auto"})
        for value, normalized in expected.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_qwen_image_size(aspect_ratio=value), normalized)

    def test_explicit_dimensions_are_normalized_and_bounded(self):
        self.assertEqual(normalize_qwen_image_size(size=" 1024X1536 "), "1024x1536")
        for value in ("511x511", "2049x2048", "1x1", "8192x8192", "1024*1024", "square"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_qwen_image_size(size=value)

    def test_ambiguous_or_unknown_aspect_values_are_rejected(self):
        for kwargs in (
            {"size": "1024x1024", "aspect_ratio": "1:1"},
            {"aspect_ratio": "2:3"},
            {"size": ""},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    normalize_qwen_image_size(**kwargs)


if __name__ == "__main__":
    unittest.main()
