#!/usr/bin/env python3
"""
Language parser for distributed-system-style projects.
Given a block (file_path, start_line, end_line), extracts:
- Full qualified function name (e.g. Class.method in Java, module.Class.method in Go)
- Docstring / leading comment block
- Full code block of the enclosing function/method.
"""

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

# Optional: use FQFNExtractor for refinement when available
try:
    from fqfn_extractor import FQFNExtractor

    _HAS_FQFN = True
except ImportError:
    _HAS_FQFN = False


# ---------------------------------------------------------------------------
# Language detection (aligned with text_splitter.LANGUAGE_MAP)
# ---------------------------------------------------------------------------
EXT_TO_LANG = {
    "java": "java",
    "scala": "scala",
    "py": "python",
    "python": "python",
    "go": "go",
    "cpp": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "c": "c",
    "h": "c",
    "rs": "rust",
    "rust": "rust",
    "js": "javascript",
    "ts": "typescript",
}


def detect_language(file_path: str) -> Optional[str]:
    """Detect language from file path. Returns tree-sitter language name or None."""
    ext = (file_path or "").split(".")[-1].lower()
    return EXT_TO_LANG.get(ext)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------
@dataclass
class ParsedBlock:
    """Result of parsing a block: FQFN, docstring, and full code."""

    file_path: str
    start_line: int  # 1-based, first line of the function
    end_line: int  # 1-based, last line of the function
    fqfn: Optional[str] = (
        None  # e.g. "ClassName.methodName" or "package.ClassName.methodName"
    )
    docstring: Optional[str] = None  # leading doc comment / docstring
    code_block: Optional[str] = None  # full source of the function
    language: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self):
        return {
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "fqfn": self.fqfn,
            "docstring": self.docstring,
            "code_block": self.code_block,
            "language": self.language,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Docstring extraction (lines immediately before the node)
# ---------------------------------------------------------------------------
def _extract_docstring_before(
    source: str,
    node_start_line_0: int,
    comment_patterns: List[Tuple[str, Optional[str]]],
) -> Optional[str]:
    """
    Extract contiguous comment/docstring lines immediately before a given line.
    comment_patterns: list of (line_comment_regex, None) or (block_start_regex, block_end_regex).
    """
    if node_start_line_0 <= 0:
        return None
    lines = source.split("\n")
    doc_lines: List[str] = []
    i = node_start_line_0 - 1  # previous line (0-based index)
    while i >= 0:
        line = lines[i]
        stripped = line.strip()
        matched = False
        for start_pat, end_pat in comment_patterns:
            if end_pat is None:
                if re.match(start_pat, stripped):
                    doc_lines.append(line)
                    matched = True
                    break
            else:
                if re.search(end_pat, line):
                    block = [line]
                    j = i - 1
                    while j >= 0 and not re.search(start_pat, lines[j]):
                        block.append(lines[j])
                        j -= 1
                    if j >= 0:
                        block.append(lines[j])
                    doc_lines = list(reversed(block)) + doc_lines
                    i = j - 1
                    matched = True
                    break
        if not matched or (not stripped and not doc_lines):
            break
        i -= 1
    if not doc_lines:
        return None
    return "\n".join(reversed(doc_lines)).strip()


# Per-language comment patterns: (line_comment_regex, block_end_regex or None)
_DOC_PATTERNS = {
    "java": [
        (r"^\s*//.*", None),
        (r"/\*\*", r"\*/"),  # Javadoc
        (r"/\*", r"\*/"),
    ],
    "python": [
        (r"^\s*#.*", None),
        (r'^\s*"""', r'"""'),
        (r"^\s*'''", r"'''"),
    ],
    "go": [
        (r"^\s*//.*", None),
        (r"/\*", r"\*/"),
    ],
    "rust": [
        (r"^\s*//!?.*", None),
        (r"/\*", r"\*/"),
    ],
    "scala": [
        (r"^\s*//.*", None),
        (r"/\*\*", r"\*/"),
        (r"/\*", r"\*/"),
    ],
    "c": [
        (r"^\s*//.*", None),
        (r"/\*", r"\*/"),
    ],
    "cpp": [
        (r"^\s*//.*", None),
        (r"/\*", r"\*/"),
    ],
}


def _get_docstring(source: str, node: Node, lang: str) -> Optional[str]:
    """Get docstring for a node: lines immediately above it that are comments."""
    start_line_0 = node.start_point[0]
    patterns = _DOC_PATTERNS.get(lang, [(r"^\s*//.*", None), (r"/\*", r"\*/")])
    return _extract_docstring_before(source, start_line_0, patterns)


# ---------------------------------------------------------------------------
# Tree-sitter helpers: find enclosing function and get name
# ---------------------------------------------------------------------------
def _node_contains_line(node: Node, line_1based: int) -> bool:
    """True if node contains the given 1-based line."""
    start_0 = node.start_point[0]
    end_0 = node.end_point[0]
    return start_0 + 1 <= line_1based <= end_0 + 1


def _node_overlaps_range(node: Node, start_line: int, end_line: int) -> bool:
    """True if node overlaps [start_line, end_line] (1-based inclusive)."""
    s0, e0 = node.start_point[0] + 1, node.end_point[0] + 1
    return not (end_line < s0 or start_line > e0)


def _overlap_size(node: Node, start_line: int, end_line: int) -> int:
    """Number of lines in [start_line, end_line] that the node covers (1-based inclusive)."""
    s0, e0 = node.start_point[0] + 1, node.end_point[0] + 1
    overlap_start = max(start_line, s0)
    overlap_end = min(end_line, e0)
    return max(0, overlap_end - overlap_start + 1)


def _node_text(source: str, node: Node) -> str:
    """Get node text; tree-sitter uses byte offsets so we must slice bytes, not str."""
    if node is None:
        return ""
    # Tree-sitter node.start_byte/end_byte are byte offsets; slicing a str uses character indices
    source_bytes = source.encode("utf-8") if isinstance(source, str) else source
    return source_bytes[node.start_byte : node.end_byte].decode(
        "utf-8", errors="replace"
    )


def _child_by_name(node: Node, name: str) -> Optional[Node]:
    for i in range(node.child_count):
        c = node.child(i)
        if c and getattr(c, "type", None) == name:
            return c
    # tree-sitter uses field names
    return (
        node.child_by_field_name(name) if hasattr(node, "child_by_field_name") else None
    )


def _name_from_node(source: str, node: Node, field_name: str = "name") -> Optional[str]:
    """Get identifier name from a node (e.g. 'name' field)."""
    name_node = (
        node.child_by_field_name(field_name)
        if hasattr(node, "child_by_field_name")
        else None
    )
    if name_node is None:
        for i in range(node.child_count):
            c = node.child(i)
            if c and "name" in (getattr(c, "type", "") or ""):
                name_node = c
                break
    if name_node:
        return _node_text(source, name_node).strip()
    return None


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------
_FUNCTION_NODE_TYPES_JAVA = (
    "method_declaration",
    "constructor_declaration",
    "class_declaration",
)


def _parse_java(
    source: str, root: Node, start_line: int, end_line: int
) -> Optional[Tuple[Node, List[str]]]:
    """Find smallest enclosing method/constructor; if block spans functions, pick by max overlap."""
    best: Optional[Tuple[Node, List[str]]] = None
    fallback_candidates: List[Tuple[Node, List[str]]] = []

    def walk(node: Node, class_stack: List[str]) -> None:
        nonlocal best
        if not _node_overlaps_range(node, start_line, end_line):
            return
        kind = node.type
        if kind == "class_declaration":
            name = _name_from_node(source, node)
            if name:
                class_stack = class_stack + [name]
        if kind in ("method_declaration", "constructor_declaration"):
            name = _name_from_node(source, node)
            if name:
                if _node_contains_line(node, start_line):
                    if best is None or (node.end_point[0] - node.start_point[0]) < (
                        best[0].end_point[0] - best[0].start_point[0]
                    ):
                        best = (node, list(class_stack))
                else:
                    fallback_candidates.append((node, list(class_stack)))
        for i in range(node.child_count):
            c = node.child(i)
            if c:
                walk(c, class_stack)

    walk(root, [])
    if best is not None:
        return best
    # Block may span functions or start in comments: pick overlapping function with max overlap
    if fallback_candidates:
        best_candidate = max(
            fallback_candidates,
            key=lambda t: _overlap_size(t[0], start_line, end_line),
        )
        return best_candidate
    return None


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
_FUNCTION_NODE_TYPES_PYTHON = ("function_definition", "class_definition")


def _parse_python(
    source: str, root: Node, start_line: int, end_line: int
) -> Optional[Tuple[Node, List[str]]]:
    best: Optional[Tuple[Node, List[str]]] = None
    fallback_candidates: List[Tuple[Node, List[str]]] = []

    def walk(node: Node, class_stack: List[str]) -> None:
        nonlocal best
        if not _node_overlaps_range(node, start_line, end_line):
            return
        kind = node.type
        if kind == "class_definition":
            name = _name_from_node(source, node)
            if name:
                class_stack = class_stack + [name]
        if kind == "function_definition":
            name = _name_from_node(source, node)
            if name:
                if _node_contains_line(node, start_line):
                    if best is None or (
                        node.end_point[0] - node.start_point[0]
                        < best[0].end_point[0] - best[0].start_point[0]
                    ):
                        best = (node, list(class_stack))
                else:
                    fallback_candidates.append((node, list(class_stack)))
        for i in range(node.child_count):
            c = node.child(i)
            if c:
                walk(c, class_stack)

    walk(root, [])
    if best is not None:
        return best
    if fallback_candidates:
        return max(
            fallback_candidates,
            key=lambda t: _overlap_size(t[0], start_line, end_line),
        )
    return None


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------
def _parse_go(
    source: str, root: Node, start_line: int, end_line: int
) -> Optional[Tuple[Node, List[str]]]:
    best: Optional[Tuple[Node, List[str]]] = None
    fallback_candidates: List[Tuple[Node, List[str]]] = []

    def walk(node: Node, receiver_stack: List[str]) -> None:
        nonlocal best
        if not _node_overlaps_range(node, start_line, end_line):
            return
        kind = node.type
        if kind == "type_declaration":
            type_spec = _child_by_name(node, "type_spec") or node.child_by_field_name(
                "type_spec"
            )
            if type_spec:
                name_node = (
                    type_spec.child_by_field_name("name")
                    if hasattr(type_spec, "child_by_field_name")
                    else None
                )
                if name_node:
                    name = _node_text(source, name_node).strip()
                    if name:
                        receiver_stack = receiver_stack + [name]
        if kind == "function_declaration":
            name = _name_from_node(source, node)
            if name:
                if _node_contains_line(node, start_line):
                    if best is None or (
                        node.end_point[0] - node.start_point[0]
                        < best[0].end_point[0] - best[0].start_point[0]
                    ):
                        best = (node, list(receiver_stack))
                else:
                    fallback_candidates.append((node, list(receiver_stack)))
        if kind == "method_declaration":
            name = _name_from_node(source, node)
            if name:
                rec = (
                    node.child_by_field_name("receiver")
                    if hasattr(node, "child_by_field_name")
                    else None
                )
                rec_type = ""
                if rec and rec.child_count >= 2:
                    type_node = rec.child(1)
                    if type_node:
                        rec_type = _node_text(source, type_node).strip().strip("()*")
                stack = ([rec_type] if rec_type else []) + list(receiver_stack)
                if _node_contains_line(node, start_line):
                    if best is None or (
                        node.end_point[0] - node.start_point[0]
                        < best[0].end_point[0] - best[0].start_point[0]
                    ):
                        best = (node, stack)
                else:
                    fallback_candidates.append((node, stack))
        for i in range(node.child_count):
            c = node.child(i)
            if c:
                walk(c, receiver_stack)

    walk(root, [])
    if best is not None:
        return best
    if fallback_candidates:
        return max(
            fallback_candidates,
            key=lambda t: _overlap_size(t[0], start_line, end_line),
        )
    return None


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------
def _parse_rust(
    source: str, root: Node, start_line: int, end_line: int
) -> Optional[Tuple[Node, List[str]]]:
    best: Optional[Tuple[Node, List[str]]] = None
    fallback_candidates: List[Tuple[Node, List[str]]] = []

    def walk(node: Node, context_stack: List[str]) -> None:
        nonlocal best
        if not _node_overlaps_range(node, start_line, end_line):
            return
        kind = node.type
        if kind == "struct_item":
            name = _name_from_node(source, node)
            if name:
                context_stack = context_stack + [name]
        if kind == "enum_item":
            name = _name_from_node(source, node)
            if name:
                context_stack = context_stack + [name]
        if kind == "impl_item":
            for i in range(node.child_count):
                c = node.child(i)
                if c and getattr(c, "type", None) == "type_identifier":
                    context_stack = context_stack + [_node_text(source, c).strip()]
                    break
        if kind == "function_item":
            name = _name_from_node(source, node)
            if name:
                if _node_contains_line(node, start_line):
                    if best is None or (
                        node.end_point[0] - node.start_point[0]
                        < best[0].end_point[0] - best[0].start_point[0]
                    ):
                        best = (node, list(context_stack))
                else:
                    fallback_candidates.append((node, list(context_stack)))
        for i in range(node.child_count):
            c = node.child(i)
            if c:
                walk(c, context_stack)

    walk(root, [])
    if best is not None:
        return best
    if fallback_candidates:
        return max(
            fallback_candidates,
            key=lambda t: _overlap_size(t[0], start_line, end_line),
        )
    return None


# ---------------------------------------------------------------------------
# Scala
# ---------------------------------------------------------------------------
def _parse_scala(
    source: str, root: Node, start_line: int, end_line: int
) -> Optional[Tuple[Node, List[str]]]:
    best: Optional[Tuple[Node, List[str]]] = None
    fallback_candidates: List[Tuple[Node, List[str]]] = []

    def walk(node: Node, class_stack: List[str]) -> None:
        nonlocal best
        if not _node_overlaps_range(node, start_line, end_line):
            return
        kind = node.type
        if kind in ("class_definition", "object_definition", "trait_definition"):
            name = _name_from_node(source, node)
            if name:
                class_stack = class_stack + [name]
        if kind == "function_definition":
            name = _name_from_node(source, node)
            if name:
                if _node_contains_line(node, start_line):
                    if best is None or (
                        node.end_point[0] - node.start_point[0]
                        < best[0].end_point[0] - best[0].start_point[0]
                    ):
                        best = (node, list(class_stack))
                else:
                    fallback_candidates.append((node, list(class_stack)))
        for i in range(node.child_count):
            c = node.child(i)
            if c:
                walk(c, class_stack)

    walk(root, [])
    if best is not None:
        return best
    if fallback_candidates:
        return max(
            fallback_candidates,
            key=lambda t: _overlap_size(t[0], start_line, end_line),
        )
    return None


# ---------------------------------------------------------------------------
# C / C++
# ---------------------------------------------------------------------------
def _parse_c_cpp(
    source: str, root: Node, start_line: int, end_line: int
) -> Optional[Tuple[Node, List[str]]]:
    best: Optional[Tuple[Node, List[str]]] = None
    fallback_candidates: List[Tuple[Node, List[str]]] = []

    def walk(node: Node, class_stack: List[str]) -> None:
        nonlocal best
        if not _node_overlaps_range(node, start_line, end_line):
            return
        kind = node.type
        if kind in ("class_specifier", "struct_specifier"):
            name_node = (
                node.child_by_field_name("name")
                if hasattr(node, "child_by_field_name")
                else None
            )
            if name_node:
                name = _node_text(source, name_node).strip()
                if name:
                    class_stack = class_stack + [name]
        if kind == "function_definition":
            decl = (
                node.child_by_field_name("declarator")
                if hasattr(node, "child_by_field_name")
                else None
            )
            name = _name_from_node(source, decl) if decl else None
            if name:
                if _node_contains_line(node, start_line):
                    if best is None or (
                        node.end_point[0] - node.start_point[0]
                        < best[0].end_point[0] - best[0].start_point[0]
                    ):
                        best = (node, list(class_stack))
                else:
                    fallback_candidates.append((node, list(class_stack)))
        for i in range(node.child_count):
            c = node.child(i)
            if c:
                walk(c, class_stack)

    walk(root, [])
    if best is not None:
        return best
    if fallback_candidates:
        return max(
            fallback_candidates,
            key=lambda t: _overlap_size(t[0], start_line, end_line),
        )
    return None


# ---------------------------------------------------------------------------
# Parser dispatch and main API
# ---------------------------------------------------------------------------
_LANG_PARSERS = {
    "java": _parse_java,
    "python": _parse_python,
    "go": _parse_go,
    "rust": _parse_rust,
    "scala": _parse_scala,
    "c": _parse_c_cpp,
    "cpp": _parse_c_cpp,
}


def _refine_fqfn_if_available(
    file_path: str,
    fqfn: str,
    start_line: int,
    end_line: int,
) -> str:
    """Use FQFNExtractor.refine_name when the language is supported."""
    if not _HAS_FQFN:
        return fqfn
    ext = (file_path or "").split(".")[-1].lower()
    try:
        if ext in ("java", "py", "scala"):
            return FQFNExtractor.refine_name(file_path, fqfn, start_line, end_line)
    except Exception:
        pass
    return fqfn


def parse_block(
    file_path: str,
    start_line: int,
    end_line: int,
    *,
    use_refine: bool = True,
) -> ParsedBlock:
    """
    Parse a block (file_path + start_line + end_line, 1-based inclusive) and return
    ParsedBlock with fqfn, docstring, and full code block of the enclosing function.

    When the block spans multiple functions (or starts in comments/whitespace):
    - First we try the function that contains start_line (smallest such).
    - If none contains start_line, we pick the function that overlaps the block
      and has the largest overlap in line count (so you get one representative
      function instead of None).

    Args:
        file_path: Path to the source file.
        start_line: First line of the block (1-based).
        end_line: Last line of the block (1-based).
        use_refine: If True, use FQFNExtractor.refine_name for Java/Python/Scala when available.

    Returns:
        ParsedBlock with fqfn, docstring, code_block, and possibly error.
    """
    result = ParsedBlock(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
    )
    if not os.path.isfile(file_path):
        result.error = f"File not found: {file_path}"
        return result

    lang = detect_language(file_path)
    if not lang:
        result.error = f"Unsupported or unknown file extension: {file_path}"
        return result
    result.language = lang

    parser_fn = _LANG_PARSERS.get(lang)
    if not parser_fn:
        result.error = f"No parser for language: {lang}"
        return result

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
    except Exception as e:
        result.error = str(e)
        return result

    try:
        parser = get_parser(lang)
        tree = parser.parse(bytes(source, "utf-8"))
        root = tree.root_node
        if root is None or root.has_error:
            result.error = "Parse error in file"
            return result
    except Exception as e:
        result.error = f"Parser error: {e}"
        return result

    parsed = parser_fn(source, root, start_line, end_line)
    if not parsed:
        result.error = f"No enclosing function found for lines {start_line}-{end_line}"
        # Still return the raw block of lines as code_block
        lines = source.split("\n")
        if 1 <= start_line <= len(lines) and 1 <= end_line <= len(lines):
            result.code_block = "\n".join(lines[start_line - 1 : end_line])
        return result

    node, name_stack = parsed
    name = _name_from_node(source, node)
    if not name:
        name = "<anonymous>"
    fqfn = ".".join(name_stack + [name]) if name_stack else name
    if use_refine:
        fqfn = _refine_fqfn_if_available(
            file_path,
            fqfn,
            node.start_point[0] + 1,
            node.end_point[0] + 1,
        )

    result.fqfn = fqfn
    result.start_line = node.start_point[0] + 1
    result.end_line = node.end_point[0] + 1
    result.code_block = _node_text(source, node)
    result.docstring = _get_docstring(source, node, lang)
    return result


def parse_block_from_dict(block: dict, *, use_refine: bool = True) -> ParsedBlock:
    """
    Parse from a block dict (e.g. from Block.to_dict() without class_func).
    Expects keys: file_path, start_line, end_line. Optional: class_func (ignored; we compute it).
    """
    return parse_block(
        block["file_path"],
        int(block["start_line"]),
        int(block["end_line"]),
        use_refine=use_refine,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 4:
        path, s, e = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
        r = parse_block(path, s, e)
        print("fqfn:", r.fqfn)
        print(
            "docstring:",
            r.docstring[:200] + "..."
            if r.docstring and len(r.docstring) > 200
            else r.docstring,
        )
        print("code_block:", r.code_block)
        print("code_block lines:", len((r.code_block or "").split("\n")))
        print("error:", r.error)
    else:
        print("Usage: language_parser.py <file_path> <start_line> <end_line>")
