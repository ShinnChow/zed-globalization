#!/usr/bin/env python3
"""替换保护规则 + AI 响应解析的回归测试

直接运行: python3 test_zedl10n.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from zedl10n.prompts import validate_placeholders  # noqa: E402
from zedl10n.replace import (  # noqa: E402
    _escape_for_rust_source, _find_protected_ranges, _replace_skip_protected,
)
from zedl10n.utils import parse_json_response  # noqa: E402


def translate(content: str, original: str, translated: str) -> str:
    """模拟 replace_in_source 对单条翻译的处理"""
    protected = _find_protected_ranges(content)
    safe = _escape_for_rust_source(translated)
    new_content, _ = _replace_skip_protected(
        content, f'"{original}"', f'"{safe}"', protected,
    )
    return new_content


def test_raw_string_regex_untouched() -> None:
    """issue #65: 原始字符串里的正则不能被转义写回破坏

    r"fn (.+?)\\(" 按普通字符串写回会变成 r"fn (.+?)\\\\("，
    正则引擎把 \\\\ 当作转义反斜杠，后面的 ( 成为未闭合分组 → 编译 panic。
    """
    src = 'static RE: LazyLock<Regex> =\n' \
          '    LazyLock::new(|| Regex::new(r"fn (.+?)\\(").expect("失败"));'
    out = translate(src, r'fn (.+?)\(', r'fn (.+?)\(')
    assert r'r"fn (.+?)\("' in out, out
    assert r'\\(' not in out, "原始字符串里的反斜杠被加倍了"


def test_raw_hash_string_untouched() -> None:
    """r#"..."# 形式同样受保护"""
    src = 'let re = Regex::new(r#"\\d+\\s*"#).unwrap();'
    out = translate(src, r'\d+\s*', '数字')
    assert r'r#"\d+\s*"#' in out, out


def test_key_context_untouched() -> None:
    """issue #66: key context 是代码标识符，译成中文后 keymap 匹配不上"""
    src = 'dispatch_context.add("Terminal");\n' \
          'key_context.set("mode", "full");'
    out = translate(src, "Terminal", "终端")
    out = translate(out, "full", "完整")
    assert 'dispatch_context.add("Terminal")' in out, out
    assert 'key_context.set("mode", "full")' in out, out


def test_key_context_parse_untouched() -> None:
    """KeyContext::parse("editor mode=full") 同样是标识符表达式"""
    src = 'let ctx = KeyContext::parse("editor mode=full").unwrap();'
    out = translate(src, "editor mode=full", "编辑器 模式=完整")
    assert 'KeyContext::parse("editor mode=full")' in out, out


def test_serialized_item_kind_untouched() -> None:
    """issue #66: 序列化标识符被翻译会破坏工作区持久化"""
    src = 'impl SerializableItem for TerminalView {\n' \
          '    fn serialized_item_kind() -> &\'static str {\n' \
          '        "Terminal"\n' \
          '    }\n}'
    out = translate(src, "Terminal", "终端")
    assert '"Terminal"' in out, out
    assert "终端" not in out, out


def test_ui_text_still_translated() -> None:
    """防过度保护：保护区之外的同名字符串仍要翻译"""
    src = 'dispatch_context.add("Terminal");\n' \
          'let label = "Terminal";'
    out = translate(src, "Terminal", "终端")
    assert 'dispatch_context.add("Terminal")' in out, out
    assert 'let label = "终端";' in out, out


def test_existing_protections_intact() -> None:
    """原有规则不回归：字节串与属性宏仍受保护，#[error] 仍可翻译"""
    src = 'const MAGIC: &[u8] = b"Terminal";\n' \
          '#[serde(rename = "Terminal")]\n' \
          '#[error("Terminal")]'
    out = translate(src, "Terminal", "终端")
    assert 'b"Terminal"' in out, out
    assert '#[serde(rename = "Terminal")]' in out, out
    assert '#[error("终端")]' in out, "用户可见的错误消息应该被翻译"


def test_escape_still_works_for_normal_strings() -> None:
    """普通字符串的转义逻辑保持不变"""
    assert _escape_for_rust_source('行1\n行2') == '行1\\n行2'
    assert _escape_for_rust_source('说"你好"') == '说\\"你好\\"'
    assert _escape_for_rust_source(r'路径\n换行') == r'路径\n换行'


def test_nested_value_dropped() -> None:
    """模型把译文包成对象时必须视同解析失败，交给降级链重试

    放行的话下游 extract_placeholders 会对 dict 调 .replace()，
    异常在 try 之外冒泡，整个 workflow 挂掉。
    """
    raw = '{"Hello": {"translation": "你好"}}'
    assert parse_json_response(raw) == {}, "嵌套对象应被丢弃以触发降级"


def test_partial_nested_keeps_valid() -> None:
    """部分条目异常时保留合法的，异常条目视同未翻译"""
    raw = '{"Hello": "你好", "Bye": {"t": "再见"}, "N": 42, "X": null}'
    assert parse_json_response(raw) == {"Hello": "你好"}


def test_non_object_toplevel() -> None:
    """顶层不是对象时不能当成翻译结果"""
    assert parse_json_response("[1, 2, 3]") == {}
    assert parse_json_response('"just a string"') == {}


def test_normal_json_unaffected() -> None:
    """正常响应与 markdown 包裹的解析行为不变"""
    assert parse_json_response('{"Hello": "你好"}') == {"Hello": "你好"}
    fenced = '```json\n{"Hello": "你好", "Bye": ""}\n```'
    assert parse_json_response(fenced) == {"Hello": "你好", "Bye": ""}


def test_bad_response_no_longer_crashes_validation() -> None:
    """回归 CI 崩溃：解析结果必须能安全喂给占位符校验"""
    raw = '{"a {} b": {"translation": "甲 {} 乙"}, "c {}": "丙 {}"}'
    result = parse_json_response(raw)
    validate_placeholders(result)  # 修复前这里 AttributeError
    assert result == {"c {}": "丙 {}"}


def main() -> int:
    logging.disable(logging.WARNING)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
