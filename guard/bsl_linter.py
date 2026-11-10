from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Optional

logger = logging.getLogger("kwork-guard.linter")


@dataclass
class LintIssue:
    code: str
    line: int
    message: str
    severity: str
    col: int = 1


BLOCK_PAIRS = {
    "если": "конецесли",
    "цикл": "конеццикла",
    "попытка": "конецпопытки",
    "процедура": "конецпроцедуры",
    "функция": "конецфункции",
    "#область": "#конецобласти",
    "#если": "#конецесли",
}


def clean_line_content(raw_line: str, in_multiline_string: bool) -> tuple[str, bool]:
    result_chars: list[str] = []
    idx = 0
    length = len(raw_line)
    inside_str = in_multiline_string

    while idx < length:
        char = raw_line[idx]
        next_char = raw_line[idx + 1] if idx + 1 < length else ""

        if inside_str:
            if char == '"':
                if next_char == '"':
                    idx += 2
                    continue
                inside_str = False
                idx += 1
                continue
            idx += 1
            continue

        if char == "/" and next_char == "/":
            break

        if char == '"':
            inside_str = True
            idx += 1
            continue

        if char == "|" and len(result_chars) == 0:
            inside_str = True
            idx += 1
            continue

        result_chars.append(char)
        idx += 1

    return "".join(result_chars), inside_str


def lint_bsl_code(
    code: str,
    profile: str = "common",
    is_object_module: bool = False,
) -> list[LintIssue]:
    code = code.lstrip("\ufeff")
    issues: list[LintIssue] = []
    raw_lines = code.splitlines()

    in_multiline_string = False
    cleaned_lines: list[tuple[int, str]] = []
    for line_idx, raw_line in enumerate(raw_lines, start=1):
        cleaned_text, in_multiline_string = clean_line_content(raw_line, in_multiline_string)
        cleaned_lines.append((line_idx, cleaned_text))

    for line_idx, raw_line in enumerate(raw_lines, start=1):
        for template_match in re.finditer(r"\{\{([^}]+)\}\}", raw_line):
            issues.append(
                LintIssue(
                    code="BSL003_UNRESOLVED_TEMPLATE",
                    line=line_idx,
                    message=f"Незамененный шаблон: {{{{{template_match.group(1)}}}}}",
                    severity="ERROR",
                )
            )

        guid_matches = re.finditer(
            r"""(?i)["'][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}["']""",
            raw_line,
        )
        for _ in guid_matches:
            issues.append(
                LintIssue(
                    code="BSL009_HARDCODED_GUID",
                    line=line_idx,
                    message="Обнаружен жестко закодированный литерал UUID/GUID",
                    severity="WARNING",
                )
            )

    block_stack: list[tuple[str, int]] = []
    loop_depth = 0
    try_depth = 0

    token_pattern = re.compile(
        r"(?i)(#область|#конецобласти|#если|#конецесли)\b|\b(если|иначеесли|конецесли|пока|для|цикл|конеццикла|попытка|исключение|конецпопытки|процедура|конецпроцедуры|функция|конецфункции)\b"
    )

    for line_idx, cleaned_text in cleaned_lines:
        if loop_depth > 0:
            if re.search(r"(?i)\.(?:Выполнить|Выбрать)\s*\(", cleaned_text):
                issues.append(
                    LintIssue(
                        code="BSL002_QUERY_IN_LOOP",
                        line=line_idx,
                        message="Обнаружен вызов запроса или выборки внутри цикла",
                        severity="ERROR",
                    )
                )

        if try_depth > 0:
            if re.search(r"(?i)\bНачатьТранзакцию\s*\(", cleaned_text):
                issues.append(
                    LintIssue(
                        code="BSL006_TRANSACTION_USAGE",
                        line=line_idx,
                        message="Вызов НачатьТранзакцию() внутри блока Попытка нарушает стандарты 1С",
                        severity="ERROR",
                    )
                )

        if re.search(r"(?i)\bТекущиеДанные\s*=", cleaned_text):
            issues.append(
                LintIssue(
                    code="BSL008_READONLY_CURRENTDATA",
                    line=line_idx,
                    message="Попытка присвоения свойству ТекущиеДанные, доступному только для чтения",
                    severity="ERROR",
                )
            )

        if re.search(r"(?i)\bПерейти\s+~[A-Za-zА-Яа-я0-9_]+", cleaned_text):
            issues.append(
                LintIssue(
                    code="BSL010_DYNAMIC_GOTO",
                    line=line_idx,
                    message="Оператор безусловного перехода Перейти запрещен стандартами разработки 1С",
                    severity="ERROR",
                )
            )

        matches = list(token_pattern.finditer(cleaned_text))
        for token_match in matches:
            tok = (token_match.group(1) or token_match.group(2)).lower()

            if tok == "#область":
                block_stack.append(("#область", line_idx))
            elif tok == "#конецобласти":
                if block_stack and block_stack[-1][0] == "#область":
                    block_stack.pop()
                else:
                    issues.append(
                        LintIssue(
                            code="BSL001_UNBALANCED_BLOCKS",
                            line=line_idx,
                            message="Неожиданный #КонецОбласти",
                            severity="ERROR",
                        )
                    )
            elif tok == "#если":
                block_stack.append(("#если", line_idx))
            elif tok == "#конецесли":
                if block_stack and block_stack[-1][0] == "#если":
                    block_stack.pop()
                else:
                    issues.append(
                        LintIssue(
                            code="BSL001_UNBALANCED_BLOCKS",
                            line=line_idx,
                            message="Неожиданный #КонецЕсли",
                            severity="ERROR",
                        )
                    )
            elif tok == "если":
                block_stack.append(("если", line_idx))
            elif tok == "конецесли":
                if block_stack and block_stack[-1][0] == "если":
                    block_stack.pop()
                else:
                    issues.append(
                        LintIssue(
                            code="BSL001_UNBALANCED_BLOCKS",
                            line=line_idx,
                            message="Неожиданный КонецЕсли",
                            severity="ERROR",
                        )
                    )
            elif tok in ("пока", "для"):
                block_stack.append(("цикл", line_idx))
                loop_depth += 1
            elif tok == "конеццикла":
                if block_stack and block_stack[-1][0] == "цикл":
                    block_stack.pop()
                    loop_depth = max(0, loop_depth - 1)
                else:
                    issues.append(
                        LintIssue(
                            code="BSL001_UNBALANCED_BLOCKS",
                            line=line_idx,
                            message="Неожиданный КонецЦикла",
                            severity="ERROR",
                        )
                    )
            elif tok == "попытка":
                block_stack.append(("попытка", line_idx))
                try_depth += 1
            elif tok == "конецпопытки":
                if block_stack and block_stack[-1][0] == "попытка":
                    block_stack.pop()
                    try_depth = max(0, try_depth - 1)
                else:
                    issues.append(
                        LintIssue(
                            code="BSL001_UNBALANCED_BLOCKS",
                            line=line_idx,
                            message="Неожиданный КонецПопытки",
                            severity="ERROR",
                        )
                    )
            elif tok == "процедура":
                block_stack.append(("процедура", line_idx))
            elif tok == "конецпроцедуры":
                if block_stack and block_stack[-1][0] == "процедура":
                    block_stack.pop()
                else:
                    issues.append(
                        LintIssue(
                            code="BSL001_UNBALANCED_BLOCKS",
                            line=line_idx,
                            message="Неожиданный КонецПроцедуры",
                            severity="ERROR",
                        )
                    )
            elif tok == "функция":
                block_stack.append(("функция", line_idx))
            elif tok == "конецфункции":
                if block_stack and block_stack[-1][0] == "функция":
                    block_stack.pop()
                else:
                    issues.append(
                        LintIssue(
                            code="BSL001_UNBALANCED_BLOCKS",
                            line=line_idx,
                            message="Неожиданный КонецФункции",
                            severity="ERROR",
                        )
                    )

    while block_stack:
        unclosed_tok, unclosed_line = block_stack.pop()
        issues.append(
            LintIssue(
                code="BSL001_UNBALANCED_BLOCKS",
                line=unclosed_line,
                message=f"Незакрытый блок {unclosed_tok}",
                severity="ERROR",
            )
        )

    clean_whole = "\n".join(text for _, text in cleaned_lines)
    empty_except_pattern = re.compile(
        r"(?ims)\bИсключение\b\s*(\bКонецПопытки\b|(?:\s*\n)+\s*\bКонецПопытки\b)"
    )
    for match in empty_except_pattern.finditer(clean_whole):
        line_num = clean_whole[: match.start()].count("\n") + 1
        issues.append(
            LintIssue(
                code="BSL005_EMPTY_EXCEPT",
                line=line_num,
                message="Пустой блок Исключение без обработки или логирования",
                severity="ERROR",
            )
        )

    func_pattern = re.compile(
        r"(?ims)\bФункция\s+([A-Za-zА-Яа-я0-9_]+)\b(.*?)\bКонецФункции\b"
    )
    for func_match in func_pattern.finditer(clean_whole):
        func_name = func_match.group(1)
        func_body = func_match.group(2)
        if not re.search(r"(?i)\bВозврат\b", func_body):
            line_num = clean_whole[: func_match.start()].count("\n") + 1
            issues.append(
                LintIssue(
                    code="BSL007_MISSING_RETURN",
                    line=line_num,
                    message=f"Функция '{func_name}' не содержит оператора Возврат",
                    severity="ERROR",
                )
            )

    if is_object_module and profile in ("vpf", "report", "processor"):
        reg_pattern = re.compile(
            r"(?i)\bФункция\s+СведенияОВнешнейОбработке\s*\(.*?\)\s*Экспорт",
            re.DOTALL,
        )
        if not reg_pattern.search(code):
            issues.append(
                LintIssue(
                    code="BSL004_MISSING_REGISTRATION",
                    line=1,
                    message="Отсутствует обязательная экспортная функция СведенияОВнешнейОбработке",
                    severity="ERROR",
                )
            )

    return issues


def filter_critical_issues(
    issues: list[LintIssue],
    include_warnings: bool = True,
) -> list[LintIssue]:
    allowed = {"ERROR", "CRITICAL"}
    if include_warnings:
        allowed.add("WARNING")
    return [item for item in issues if item.severity.upper() in allowed]


def format_diagnostics_for_llm(
    issues: list[LintIssue],
    include_warnings: bool = True,
) -> str:
    relevant = filter_critical_issues(issues, include_warnings=include_warnings)
    if not relevant:
        return ""
    lines = [f"{item.line}:{item.col} [{item.code}] {item.message}" for item in relevant]
    return "\n".join(lines)
