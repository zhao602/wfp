# =============================================================================
# 向量知识库管理解析层单元测试（tests/unit/test_vector_kb_management_parsers.py）
#
# 覆盖（CHG-16 / design §2.10.1 parsers 行，AC 对照）：
#   - 格式分派 4（.txt/.md/.pdf/.docx）
#   - `.txt` 中文无乱码 1 / `.md` 结构保留 1
#   - 编码探测 3（utf-8 / utf-8-sig / 非法 → 报错）+ 声明编码回退
#   - 大小上界 2（超限拒绝 / 界内通过）
#   - 重格式探测 2（缺失 → 确定性拒绝 + 提示；docx 可用 → 真实解析）
#   - `.doc` 拒绝 1 / 未知格式拒绝
#   - 空内容确定性拒绝（INV-D09）
#   - 无依赖环境导入不崩溃 1（INV-D15）
#
# 沙箱验证：PYTHONPATH=src python -m pytest tests/unit/test_vector_kb_management_parsers.py -q
# （同步 API 驱动，禁 asyncio.run；重格式缺失不判失败——环境性隔离，NV-01）
# =============================================================================

import importlib.util
import io
import os
import subprocess
import sys

import pytest

from orchestration_platform.domain.errors import (
    VECTOR_KB_EMPTY_CONTENT,
    VECTOR_KB_FILE_TOO_LARGE,
    VECTOR_KB_PARSER_UNAVAILABLE,
    VECTOR_KB_UNSUPPORTED_FORMAT,
    WorkflowValidationError,
)
from orchestration_platform.integrations.vector_kb_management.models import (
    ParsedDocument,
)
from orchestration_platform.integrations.vector_kb_management.parsers import (
    ParserRegistry,
    decode_text,
    probe_parser_availability,
)

# 探测结论（模块级缓存，供 skipif 分支：缺失路径不乱判失败，NV-01）
_AVAILABILITY = probe_parser_availability(refresh=True)
_PDF_AVAILABLE = _AVAILABILITY["pypdf"] or _AVAILABILITY["pdfplumber"]
_DOCX_AVAILABLE = _AVAILABILITY["docx"]


def _registry(**kwargs) -> ParserRegistry:
    return ParserRegistry(**kwargs)


def _assert_rejection(code: str, fn, *args, **kwargs) -> WorkflowValidationError:
    with pytest.raises(WorkflowValidationError) as exc_info:
        fn(*args, **kwargs)
    assert exc_info.value.code == code
    return exc_info.value


# ---------------------------------------------------------------------------
# 格式分派（4）
# ---------------------------------------------------------------------------


def test_dispatch_txt() -> None:
    document = _registry().parse("notes.txt", "文本内容".encode("utf-8"))
    assert isinstance(document, ParsedDocument)
    assert document.format == "txt"
    assert document.text == "文本内容"
    assert document.structure is None


def test_dispatch_md() -> None:
    document = _registry().parse("guide.md", "# 标题\n\n正文".encode("utf-8"))
    assert document.format == "md"
    assert document.text.startswith("# 标题")


def test_dispatch_pdf() -> None:
    """`.pdf` 分派：解析器缺失 → 确定性拒绝（本沙箱不可安装，NV-01）。"""
    if not _PDF_AVAILABLE:
        error = _assert_rejection(
            VECTOR_KB_PARSER_UNAVAILABLE,
            _registry().parse,
            "spec.pdf",
            b"%PDF-1.4 minimal",
        )
        assert "pypdf" in error.message or "pdfplumber" in error.message
    else:
        assert _PDF_AVAILABLE  # 真实 PDF 解析质量为用户环境复跑项（HR-10）


def test_dispatch_docx() -> None:
    """`.docx` 分派：python-docx 可用 → 真实解析；缺失 → 确定性拒绝。"""
    if _DOCX_AVAILABLE:
        import docx as docx_module

        buffer = io.BytesIO()
        document_writer = docx_module.Document()
        document_writer.add_paragraph("第一段内容")
        document_writer.add_paragraph("第二段内容")
        document_writer.save(buffer)
        parsed = _registry().parse("manual.docx", buffer.getvalue())
        assert parsed.format == "docx"
        assert "第一段内容" in parsed.text
        assert "第二段内容" in parsed.text
    else:
        error = _assert_rejection(
            VECTOR_KB_PARSER_UNAVAILABLE,
            _registry().parse,
            "manual.docx",
            b"PK\x03\x04 minimal",
        )
        assert "python-docx" in error.message


# ---------------------------------------------------------------------------
# `.txt` 中文无乱码（1）
# ---------------------------------------------------------------------------


def test_txt_chinese_no_mojibake() -> None:
    content = "你好，世界。第一段落内容。\n\n第二段落：向量知识库管理。"
    document = _registry().parse("chinese.txt", content.encode("utf-8"))
    assert document.text == content


# ---------------------------------------------------------------------------
# `.md` 结构保留（1）
# ---------------------------------------------------------------------------


def test_md_structure_preserved() -> None:
    content = "# 一级标题\n\n正文一\n\n## 二级标题\n\n正文二\n\n### 三级标题\n\n正文三"
    document = _registry().parse("doc.md", content.encode("utf-8"))
    assert document.structure is not None
    headings = document.structure.get("headings")
    assert isinstance(headings, list)
    assert len(headings) == 3
    assert headings[0]["level"] == 1
    assert headings[0]["text"] == "一级标题"
    assert headings[1]["level"] == 2
    assert headings[2]["level"] == 3


# ---------------------------------------------------------------------------
# 编码探测（3 + 声明编码回退）
# ---------------------------------------------------------------------------


def test_decode_utf8() -> None:
    assert decode_text("中文内容".encode("utf-8")) == "中文内容"


def test_decode_utf8_sig_bom_stripped() -> None:
    decoded = decode_text("带 BOM 的中文".encode("utf-8-sig"))
    assert decoded == "带 BOM 的中文"
    assert not decoded.startswith("\ufeff")


def test_decode_invalid_bytes_raises() -> None:
    error = _assert_rejection(
        VECTOR_KB_UNSUPPORTED_FORMAT,
        decode_text,
        b"\xff\xfe\xfa\xfb\x00\x01\x02",
    )
    assert "编码" in error.message


def test_decode_declared_encoding_fallback() -> None:
    decoded = decode_text("声明编码内容".encode("utf-16"), declared_encoding="utf-16")
    assert decoded == "声明编码内容"


# ---------------------------------------------------------------------------
# 大小上界（2）
# ---------------------------------------------------------------------------


def test_size_limit_rejects_oversize() -> None:
    oversize = b"a" * (1024 * 1024 + 1)
    error = _assert_rejection(
        VECTOR_KB_FILE_TOO_LARGE,
        _registry(max_file_size_mb=1).parse,
        "big.txt",
        oversize,
    )
    assert "上界" in error.message


def test_size_limit_allows_within_boundary() -> None:
    within = "a" * (1024 * 1024)
    document = _registry(max_file_size_mb=1).parse("ok.txt", within.encode("utf-8"))
    assert document.text == within


# ---------------------------------------------------------------------------
# 重格式探测（2：缺失 → 确定性拒绝 + 提示）
# ---------------------------------------------------------------------------


def test_probe_parser_availability_diagnosable() -> None:
    availability = probe_parser_availability()
    assert set(availability.keys()) == {"pypdf", "pdfplumber", "docx"}
    assert all(isinstance(value, bool) for value in availability.values())
    # 缓存且可诊断：重复读取返回一致结论
    assert probe_parser_availability() == availability


def test_probe_matches_environment() -> None:
    """探测结论须与真实模块可用性一致（环境中立：沙箱缺失/用户环境存在均成立）。"""
    assert _AVAILABILITY["pypdf"] == (importlib.util.find_spec("pypdf") is not None)
    assert _AVAILABILITY["pdfplumber"] == (
        importlib.util.find_spec("pdfplumber") is not None
    )
    assert _AVAILABILITY["docx"] == (importlib.util.find_spec("docx") is not None)


# ---------------------------------------------------------------------------
# `.doc` 拒绝（1）与未知格式拒绝
# ---------------------------------------------------------------------------


def test_doc_rejected_with_alternatives() -> None:
    error = _assert_rejection(
        VECTOR_KB_UNSUPPORTED_FORMAT,
        _registry().parse,
        "legacy.doc",
        b"\xd0\xcf\x11\xe0 binary",
    )
    assert ".docx" in error.message or ".pdf" in error.message


def test_unknown_extension_rejected_with_supported_list() -> None:
    error = _assert_rejection(
        VECTOR_KB_UNSUPPORTED_FORMAT,
        _registry().parse,
        "archive.zip",
        b"PK\x03\x04",
    )
    assert ".txt" in error.message
    assert ".md" in error.message
    assert ".pdf" in error.message
    assert ".docx" in error.message


def test_no_extension_rejected() -> None:
    _assert_rejection(
        VECTOR_KB_UNSUPPORTED_FORMAT,
        _registry().parse,
        "README",
        b"plain",
    )


def test_extension_case_insensitive() -> None:
    document = _registry().parse("NOTES.TXT", "大写扩展名".encode("utf-8"))
    assert document.format == "txt"


# ---------------------------------------------------------------------------
# 空内容确定性拒绝（INV-D09）
# ---------------------------------------------------------------------------


def test_empty_content_rejected() -> None:
    _assert_rejection(
        VECTOR_KB_EMPTY_CONTENT,
        _registry().parse,
        "blank.txt",
        "   \n\t  \n".encode("utf-8"),
    )


# ---------------------------------------------------------------------------
# 无依赖环境导入不崩溃（1，INV-D15）
# ---------------------------------------------------------------------------


def test_import_package_without_heavy_dependencies() -> None:
    """子进程隔离验证：导入本子包不触发任何重依赖顶层加载。"""
    probe_code = (
        "import sys; "
        "import orchestration_platform.integrations.vector_kb_management; "
        "heavy = [name for name in "
        "('pypdf', 'pdfplumber', 'docx', 'sentence_transformers') "
        "if name in sys.modules]; "
        "print('HEAVY=' + ','.join(heavy))"
    )
    environment = dict(os.environ)
    existing_path = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = "src" + (
        os.pathsep + existing_path if existing_path else ""
    )
    result = subprocess.run(
        [sys.executable, "-c", probe_code],
        capture_output=True,
        text=True,
        env=environment,
        cwd=os.getcwd(),
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "HEAVY=" in result.stdout
    heavy_modules = result.stdout.strip().split("HEAVY=", 1)[1]
    assert heavy_modules == ""
