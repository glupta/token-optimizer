"""Tests for detectors/pdf_ingestion.py — detect_pdf_ingestion_inline."""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "token-optimizer", "scripts"))

from detectors.pdf_ingestion import detect_pdf_ingestion_inline, EXPENSIVE_BINARY


class PdfIngestionInlineTests(unittest.TestCase):
    # ------------------------------------------------------------------ happy path

    def test_pdf_file_returns_finding(self):
        result = detect_pdf_ingestion_inline("/docs/report.pdf", 2 * 1024 * 1024, ".pdf")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "pdf_ingestion")
        self.assertGreater(result["savings_tokens"], 0)
        self.assertEqual(result["confidence"], 0.9)
        self.assertEqual(result["occurrence_count"], 1)

    def test_image_file_returns_finding(self):
        result = detect_pdf_ingestion_inline("/assets/photo.png", 500 * 1024, ".png")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "pdf_ingestion")

    def test_docx_file_returns_finding(self):
        result = detect_pdf_ingestion_inline("/docs/spec.docx", 1 * 1024 * 1024, ".docx")
        self.assertIsNotNone(result)
        self.assertIn("docx", result["evidence"])

    # ------------------------------------------------------------------ token estimates

    def test_pdf_token_estimate_uses_document_rate(self):
        size_bytes = 1 * 1024 * 1024  # 1 MB
        result = detect_pdf_ingestion_inline("x.pdf", size_bytes, ".pdf")
        # _DOCUMENT_TOKENS_PER_MB = 2500 → 1MB ≈ 2500 tokens
        self.assertAlmostEqual(result["savings_tokens"], 2500, delta=100)

    def test_image_token_estimate_uses_media_rate(self):
        size_bytes = 1 * 1024 * 1024  # 1 MB
        result = detect_pdf_ingestion_inline("x.jpg", size_bytes, ".jpg")
        # _MEDIA_TOKENS_PER_MB = 1500 → 1MB ≈ 1500 tokens
        self.assertAlmostEqual(result["savings_tokens"], 1500, delta=100)

    # ------------------------------------------------------------------ skip conditions

    def test_tiny_file_under_1kb_returns_none(self):
        result = detect_pdf_ingestion_inline("/docs/tiny.pdf", 512, ".pdf")
        self.assertIsNone(result)

    def test_exactly_1kb_returns_finding(self):
        result = detect_pdf_ingestion_inline("/docs/borderline.pdf", 1024, ".pdf")
        self.assertIsNotNone(result)

    def test_non_expensive_extension_returns_none(self):
        result = detect_pdf_ingestion_inline("/code/main.py", 50 * 1024, ".py")
        self.assertIsNone(result)

    def test_plain_text_returns_none(self):
        result = detect_pdf_ingestion_inline("/docs/notes.txt", 100 * 1024, ".txt")
        self.assertIsNone(result)

    # ------------------------------------------------------------------ suggestion content

    def test_pdf_suggestion_mentions_pdftotext(self):
        result = detect_pdf_ingestion_inline("doc.pdf", 2 * 1024 * 1024, ".pdf")
        self.assertIn("pdftotext", result["suggestion"])

    def test_image_suggestion_different_from_pdf(self):
        pdf_result = detect_pdf_ingestion_inline("a.pdf", 1024 * 1024, ".pdf")
        img_result = detect_pdf_ingestion_inline("a.png", 1024 * 1024, ".png")
        self.assertNotEqual(pdf_result["suggestion"], img_result["suggestion"])

    # ------------------------------------------------------------------ EXPENSIVE_BINARY constant

    def test_expensive_binary_includes_expected_extensions(self):
        for ext in (".pdf", ".png", ".jpg", ".docx", ".xlsx", ".pptx"):
            self.assertIn(ext, EXPENSIVE_BINARY, msg=f"{ext} missing from EXPENSIVE_BINARY")

    def test_all_expensive_binary_extensions_produce_findings(self):
        for ext in EXPENSIVE_BINARY:
            with self.subTest(ext=ext):
                result = detect_pdf_ingestion_inline(f"file{ext}", 10 * 1024 * 1024, ext)
                self.assertIsNotNone(result, msg=f"{ext} should produce a finding")


if __name__ == "__main__":
    unittest.main()
