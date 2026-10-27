"""Small wikitext helpers shared by the rule reader and the fixer."""

import re

import mwparserfromhell
from mwparserfromhell.wikicode import Wikicode

_COMMENT_RE = re.compile(r'<!--.*?(?:-->|$)', re.DOTALL)
_PREFIX_RE = re.compile(r'^\s*template\s*:\s*', re.IGNORECASE)
_FINGERPRINT_RE = re.compile(r'[\s|]')


def strip_comments(text: object) -> str:
    return _COMMENT_RE.sub('', str(text))


def normalize_title(name: object) -> str:
    """A page title as MediaWiki sees it: comments and underscores gone,
    whitespace collapsed, first letter upper-cased."""
    title = ' '.join(strip_comments(name).replace('_', ' ').split())
    return title[:1].upper() + title[1:]


def normalize_template_name(name: object) -> str:
    """The name part of a {{template call}} as a title without the
    Template: prefix, e.g. ``template:infobox_venue`` -> ``Infobox venue``."""
    return normalize_title(_PREFIX_RE.sub('', normalize_title(name)))


def param_key(name: object) -> str:
    """The parameter name MediaWiki sees: comments stripped, whitespace trimmed."""
    return strip_comments(name).strip()


def fingerprint(text: str) -> str:
    """The text with whitespace and pipes removed; if two versions of a
    template call have the same fingerprint, rebuilding it lost nothing."""
    return _FINGERPRINT_RE.sub('', text)


def parse(text: str) -> Wikicode:
    return mwparserfromhell.parse(text)
