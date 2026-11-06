import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

from markitdown import MarkItDown
from openpyxl import Workbook
from pptx import Presentation

from convert import polish, validate_source, automatic_output, main, convert, confirm_key, report_error


def fake_client(content="# 标题\n\n正文\n\n## 文档总结（AI 生成）\n\n- 原文包含正文。", reason="stop"):
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[
        SimpleNamespace(finish_reason=reason, message=SimpleNamespace(content=content))
    ])
    return client


class ConversionTests(unittest.TestCase):
    def test_url_boundary(self):
        self.assertEqual(validate_source("https://demo.feishu.cn/docx/abc123"), "feishu")
        for url in ["http://demo.feishu.cn/docx/abc", "https://feishu.cn.evil.com/docx/a",
                    "https://u:p@demo.feishu.cn/docx/a", "https://demo.feishu.cn/wiki/a",
                    "https://demo.feishu.cn:999/docx/a", "missing.docx"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_source(url)

    def test_model_request(self):
        for model in ["glm-5.2", "deepseek-chat"]:
            client = fake_client()
            with patch("convert.MODEL", model):
                self.assertIn("正文", polish("# 标题\n\n正文", client))
            args = client.chat.completions.create.call_args.kwargs
            self.assertEqual(args["model"], model)
            self.assertEqual("extra_body" in args, model == "glm-5.2")

    def test_empty_long_and_truncated(self):
        for text in [" ", "a" * 12001]:
            client = fake_client()
            with self.assertRaises(ValueError):
                polish(text, client)
            client.chat.completions.create.assert_not_called()
        for content, reason in [("", "stop"), ("partial", "length"), (None, "stop")]:
            with self.assertRaises(RuntimeError):
                polish("hello", fake_client(content, reason))

    def test_summary_required(self):
        for content in ["# 正文", "# 正文\n\n## 文档总结（AI 生成）\n"]:
            with self.subTest(content=content), self.assertRaises(RuntimeError):
                polish("正文", fake_client(content))
        result = polish("正文", fake_client())
        self.assertIn("## 文档总结（AI 生成）", result)
        self.assertTrue(result.startswith("# 标题"))

    def test_output_names(self):
        with patch.object(Path, "exists", return_value=False):
            self.assertEqual(automatic_output("notes.docx"), Path("output/notes.md"))
            self.assertEqual(automatic_output("https://demo.feishu.cn/docx/abc"), Path("output/abc.md"))
        with patch.object(Path, "exists", side_effect=[True, False, False]):
            self.assertEqual(automatic_output("notes.docx"), Path("output/notes-1.md"))

    def test_interactive_continues_after_error(self):
        with patch("sys.argv", ["convert.py"]), patch("builtins.input", side_effect=["bad", "sample.txt", "exit"]), patch("convert.convert", side_effect=[ValueError("bad"), None]) as run:
            main()
            self.assertEqual(run.call_count, 2)

    def test_consent_and_key(self):
        with patch("builtins.input", return_value="no"), patch("convert.getpass.getpass") as prompt:
            self.assertIsNone(confirm_key())
            prompt.assert_not_called()
        with patch("builtins.input", return_value="YES"), patch.dict("os.environ", {"LLM_API_KEY": "<你的 key>"}):
            with self.assertRaises(ValueError):
                confirm_key()

    def test_cancel_keeps_raw_without_api(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.txt", Path(folder) / "out.md"
            source.write_text("Public sample 42", encoding="utf-8")
            with patch("convert.OpenAI") as client:
                convert(str(source), output, request_key=lambda: None)
                client.assert_not_called()
            self.assertTrue(output.with_name("out.raw.md").exists())
            self.assertFalse(output.exists())
            with self.assertRaises(ValueError):
                convert(str(source), output, local_only=True)

    def test_error_does_not_echo_provider_body(self):
        class ProviderError(Exception):
            pass
        with patch("builtins.print") as log:
            report_error(ExceptionGroup("wrapper", [ProviderError("private-token")]))
            self.assertNotIn("private-token", str(log.call_args))

    def test_formats(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            (d / "sample.txt").write_text("Conversion sample 42", encoding="utf-8")
            (d / "sample.html").write_text("<h1>Conversion sample</h1><p>42</p>", encoding="utf-8")
            with ZipFile(d / "sample.docx", "w") as z:
                z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
                z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Conversion sample 42</w:t></w:r></w:p></w:body></w:document>')
            wb = Workbook()
            wb.active.append(["Conversion sample", 42])
            wb.save(d / "sample.xlsx")
            deck = Presentation()
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = "Conversion sample 42"
            deck.save(d / "sample.pptx")
            # Minimal text-layer PDF, with correct xref offsets; no extra test dependency.
            stream = b"BT /F1 12 Tf 72 720 Td (Conversion sample 42) Tj ET"
            objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
                       b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                       b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
                       b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                       b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
            pdf, offsets = bytearray(b"%PDF-1.4\n"), [0]
            for i, obj in enumerate(objects, 1):
                offsets.append(len(pdf))
                pdf.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
            xref = len(pdf)
            pdf.extend(b"xref\n0 6\n0000000000 65535 f \n")
            for offset in offsets[1:]:
                pdf.extend(f"{offset:010d} 00000 n \n".encode())
            pdf.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
            (d / "sample.pdf").write_bytes(pdf)
            converter = MarkItDown()
            for file in d.iterdir():
                with self.subTest(format=file.suffix):
                    text = converter.convert_local(str(file)).text_content
                    self.assertIn("Conversion sample", text)
                    self.assertIn("42", text)


if __name__ == "__main__":
    unittest.main()
