# =============================================================================
# 向量知识库管理文件解析层（integrations/vector_kb_management/parsers.py）
#
# 职责：格式注册表 + 零依赖直读 + 重格式延迟导入探测 + 确定性拒绝
# （CHG-03 / design DC-01）。
# 设计意图（vector_kb_management design INV-D09 / INV-D13 / INV-D15）：
#   1. `.txt` / `.md` 零依赖直读（仅标准库）；`.md` 保留标题层级结构供
#      structural 分块；
#   2. `.pdf` / `.docx` 解析器一律方法内延迟导入（importlib）；
#      缺失 → 确定性拒绝 VECTOR_KB_PARSER_UNAVAILABLE + 可执行安装提示，
#      禁止以空内容冒充解析成功（INV-D09）；
#   3. 未知格式 → VECTOR_KB_UNSUPPORTED_FORMAT（附支持清单）；
#      `.doc` 本期明确不支持（给出格式替代建议）；
#   4. 编码探测：utf-8 → utf-8-sig（BOM 剥离）→ 声明编码 → 确定性报错
#      （禁静默乱码）；
#   5. 大小上界：默认 10 MB（可配置），超限确定性拒绝（不产生半写入）；
#   6. 真实 PDF/DOCX 解析质量为沙箱不可验证项（NV-01），验收以
#      “延迟导入探测 + 缺失时确定性拒绝”为准。
# =============================================================================

import importlib.util
import io
import re
from typing import Callable, Dict, List, Optional

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

# 支持格式清单（附于拒绝提示；.doc 本期不支持）
SUPPORTED_FORMATS = (".txt", ".md", ".pdf", ".docx")

# Markdown 标题行（^#{1,6}\s，供 structural 分块；零依赖）
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*)$")

# 探测结果缓存（可诊断；refresh=True 强制刷新）
_PROBE_CACHE: Optional[Dict[str, bool]] = None


def _module_available(module_name: str) -> bool:
    """标准库探测模块可用性（不触发重依赖顶层导入副作用）。"""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def probe_parser_availability(refresh: bool = False) -> Dict[str, bool]:
    """重格式解析器可用性探测（结果缓存且可诊断）。

    - pypdf / pdfplumber —— `.pdf` 首选与备选解析器；
    - docx               —— `.docx`（python-docx）；
    - 探测仅用 find_spec，不执行重依赖顶层导入。
    """
    global _PROBE_CACHE
    if _PROBE_CACHE is None or refresh:
        _PROBE_CACHE = {
            "pypdf": _module_available("pypdf"),
            "pdfplumber": _module_available("pdfplumber"),
            "docx": _module_available("docx"),
        }
    return dict(_PROBE_CACHE)


def decode_text(data: bytes, declared_encoding: Optional[str] = None) -> str:
    """编码探测解码：utf-8 → utf-8-sig（BOM 剥离）→ 声明编码 → 确定性报错。

    禁止静默乱码（AC-17）：全部探测失败 → VECTOR_KB_UNSUPPORTED_FORMAT。
    """
    try:
        text = data.decode("utf-8")
        if text.startswith("\ufeff"):
            return data.decode("utf-8-sig")
        return text
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    if declared_encoding:
        try:
            return data.decode(declared_encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    raise WorkflowValidationError(
        VECTOR_KB_UNSUPPORTED_FORMAT,
        "无法解码文件内容（支持编码：utf-8 / utf-8-sig / 显式声明编码；"
        "请将文件转存为 UTF-8 后重试）",
    )


def check_size_limit(data: bytes, max_file_size_mb: int) -> None:
    """大小上界校验（默认 10 MB）：超限确定性拒绝（不产生半写入）。"""
    limit_bytes = max_file_size_mb * 1024 * 1024
    if len(data) > limit_bytes:
        raise WorkflowValidationError(
            VECTOR_KB_FILE_TOO_LARGE,
            f"文件超过大小上界：{len(data)} 字节 > {max_file_size_mb} MB"
            f"（上限可经 vector_kb_management_max_file_size_mb 调整）",
        )


def parse_text(data: bytes, declared_encoding: Optional[str] = None) -> ParsedDocument:
    """`.txt` 零依赖直读（编码探测；纯文本无结构信息）。"""
    return ParsedDocument(
        text=decode_text(data, declared_encoding),
        format="txt",
        structure=None,
    )


def parse_markdown(
    data: bytes, declared_encoding: Optional[str] = None
) -> ParsedDocument:
    """`.md` 零依赖直读：保留标题层级结构（供 structural 分块）。"""
    text = decode_text(data, declared_encoding)
    headings: List[Dict[str, object]] = []
    for line in text.splitlines():
        match = _HEADING_PATTERN.match(line)
        if match:
            headings.append(
                {"level": len(match.group(1)), "text": match.group(2).strip()}
            )
    return ParsedDocument(
        text=text,
        format="md",
        structure={"headings": headings},
    )


def parse_pdf(data: bytes, declared_encoding: Optional[str] = None) -> ParsedDocument:
    """`.pdf` 解析：pypdf（首选）/ pdfplumber（备选）方法内延迟导入。

    解析器缺失 → 确定性拒绝 + 可执行安装提示（INV-D09 / NV-01）。
    """
    del declared_encoding  # 二进制格式无编码探测语义
    availability = probe_parser_availability()
    text_parts: List[str] = []
    if availability["pypdf"]:
        module = importlib.import_module("pypdf")
        reader = module.PdfReader(io.BytesIO(data))
        for page in reader.pages:
            text_parts.append(page.extract_text() or "")
    elif availability["pdfplumber"]:
        module = importlib.import_module("pdfplumber")
        with module.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
    else:
        raise WorkflowValidationError(
            VECTOR_KB_PARSER_UNAVAILABLE,
            "PDF 解析器不可用：请安装 pypdf（或备选 pdfplumber）后重试；"
            "离线环境可先将文档导出为 .txt / .md 再导入",
        )
    return ParsedDocument(text="\n".join(text_parts), format="pdf", structure=None)


def parse_docx(data: bytes, declared_encoding: Optional[str] = None) -> ParsedDocument:
    """`.docx` 解析：python-docx 方法内延迟导入（缺失 → 确定性拒绝）。"""
    del declared_encoding  # 二进制格式无编码探测语义
    availability = probe_parser_availability()
    if not availability["docx"]:
        raise WorkflowValidationError(
            VECTOR_KB_PARSER_UNAVAILABLE,
            "DOCX 解析器不可用：请安装 python-docx 后重试；"
            "离线环境可先将文档另存为 .txt / .md 再导入",
        )
    module = importlib.import_module("docx")
    document = module.Document(io.BytesIO(data))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    return ParsedDocument(text=text, format="docx", structure=None)


class ParserRegistry:
    """格式注册表：扩展名分派 + 大小上界 + 空内容确定性拒绝（DC-01）。

    构造零副作用（不触盘、不探测依赖，懒初始化）；大小上界经构造注入
    （默认 10 MB），测试可覆盖。
    """

    def __init__(self, max_file_size_mb: int = 10) -> None:
        self._max_file_size_mb = max_file_size_mb
        self._parsers: Dict[str, Callable[[bytes, Optional[str]], ParsedDocument]] = {
            ".txt": parse_text,
            ".md": parse_markdown,
            ".pdf": parse_pdf,
            ".docx": parse_docx,
        }

    def parse(
        self,
        file_name: str,
        data: bytes,
        declared_encoding: Optional[str] = None,
    ) -> ParsedDocument:
        """依扩展名分派解析（file_name 仅作扩展名与元数据来源，不参与路径构造）。"""
        check_size_limit(data, self._max_file_size_mb)
        extension = self._extension_of(file_name)
        if extension == ".doc":
            raise WorkflowValidationError(
                VECTOR_KB_UNSUPPORTED_FORMAT,
                "旧版 .doc 二进制格式本期不支持：请先另存为 .docx"
                " 或导出为 .pdf 后再导入",
            )
        parser = self._parsers.get(extension)
        if parser is None:
            raise WorkflowValidationError(
                VECTOR_KB_UNSUPPORTED_FORMAT,
                "不支持的文件格式：仅支持 "
                + " / ".join(SUPPORTED_FORMATS)
                + "（.doc 本期不支持）",
            )
        document = parser(data, declared_encoding)
        if not document.text.strip():
            raise WorkflowValidationError(
                VECTOR_KB_EMPTY_CONTENT,
                "解析后无有效内容（拒绝以空内容冒充解析成功，INV-D09）",
            )
        return document

    @staticmethod
    def _extension_of(file_name: str) -> str:
        """提取小写扩展名（含点；无扩展名返回空字符串）。"""
        index = file_name.rfind(".")
        if index <= 0:
            return ""
        return file_name[index:].lower()
