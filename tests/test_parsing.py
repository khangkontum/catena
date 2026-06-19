from pathlib import Path

from docling.datamodel.pipeline_options import RapidOcrOptions

from catena import parsing
from catena.parsing import ParsedDocument


def test_parse_pdfs_disables_ocr_for_text_layer(monkeypatch, tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    do_ocr_values: list[bool] = []

    class FakeParser:
        def __init__(self, *, do_ocr: bool) -> None:
            do_ocr_values.append(do_ocr)

        def parse_pdf(self, path: Path) -> ParsedDocument:
            return ParsedDocument(markdown="# paper", docling_json={}, chunks=[])

    monkeypatch.setattr(parsing, "DoclingParser", FakeParser)
    monkeypatch.setattr(parsing, "has_sufficient_text_layer", lambda path: True)

    result = parsing.parse_pdfs([pdf])

    assert result[0].document is not None
    assert do_ocr_values == [False]


def test_parse_pdfs_enables_ocr_for_low_text_pdf(monkeypatch, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"pdf")
    do_ocr_values: list[bool] = []

    class FakeParser:
        def __init__(self, *, do_ocr: bool) -> None:
            do_ocr_values.append(do_ocr)

        def parse_pdf(self, path: Path) -> ParsedDocument:
            return ParsedDocument(markdown="# scan", docling_json={}, chunks=[])

    monkeypatch.setattr(parsing, "DoclingParser", FakeParser)
    monkeypatch.setattr(parsing, "has_sufficient_text_layer", lambda path: False)

    result = parsing.parse_pdfs([pdf])

    assert result[0].document is not None
    assert do_ocr_values == [True]


def test_pdf_pipeline_options_use_mps_rapidocr_when_available(monkeypatch):
    monkeypatch.setattr(parsing, "_preferred_docling_device", lambda: "mps")

    options = parsing._pdf_pipeline_options(do_ocr=True)

    assert options.do_ocr is True
    assert options.accelerator_options.device == "mps"
    assert isinstance(options.ocr_options, RapidOcrOptions)
    assert options.ocr_options.backend == "torch"
    assert options.ocr_options.rapidocr_params["EngineConfig.torch.use_mps"] is True


def test_pdf_pipeline_options_disable_ocr(monkeypatch):
    monkeypatch.setattr(parsing, "_preferred_docling_device", lambda: "mps")

    options = parsing._pdf_pipeline_options(do_ocr=False)

    assert options.do_ocr is False
    assert options.accelerator_options.device == "mps"
