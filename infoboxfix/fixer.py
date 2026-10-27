"""Fixing the calls to one template in an article's wikitext.

A port of Primefac's AWB infobox module ([[User:PrimeBOT/code dump#Infobox
parameter changes]] on English Wikipedia), driven by a TemplateInfo instead
of hand-written lists:

* calls to a redirect are renamed to the template itself (commonName);
* parameters with a removal rule are removed (paramKiller);
* deprecated parameters are renamed (paramReplace), always with its
  "only one" handling, so a rename never leaves two copies of a name:
  if the new name is already there, an empty or identical old parameter is
  dropped, an empty new one is dropped before the rename, and two different
  values are left for a human;
* each call is rebuilt one ``| name = value`` per line with the ``=``
  signs lined up, blank lines dropped and ``}}`` on its own line
  (infoboxCleanup), unless that would lose any text.

The AWB module's JoinLinks step isn't needed: it only kept AWB's regex
parser from splitting a link that has a line break in it, and
mwparserfromhell parses those correctly.
"""

import re
from dataclasses import dataclass, field

from mwparserfromhell.nodes import Template

from .rules import TemplateInfo
from .wikitext import fingerprint, normalize_template_name, param_key, parse, strip_comments

__all__ = ['PageResult', 'fix_page', 'align_call']


@dataclass
class PageResult:
    text: str = ''
    calls: int = 0
    renamed: list[tuple[str, str]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    bypassed: list[str] = field(default_factory=list)
    aligned: int = 0
    # Whether a parameter with a value was renamed or removed.  Changes to
    # empty parameters, bypassing redirects and lining up are all cosmetic.
    substantive: bool = False
    conflicts: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def _blank(value: object) -> bool:
    return not strip_comments(value).strip()


def _same(a: object, b: object) -> bool:
    return str(a).split() == str(b).split()


def _named(tpl: Template, name: str) -> list:
    return [p for p in tpl.params if p.showkey and param_key(p.name) == name]


def _rename(param, new: str) -> None:
    text = str(param.name)
    old = param_key(text)
    lead, trail = re.match(r'(\s*).*?(\s*)$', text, re.DOTALL).groups()
    param.name = lead + new + trail if strip_comments(text) == text else text.replace(old, new, 1)


def fix_page(text: str, info: TemplateInfo, *, align: bool = True) -> PageResult:
    result = PageResult(text=text)
    code = parse(text)
    calls = [t for t in code.filter_templates(recursive=True)
             if normalize_template_name(t.name) in info.names]
    # Inner calls first (a child infobox in |embedded=), so that rebuilding
    # the outer call picks up their changes.
    for tpl in reversed(calls):
        result.calls += 1
        _fix_call(tpl, info, result, align)
    result.text = str(code)
    return result


def _fix_call(tpl: Template, info: TemplateInfo, result: PageResult, align: bool) -> None:
    name = normalize_template_name(tpl.name)
    if name != info.name:
        lead, trail = re.match(r'(\s*).*?(\s*)$', str(tpl.name), re.DOTALL).groups()
        tpl.name = lead + info.name + trail
        result.bypassed.append(name)

    seen = set()
    for key in [param_key(p.name) for p in tpl.params if p.showkey]:
        if key in seen:
            continue
        seen.add(key)
        found = info.resolve(key)
        if found is None:
            continue
        olds = _named(tpl, key)
        if len(olds) > 1:
            result.conflicts.append(f'|{key}= appears {len(olds)} times')
            continue
        old = olds[0]
        if found.new is None:
            tpl.remove(old)
            result.removed.append(key)
            result.substantive |= not _blank(old.value)
            continue
        news = _named(tpl, found.new)
        if len(news) > 1:
            result.conflicts.append(f'|{found.new}= appears {len(news)} times')
            continue
        if news:
            new = news[0]
            if not str(old.value).strip() or _same(old.value, new.value):
                # The old one is empty or a copy of the new one: drop it
                tpl.remove(old)
                result.removed.append(key)
                result.substantive |= not _blank(old.value)
                continue
            if str(new.value).strip():
                result.conflicts.append(f'|{key}= and |{found.new}= are both set, to different values')
                continue
            # The new one is empty: drop it so the rename doesn't make a duplicate
            tpl.remove(new)
        _rename(old, found.new)
        result.renamed.append((key, found.new))
        result.substantive |= not _blank(old.value)

    if info.known is not None:
        for p in tpl.params:
            key = param_key(p.name)
            if p.showkey and key not in info.known and not _blank(p.value):
                result.unresolved.append(key)
    if align and align_call(tpl):
        result.aligned += 1


def align_call(tpl: Template) -> bool:
    """Rebuild a call one ``| name = value`` per line, with the ``=`` signs
    lined up and ``}}`` on its own line.  Calls with unnamed or repeated
    parameters, or that would lose any text, are left alone.  Returns
    whether the call changed."""
    params = tpl.params
    if not params or not all(p.showkey for p in params):
        return False
    names = [str(p.name).strip() for p in params]
    if len(set(map(param_key, names))) != len(names):
        return False
    values = [str(p.value).strip() for p in params]
    width = max(len(n) for n in names)
    name = str(tpl.name).strip()
    lines = [f'| {n.ljust(width)} = {v}' for n, v in zip(names, values)]
    rebuilt = '{{' + name + '\n' + '\n'.join(lines) + '\n}}'
    old = str(tpl)
    if rebuilt == old or fingerprint(rebuilt) != fingerprint(old):
        return False
    tpl.name = name + '\n'
    for p, n, v in zip(params, names, values):
        p.name = f' {n.ljust(width)} '
        p.value = f' {v}\n'
    return True
