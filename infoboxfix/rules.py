"""Reading a template's deprecated parameters from its code.

Everything here works on wikitext and plain data, so it can be tested
without a wiki; ``cli.py`` does the fetching.

The only rules come from the template's current
``{{#invoke:Check for deprecated parameters|check|...}}`` call
(``old = new``, ``_remove = a; b``, ``_regexpN = pattern = repl``), plus any
the operator adds on the command line.  Nothing is inferred from the
template's history or from similar-looking names: a deprecation that was
taken out of the code may have been taken out on purpose, and once a
deprecation is finished ParamBot may have a rule for it.

The ``{{#invoke:Check for unknown parameters|check|...}}`` list is used to
check that every rule renames to a name the template accepts, and to list
unknown parameters the rules don't cover.
"""

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace

from mwparserfromhell.nodes import Template
from mwparserfromhell.wikicode import Wikicode

from .luapattern import LuaPatternError, compile_lua, lua_replacement
from .wikitext import normalize_template_name, param_key, parse, strip_comments

__all__ = ['KnownParams', 'Resolution', 'Rule', 'TemplateInfo', 'build_info', 'map_lists',
           'parse_rules_text', 'tracking_categories']

UNKNOWN_MODULE = 'Check for unknown parameters'
DEPRECATED_MODULE = 'Check for deprecated parameters'
UNKNOWN_MODULE_PAGE = 'Module:' + UNKNOWN_MODULE

# Settings of Module:Check for unknown parameters that switch on a list of
# map parameters kept in the module: {setting: name of the Lua table}.
MAP_SETTINGS = {'mapframe_args': 'mapframe_params', 'pushpin_map_args': 'pushpin_map_params'}
_DEPRECATED_SETTINGS = ('_category', 'ignoreblank', 'preview')
_CATEGORY_RE = re.compile(r'\[\[\s*:?\s*category\s*:\s*([^|\]]+)', re.IGNORECASE)


@dataclass(frozen=True)
class Rule:
    """Rename ``old`` to ``new``, or remove it if ``new`` is None.

    For a ``_regexpN`` rule, ``pattern`` is the compiled pattern, ``old`` its
    Lua text and ``new`` a re.sub template."""
    old: str
    new: str | None
    source: str
    pattern: re.Pattern | None = None

    def describe(self) -> str:
        return f'{self.old} -> {self.new if self.new is not None else "(remove)"}'


@dataclass(frozen=True)
class Resolution:
    """What to do with one parameter of one article: rename it to ``new``,
    or remove it if ``new`` is None."""
    old: str
    new: str | None
    source: str


class KnownParams:
    """The names a template accepts, per its unknown-parameter check.

    ``uncertain`` lists what the bot couldn't read (a pattern it can't
    translate, a map setting whose list it couldn't load), so some names it
    takes for unknown may be accepted after all."""

    def __init__(self, names: Iterable[str] = (), patterns: Iterable[re.Pattern] = ()) -> None:
        self.names = set(names)
        self.patterns = list(patterns)
        self.uncertain: list[str] = []

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and (
            name in self.names or any(p.match(name) for p in self.patterns))

    def __repr__(self) -> str:
        return f'KnownParams({len(self.names)} names, {len(self.patterns)} patterns)'


@dataclass
class TemplateInfo:
    name: str
    redirects: set[str] = field(default_factory=set)
    known: KnownParams | None = None
    rules: list[Rule] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Whether the template's code has a deprecated-parameter check that
    # lists anything.  Without one the bot has nothing to go on.
    declared: bool = False

    def __post_init__(self) -> None:
        self._exact = {r.old: r for r in self.rules if r.pattern is None}
        self._patterns = [r for r in self.rules if r.pattern is not None]

    @property
    def names(self) -> set[str]:
        """The normalized names a call to the template can use."""
        return {self.name} | self.redirects

    def resolve(self, name: str) -> Resolution | None:
        """What to do with parameter ``name``, or None to leave it alone."""
        rule = self._exact.get(name)
        if rule is not None:
            return Resolution(name, rule.new, rule.source)
        for rule in self._patterns:
            if rule.pattern.match(name):
                new = rule.pattern.sub(rule.new, name) if rule.new is not None else None
                if new is None or new != name and (self.known is None or new in self.known):
                    return Resolution(name, new, rule.source)
        return None


# -- reading the check modules' calls -----------------------------------------

def invokes(code: Wikicode, module: str, function: str = 'check') -> Iterator[Template]:
    """The {{#invoke:module|function|...}} calls in parsed wikitext."""
    for call in code.filter_templates(recursive=True):
        name = ' '.join(strip_comments(call.name).replace('_', ' ').split())
        m = re.fullmatch(r'#invoke\s*:\s*(.+)', name, re.IGNORECASE)
        if not m:
            continue
        target = re.sub(r'^module\s*:\s*', '', m.group(1), flags=re.IGNORECASE)
        if (normalize_template_name(target) == module and call.params
                and not call.params[0].showkey
                and strip_comments(call.params[0].value).strip() == function):
            yield call


def map_lists(module_source: str) -> dict[str, frozenset[str]]:
    """The map parameter lists in Module:Check for unknown parameters,
    keyed by the setting that switches each on."""
    lists = {}
    for setting, table in MAP_SETTINGS.items():
        m = re.search(r'\b' + table + r'\s*=\s*\{(.*?)\}', module_source, re.DOTALL)
        if m:
            lists[setting] = frozenset(re.findall(r'[\'"]([^\'"]+)[\'"]', m.group(1)))
    return lists


def known_params(code: Wikicode, maps: Mapping[str, frozenset[str]] | None = None
                 ) -> KnownParams | None:
    """The names the template's unknown-parameter check accepts, or None if
    it has no such check."""
    calls = list(invokes(code, UNKNOWN_MODULE))
    if not calls:
        return None
    known = KnownParams()
    for call in calls:
        for param in call.params[1:]:
            value = strip_comments(param.value).strip()
            if not param.showkey:
                if value and '{' not in value:
                    known.names.add(value)
                continue
            key = param_key(param.name)
            if re.fullmatch(r'regexp[1-9][0-9]*', key) and value:
                try:
                    known.patterns.append(compile_lua(value))
                except LuaPatternError as e:
                    known.uncertain.append(f'{key} = {value} ({e})')
            elif key in MAP_SETTINGS and value:
                if maps is not None and key in maps:
                    known.names |= maps[key]
                else:
                    known.uncertain.append(f'{key} (could not read its list from {UNKNOWN_MODULE_PAGE})')
    return known


def deprecated_rules(code: Wikicode, source: str) -> tuple[list[Rule], list[str]]:
    """The rules in a template's deprecated-parameter checks, and warnings
    about entries the bot couldn't use."""
    rules: list[Rule] = []
    warnings: list[str] = []
    for call in invokes(code, DEPRECATED_MODULE):
        for param in call.params[1:]:
            if not param.showkey:
                continue
            key = param_key(param.name)
            value = strip_comments(param.value).strip()
            if key in _DEPRECATED_SETTINGS:
                continue
            if '{' in key or '{' in value:
                warnings.append(f'{source}: skipped "{key} = {value}", which depends on other markup')
            elif key == '_remove':
                rules += [Rule(name.strip(), None, source) for name in value.split(';') if name.strip()]
            elif re.fullmatch(r'_regexp[1-9][0-9]*', key):
                parts = re.split(r'\s*=\s*', value)
                try:
                    if len(parts) != 2:
                        raise LuaPatternError('expected "pattern = replacement"')
                    rules.append(Rule(parts[0], lua_replacement(parts[1]), source,
                                      pattern=compile_lua(parts[0])))
                except LuaPatternError as e:
                    warnings.append(f'{source}: could not use "{key} = {value}": {e}')
            elif value:
                rules.append(Rule(key, value, source))
            else:
                warnings.append(f'{source}: |{key}= is deprecated with no replacement named')
    return rules, warnings


def tracking_categories(code: Wikicode, template: str) -> list[str]:
    """The template's own unknown- and deprecated-parameter categories.
    Shared categories, which don't name the template, are left out."""
    texts = [str(c.get('unknown').value) for c in invokes(code, UNKNOWN_MODULE) if c.has('unknown')]
    texts += [str(c.get('_category').value) for c in invokes(code, DEPRECATED_MODULE)
              if c.has('_category')]
    categories = []
    for text in texts:
        for name in _CATEGORY_RE.findall(strip_comments(text)):
            name = ' '.join(name.replace('_', ' ').split())
            if ('{' not in name and template.casefold() in name.casefold()
                    and name not in categories):
                categories.append(name)
    return categories


def parse_rules_text(text: str, source: str) -> list[Rule]:
    """Rules written one per line as ``old = new``, or ``old =`` to remove
    the parameter.  Blank lines and lines starting with # are ignored."""
    rules = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        old, eq, new = line.partition('=')
        if not eq or not old.strip():
            raise ValueError(f'{source}, line {n}: expected "old = new" or "old =", got {line!r}')
        rules.append(Rule(old.strip(), new.strip() or None, source))
    return rules


# -- putting it together ------------------------------------------------------

def build_info(name: str, source: str, *, redirects: Iterable[str] = (),
               maps: Mapping[str, frozenset[str]] | None = None,
               extra_rules: Iterable[Rule] = ()) -> TemplateInfo:
    """Everything the fixer needs to know about template ``name``, whose
    current wikitext is ``source``.  ``extra_rules`` (from the operator)
    come before the template's own and win where they disagree."""
    name = normalize_template_name(name)
    code = parse(source)
    known = known_params(code, maps)
    warnings: list[str] = []
    declared, found_warnings = deprecated_rules(code, 'deprecated-parameter check')
    warnings += found_warnings
    if known is None:
        warnings.append(f'Template:{name} has no {{{{#invoke:{UNKNOWN_MODULE}|check}}}} call, so '
                        'the bot cannot check that the new names are accepted')
    else:
        warnings += [f'could not read {item}' for item in known.uncertain]

    rules: list[Rule] = []
    exact: dict[str, Rule] = {}
    for rule in [*extra_rules, *declared]:
        if rule.pattern is not None:
            rules.append(rule)
        elif rule.old in exact:
            first = exact[rule.old]
            if first.new != rule.new:
                warnings.append(f'ignored {rule.describe()} ({rule.source}): '
                                f'{first.describe()} ({first.source}) comes first')
        else:
            exact[rule.old] = rule
    for rule in exact.values():
        # Follow chains in the list, e.g. othernames = other_names, other_names = nickname
        new, hops = rule.new, []
        while (new is not None and known is not None and new not in known
               and new in exact and len(hops) < 10):
            hops.append(new)
            new = exact[new].new
        if hops:
            rule = replace(rule, new=new, source=f'{rule.source}, via {" -> ".join(hops)}')
        if rule.new == rule.old:
            continue
        if rule.new is not None and known is not None and rule.new not in known:
            warnings.append(f'ignored {rule.describe()} ({rule.source}): '
                            f'Template:{name} does not accept |{rule.new}=')
            continue
        rules.append(rule)
    rules.sort(key=lambda r: r.pattern is not None)
    return TemplateInfo(name=name, redirects={normalize_template_name(r) for r in redirects} - {name},
                        known=known, rules=rules, categories=tracking_categories(code, name),
                        warnings=warnings, declared=bool(declared))
