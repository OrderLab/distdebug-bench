import os
from enum import Enum

from tree_sitter import Parser
from tree_sitter_language_pack import get_parser


class LangType(Enum):
    """Enum for supported languages."""

    JAVA = "java"
    PYTHON = "py"
    SCALA = "scala"
    CPP = "cpp"
    C = "c"

    @classmethod
    def supported_languages(cls):
        """Return a list of supported languages."""
        return [cls.JAVA.value, cls.PYTHON.value, cls.SCALA.value]


class FQFNExtractor:
    """Class to extract fully qualified function names from source code."""

    langtype: LangType
    class_field_type: str
    function_field_type: str
    constructor_field_type: str
    class_field_name: str
    function_field_name: str
    parser: Parser

    def __init__(self, type: LangType):
        self.langtype = type
        match type:
            case LangType.JAVA:
                self.parser = get_parser("java")
                self.class_field_type = "class_declaration"
                self.function_field_type = "method_declaration"
                self.constructor_field_type = "constructor_declaration"
                self.class_field_name = "name"
                self.function_field_name = "name"
            case LangType.PYTHON:
                self.parser = get_parser("python")
                self.class_field_type = "class_definition"
                self.function_field_type = "function_definition"
                self.constructor_field_type = "function_definition"  # Python doesn't have a separate constructor node
                self.class_field_name = "name"
                self.function_field_name = "name"
            case LangType.SCALA:
                self.parser = get_parser("scala")
                self.class_field_type = "class_definition"
                self.function_field_type = "function_definition"
                self.constructor_field_type = (
                    "function_definition"  # constructors are also functions
                )
                self.class_field_name = "name"
                self.function_field_name = "name"
            case LangType.CPP:
                raise NotImplementedError(
                    "C++ language support is not implemented yet."
                )
            case LangType.C:
                raise NotImplementedError("C language support is not implemented yet.")
            case _:
                raise ValueError(f"Unsupported language type: {type}")

    def _node_text(self, code_bytes: bytes, node) -> str:
        """Get node text from source using byte range (py-tree-sitter Node has no .text)."""
        if node is None:
            return ""
        return (
            code_bytes[node.start_byte : node.end_byte]
            .decode("utf-8", errors="replace")
            .strip()
        )

    def get_all_fqfn(self, file_path: str):
        """Parse the file and return all fully qualified function names with start/end line numbers."""
        with open(file_path, "rb") as f:
            code_bytes = f.read()
        tree = self.parser.parse(code_bytes)
        root = tree.root_node

        def recursive_extract(node, class_stack=[]):
            results = []

            if node.type == self.class_field_type:
                # Push class name (use source slice; Node has no .text in py-tree-sitter)
                name_node = node.child_by_field_name(self.class_field_name)
                class_name = self._node_text(code_bytes, name_node) if name_node else ""
                class_stack.append(class_name)

                # Recurse into children
                for child in node.children:
                    results.extend(recursive_extract(child, class_stack))

                # Pop class name after processing
                class_stack.pop()

            elif node.type in (self.function_field_type, self.constructor_field_type):
                name_node = node.child_by_field_name(self.function_field_name)
                method_name = (
                    self._node_text(code_bytes, name_node) if name_node else ""
                )
                # Keep only identifier (strip trailing punctuation e.g. from misparsed spans)
                method_name = method_name.split("(")[0].split("<")[0].strip()
                fq_name = ".".join(class_stack + [method_name])
                # Append the fully qualified method name with start and end line numbers (inclusive)
                results.append((fq_name, node.start_point[0], node.end_point[0]))

                class_stack.append(method_name)
                for child in node.children:
                    results.extend(recursive_extract(child, class_stack))
                class_stack.pop()

            else:
                for child in node.children:
                    results.extend(recursive_extract(child, class_stack))

            return results

        return recursive_extract(root)

    @classmethod
    def refine_name(cls, file_path: str, name: str, start_line: int, end_line: int):
        """Refine the function name by file name and line numbers."""
        if not os.path.isfile(file_path):
            return name
        file_extension = file_path.split(".")[-1]
        if file_extension not in LangType.supported_languages():
            return name  # Unsupported language, return original name
        extractor = cls(LangType(file_extension))
        fqfn_list = extractor.get_all_fqfn(file_path)

        # Find the function name that matches the postfix
        matching_list = [
            (fqfn, start, end)
            for fqfn, start, end in fqfn_list
            if fqfn.split(".")[-1] == name.split(".")[-1]
        ]
        if not matching_list:
            return name
        if len(matching_list) == 1:
            return matching_list[0][0]

        # If multiple matches, return the one with the closest line numbers
        dists = [
            max(start_line - end, start - (end_line - 1), 0)
            for _, start, end in matching_list
        ]
        min_dist = min(dists)
        index = dists.index(min_dist)
        return matching_list[index][0]
