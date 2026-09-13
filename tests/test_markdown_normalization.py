import unittest

from wmadapter.providers.normalizer import ToolProtocolNormalizer


class MarkdownNormalizationTests(unittest.TestCase):
    def test_tab_separated_renderer_table_becomes_markdown_table(self):
        answer = "Feature\tPython\tJavaScript\nTyping\tDynamic\tDynamic"
        _, _, visible = ToolProtocolNormalizer().normalize_with_status(answer, None)
        self.assertEqual(visible, "| Feature | Python | JavaScript |\n| --- | --- | --- |\n| Typing | Dynamic | Dynamic |")

    def test_tabs_inside_code_fences_are_preserved(self):
        answer = "```text\na\tb\n```"
        _, _, visible = ToolProtocolNormalizer().normalize_with_status(answer, None)
        self.assertEqual(visible, answer)

    def test_mermaid_markdown_is_preserved_for_client_renderer(self):
        answer = '```mermaid\nxychart-beta\n    x-axis ["Jan", "Feb"]\n    bar [100, 200]\n```'
        _, _, visible = ToolProtocolNormalizer().normalize_with_status(answer, None)
        self.assertEqual(visible, answer)
