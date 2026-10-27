"""Translate the Lua patterns used by the check modules into Python regexes.

Module:Check for unknown parameters (``regexp1 = image[0-9]+``) and
Module:Check for deprecated parameters (``_regexp1 = blank(%d+)_name =
custom_label%1``) take Lua patterns.  Only the parts of the Lua syntax those
templates actually use are supported; anything else raises LuaPatternError so
the rule is reported rather than guessed at.
"""

import re
import string

__all__ = ['LuaPatternError', 'compile_lua', 'lua_replacement']


class LuaPatternError(ValueError):
    pass


# %x classes, as the inside of a [...] set.  Upper-case letters negate.
_CLASSES = {
    'a': 'A-Za-z',
    'c': '\\x00-\\x1f\\x7f',
    'd': '0-9',
    'l': 'a-z',
    'p': re.escape(string.punctuation),
    's': ' \\t\\n\\r\\f\\v',
    'u': 'A-Z',
    'w': 'A-Za-z0-9',
    'x': '0-9A-Fa-f',
}


def _class(letter: str, in_set: bool) -> str:
    inner = _CLASSES.get(letter.lower())
    if inner is None:
        return re.escape(letter)
    if letter.isupper():
        if in_set:
            raise LuaPatternError(f'negated class %{letter} inside a set')
        return f'[^{inner}]'
    return inner if in_set else f'[{inner}]'


def _set(pattern: str, i: int) -> tuple[str, int]:
    """Translate the [...] set starting at pattern[i]; return it and the
    index just past its closing bracket."""
    out = ['[']
    i += 1
    if i < len(pattern) and pattern[i] == '^':
        out.append('^')
        i += 1
    first = True
    while i < len(pattern):
        c = pattern[i]
        if c == ']' and not first:
            out.append(']')
            return ''.join(out), i + 1
        if c == '%' and i + 1 < len(pattern):
            out.append(_class(pattern[i + 1], in_set=True))
            i += 2
        elif c == '-' and not first and i + 1 < len(pattern) and pattern[i + 1] != ']':
            out.append('-')
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
        first = False
    raise LuaPatternError('unclosed [')


def compile_lua(pattern: str, anchored: bool = True) -> re.Pattern:
    """Compile a Lua pattern; ``anchored`` wraps it in ^...$ as both check
    modules do."""
    out: list[str] = []
    atom = False        # whether the last token can take a quantifier
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == '%':
            if i + 1 >= len(pattern):
                raise LuaPatternError('pattern ends with %')
            n = pattern[i + 1]
            if n in 'bf':
                raise LuaPatternError(f'%{n} is not supported')
            out.append(f'(?:\\{n})' if n.isdigit() else _class(n, in_set=False))
            atom = not n.isdigit()
            i += 2
        elif c == '[':
            token, i = _set(pattern, i)
            out.append(token)
            atom = True
        elif c in '*+?-' and atom:
            out.append('*?' if c == '-' else c)
            atom = False
            i += 1
        elif c == '^' and i == 0:
            out.append('^')
            i += 1
        elif c == '$' and i == len(pattern) - 1:
            out.append('$')
            i += 1
        elif c == '.':
            out.append('.')
            atom = True
            i += 1
        elif c in '()':
            out.append(c)
            atom = False
            i += 1
        else:
            out.append(re.escape(c))
            atom = True
            i += 1
    body = ''.join(out)
    if anchored:
        body = f'^(?:{body})$'
    try:
        return re.compile(body, re.DOTALL)
    except re.error as e:
        raise LuaPatternError(str(e)) from e


def lua_replacement(repl: str) -> str:
    """Translate a Lua gsub replacement (``custom_label%1``) into a
    re.sub template."""
    out = []
    i = 0
    while i < len(repl):
        c = repl[i]
        if c == '%' and i + 1 < len(repl):
            n = repl[i + 1]
            out.append(f'\\g<{n}>' if n.isdigit() else n.replace('\\', '\\\\'))
            i += 2
        else:
            out.append('\\\\' if c == '\\' else c)
            i += 1
    return ''.join(out)
