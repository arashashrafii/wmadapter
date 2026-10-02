import unittest

from wmadapter.providers.qwen.image_contract import (
    ASPECT_RATIO_SIZES,
    QWEN_IMAGE_MODEL,
    QWEN_IMAGE_ASPECT_RATIOS,
    QWEN_IMAGE_STUDIO_PRESETS,
    normalize_qwen_image_size,
    qwen_image_aspect_for_size,
    qwen_image_ui_label,
)


class QwenImageModelTests(unittest.TestCase):
    def test_registered_model_identifier_matches_qwen_image_3(self):
        self.assertEqual(QWEN_IMAGE_MODEL, "qwen-image-3.0")

    def test_verified_studio_presets_are_the_supported_set(self):
        self.assertEqual(QWEN_IMAGE_ASPECT_RATIOS, ("auto", "1:1", "3:4", "4:3", "16:9", "9:16"))

    def test_live_ui_presets_exclude_auto(self):
        # The live Create Image dropdown offers only the five numeric presets;
        # "auto" means "leave the provider default", not a selectable option.
        self.assertEqual(QWEN_IMAGE_STUDIO_PRESETS, ("1:1", "3:4", "4:3", "16:9", "9:16"))
        self.assertNotIn("auto", QWEN_IMAGE_STUDIO_PRESETS)

    def test_model_maps_to_its_verified_studio_label(self):
        self.assertEqual(qwen_image_ui_label("qwen-image-3.0"), "Qwen-Image 3.0")

    def test_unverified_model_has_no_studio_label(self):
        for model in ("qwen-chat", "qwen-image-2.0", "qwen-image-3.0-pro", ""):
            with self.subTest(model=model):
                with self.assertRaises(ValueError):
                    qwen_image_ui_label(model)


class QwenImageSizeNormalizationTests(unittest.TestCase):
    def test_named_presets_map_to_deterministic_openai_sizes(self):
        expected = {
            "auto": "auto",
            "1:1": "1024x1024",
            "3:4": "960x1280",
            "4:3": "1280x960",
            "16:9": "1280x720",
            "9:16": "720x1280",
        }
        self.assertEqual(
            dict(ASPECT_RATIO_SIZES),
            {key: value for key, value in expected.items() if key != "auto"},
        )
        for value, normalized in expected.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_qwen_image_size(aspect_ratio=value), normalized)

    def test_omitted_shape_defaults_to_auto(self):
        self.assertEqual(normalize_qwen_image_size(), "auto")
        self.assertEqual(normalize_qwen_image_size(size=None, aspect_ratio=None), "auto")

    def test_size_accepts_the_same_preset_vocabulary(self):
        for aspect, size in ASPECT_RATIO_SIZES.items():
            with self.subTest(aspect=aspect):
                self.assertEqual(normalize_qwen_image_size(size=aspect), size)

    def test_explicit_dimensions_snap_to_the_matching_preset(self):
        # OpenAI-style clients request concrete pixel shapes; only the five
        # preset shapes exist upstream, so a request is mapped to the preset
        # with the same aspect ratio and reports that preset's canonical size.
        cases = {
            "1024x1024": "1024x1024",
            "2048x2048": "1024x1024",
            "512x512": "1024x1024",
            "1024X1024": "1024x1024",
            # Shapes an OpenAI-SDK client actually sends.
            "2048x1152": "1280x720",
            "3840x2160": "1280x720",
            "1536x2048": "960x1280",
            "2048x1536": "1280x960",
            "1152x2048": "720x1280",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_qwen_image_size(size=value), expected)

    def test_dimensions_without_a_preset_ratio_are_rejected(self):
        # 2:3 and 3:2 have no verified Web preset.
        for value in ("1024x1536", "1536x1024", "1024*1024", "square", "0x100"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_qwen_image_size(size=value)

    def test_tiny_matching_ratio_still_selects_its_preset(self):
        # The Web flow offers five fixed shapes, so a small requested size is
        # a shape selection rather than a resolution request.
        self.assertEqual(normalize_qwen_image_size(size="1x1"), "1024x1024")

    def test_absurd_dimensions_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_qwen_image_size(size="20000x20000")

    def test_ambiguous_or_unknown_aspect_values_are_rejected(self):
        for kwargs in (
            {"size": "1024x1024", "aspect_ratio": "1:1"},
            {"aspect_ratio": "2:3"},
            {"size": ""},
            {"aspect_ratio": ""},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    normalize_qwen_image_size(**kwargs)

    def test_error_messages_redact_request_data(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_qwen_image_size(aspect_ratio="2:3")
        self.assertNotIn("prompt", str(ctx.exception).lower())


class QwenImageAspectMappingTests(unittest.TestCase):
    def test_normalized_size_round_trips_to_its_preset(self):
        for aspect, size in ASPECT_RATIO_SIZES.items():
            with self.subTest(aspect=aspect):
                self.assertEqual(qwen_image_aspect_for_size(size), aspect)

    def test_auto_maps_to_auto(self):
        self.assertEqual(qwen_image_aspect_for_size("auto"), "auto")

    def test_presetless_pixel_size_is_rejected(self):
        with self.assertRaises(ValueError):
            qwen_image_aspect_for_size("1024x1536")


if __name__ == "__main__":
    unittest.main()
