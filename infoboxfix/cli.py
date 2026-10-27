"""Fix deprecated parameters in every call to an infobox.

    infoboxfix --template:"Infobox venue"

reads the deprecated parameters listed in the template's
{{#invoke:Check for deprecated parameters|check|...}} call, lists them and
asks before starting, then works through the template's unknown- and
deprecated-parameter categories, showing each diff and asking before
saving it.  It renames deprecated parameters, removes the ones marked for
removal, renames calls to redirects to the template itself, and lines up
the infobox.  Pages where the only change would be cosmetic are skipped.

If the template's code lists no deprecated parameters, it says so and
stops: it never guesses them from the template's history.

It only runs while its run page (User:<bot>/Run, or infoboxfix_run_page in
user-config.py) says "yes", and checks it again before every save, so
anyone can stop it by changing that page.

Options (a single or double dash both work, and ":" or "="):

  -template:NAME     the template to fix (required)
  -rename:OLD=NEW    an extra rename rule; can be repeated
  -remove:NAME       an extra parameter to remove; can be repeated
  -rules:FILE        extra rules from a file, one "old = new" (or
                     "old =" to remove) per line
                     (extra rules add to the template's list; they don't
                     replace it, and the bot still stops without one)
  -show-rules       list the rules and the tracking categories' sizes, then stop
  -all               work through every article that uses the template,
                     not just its tracking categories
  -cosmetic          also save pages where the only changes are cosmetic
  -no-align          don't line up the infobox
  -summary:TEXT      the edit summary, overriding infoboxfix_summary in
                     user-config.py; {template} is the template's name,
                     {changes} lists what the bot did to the page and
                     {counts} counts it
  -report:FILE       also write the pages needing a human to FILE
  -always            save each page without asking (the rules are still
                     listed and approved once first)
  -skip-rules-prompt don't ask before using the rules (they are still
                     listed); for unattended runs, usually with -always

Any Pywikibot page generator (-page:, -cat:, -file:, -transcludes:, ...)
replaces the default list of pages, and its filters (-ns:, -limit:, ...)
apply as usual.  Global options such as -simulate, -lang: and -family:
work too.
"""

import ast
import itertools
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

import pywikibot
from pywikibot import config, pagegenerators
from pywikibot.bot import ExistingPageBot, SingleSiteBot

from . import __version__
from .fixer import PageResult, fix_page
from .rules import (DEPRECATED_MODULE, UNKNOWN_MODULE_PAGE, Rule, TemplateInfo, build_info, map_lists,
                    parse_rules_text)
from .wikitext import normalize_template_name, strip_comments

SUMMARY_LIMIT = 500
# The edit summary.  {template} is the template's name; {changes} lists what
# the bot did to the page, and {counts} counts it ("Updating 27 deprecated
# parameters, aligned parameters").  user-config.py can set its own as
# infoboxfix_summary, and -summary overrides both.
SUMMARY_SETTING = 'infoboxfix_summary'
DEFAULT_SUMMARY = '[[Template:{template}|{template}]]: {changes}'
PRELOAD_SIZE = 50   # pages per request; the API's limit for page text
# The run page: the bot only runs while it says "yes", and checks it before
# every save, so any editor can stop the bot by changing it.  user-config.py
# can name another page as infoboxfix_run_page.
RUN_PAGE_SETTING = 'infoboxfix_run_page'
DEFAULT_RUN_PAGE = 'User:{username}/Run'


class InfoboxFixBot(SingleSiteBot, ExistingPageBot):

    use_redirects = False
    update_options = {'summary': DEFAULT_SUMMARY, 'cosmetic': False, 'align': True}

    def __init__(self, info: TemplateInfo, *, run_page: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.info = info
        self.run_page = run_page
        self.stopped = False    # by the run page
        self.review: list[tuple[str, list[str]]] = []
        self.cosmetic_skips = 0

    def treat_page(self) -> None:
        page = self.current_page
        result = fix_page(page.text, self.info, align=self.opt.align)
        problems = result.conflicts + [f'unknown parameter |{p}=' for p in result.unresolved]
        if problems:
            self.review.append((page.title(), problems))
            pywikibot.warning('Needs a human: ' + '; '.join(problems))
        if result.text == page.text:
            return
        if not result.substantive and not self.opt.cosmetic:
            pywikibot.info('Only cosmetic changes; skipping.')
            self.cosmetic_skips += 1
            return
        reason = run_page_stop_reason(self.run_page, read_run_page(self.site, self.run_page))
        if reason:
            pywikibot.error(f'{reason}; stopping before saving {page.title()}.')
            self.stopped = True
            self.generator.close()  # ends run() cleanly, so teardown() still reports
            return
        self.put_current(result.text, summary=edit_summary(self.info.name, result, self.opt.summary))

    def teardown(self) -> None:
        if self.cosmetic_skips:
            pywikibot.info(f'{self.cosmetic_skips} page(s) skipped as cosmetic-only.')
        if self.review:
            pywikibot.info(f'\n{len(self.review)} page(s) need a human:')
            for title, problems in self.review:
                pywikibot.info(f'  {title}: {"; ".join(problems)}')


def _unique(items: Iterable) -> list:
    return list(dict.fromkeys(items))


def summary_counts(result: PageResult) -> str:
    """What the bot did, counted: ``Updating 27 deprecated parameters,
    aligned parameters``.  Renamed and removed parameters both count."""
    parts = []
    if n := len(result.renamed) + len(result.removed):
        parts.append(f'updating {n} deprecated parameter{"" if n == 1 else "s"}')
    if result.bypassed:
        parts.append('bypassed template redirect')
    if result.aligned:
        parts.append('aligned parameters')
    text = ', '.join(parts)
    return text[:1].upper() + text[1:]


def edit_summary(template: str, result: PageResult, fmt: str = DEFAULT_SUMMARY) -> str:
    """The edit summary: ``fmt`` with {template}, {changes} and {counts}
    filled in.  {changes} is shortened to counts if the summary would be
    too long."""
    renamed = _unique(f'{old} → {new}' for old, new in result.renamed)
    removed = _unique(result.removed)
    counts = summary_counts(result)
    tail = []
    if result.bypassed:
        tail.append('bypassed template redirect')
    if result.aligned:
        tail.append('aligned parameters')
    for detail in (True, False):
        parts = []
        if renamed:
            parts.append('renamed deprecated parameters: ' + ', '.join(renamed) if detail
                         else f'renamed {len(renamed)} deprecated parameters')
        if removed:
            parts.append('removed ' + ', '.join(removed) if detail
                         else f'removed {len(removed)} parameters')
        summary = fmt.format(template=template, changes='; '.join(parts + tail), counts=counts)
        if len(summary) <= SUMMARY_LIMIT:
            break
    return summary


def user_config_setting(name: str) -> str | None:
    """One of this bot's string settings from user-config.py, or None.
    Pywikibot drops variables it doesn't know, so the bot reads the file
    itself: a plain ``name = '...'`` assignment, parsed but never run."""
    path = Path(config.base_dir, config.user_config_file)
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError):
        return None
    value = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                                for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                value = None
            if not isinstance(value, str):
                raise ValueError(f'{name} in {path} must be a plain string')
    return value


# -- the run page ----------------------------------------------------------------

def read_run_page(site, title: str) -> str | None:
    """The run page's current text, read fresh from the wiki (never from a
    cache), or None if it doesn't exist."""
    data = site.simple_request(action='query', prop='revisions', titles=title, rvprop='content',
                               rvslots='main', formatversion=2).submit()
    page = data['query']['pages'][0]
    if page.get('missing') or page.get('invalid'):
        return None
    return page['revisions'][0]['slots']['main']['content']


def run_page_stop_reason(title: str, text: str | None) -> str | None:
    """Why the run page says to stop, or None if it says "yes".  Case,
    whitespace and HTML comments are ignored; anything else stops the bot."""
    if text is None:
        return f'{title} does not exist (create it saying "yes" to let the bot run)'
    said = strip_comments(text).strip()
    if said.casefold() == 'yes':
        return None
    shown = said if len(said) <= 40 else said[:40] + '…'
    return f'{title} says {shown!r}, not "yes"' if said else f'{title} is empty, not "yes"'


# -- reading the template from the wiki ---------------------------------------

def template_redirects(site, page: pywikibot.Page) -> list[str]:
    """The titles, without namespace, of the templates that redirect to
    ``page``.  A plain prop=redirects query: Wikimedia throttles the
    heavier generator query that Page.redirects() makes."""
    titles = []
    params = {'action': 'query', 'titles': page.title(), 'prop': 'redirects',
              'rdnamespace': 10, 'rdlimit': 'max', 'rdprop': 'title', 'formatversion': 2}
    while True:
        data = site.simple_request(**params).submit()
        for p in data['query']['pages']:
            titles += [r['title'].split(':', 1)[1] for r in p.get('redirects', ())]
        if 'continue' not in data:
            return titles
        params.update(data['continue'])


def load_info(site: pywikibot.site.BaseSite, name: str, *,
              extra_rules: Iterable[Rule]) -> TemplateInfo:
    page = pywikibot.Page(site, normalize_template_name(name), ns=10)
    if not page.exists():
        raise pywikibot.exceptions.Error(f'{page.title()} does not exist')
    if page.isRedirectPage():
        target = page.getRedirectTarget()
        pywikibot.warning(f'{page.title()} redirects to {target.title()}; using that')
        page = target
    pywikibot.info(f'Reading {page.title()}...')
    source = page.text
    redirects = template_redirects(site, page)
    maps = map_lists(pywikibot.Page(site, UNKNOWN_MODULE_PAGE).text)
    return build_info(page.title(with_ns=False), source, redirects=redirects, maps=maps,
                      extra_rules=extra_rules)


def show_info(info: TemplateInfo) -> None:
    pywikibot.info(f'\nTemplate:{info.name}')
    pywikibot.info('Redirects: ' + (', '.join(sorted(info.redirects)) or 'none'))
    if info.known is not None:
        pywikibot.info(f'Accepts {len(info.known.names)} names and '
                       f'{len(info.known.patterns)} patterns')
    pywikibot.info('Tracking categories: ' + (', '.join(info.categories) or 'none'))
    pywikibot.info(f'\nRules ({len(info.rules)}):')
    width = max((len(r.describe()) for r in info.rules), default=0)
    for rule in info.rules:
        pywikibot.info(f'  {rule.describe().ljust(width)}  [{rule.source}]')
    for warning in info.warnings:
        pywikibot.warning(warning)


def default_pages(site, info: TemplateInfo, scan_all: bool) -> Iterator[pywikibot.Page]:
    """The template's tracking categories, or every article using it."""
    template = pywikibot.Page(site, info.name, ns=10)
    if scan_all or not info.categories:
        if not scan_all:
            pywikibot.warning('No tracking categories found; working through every article '
                              'that uses the template')
        yield from template.getReferences(only_template_inclusion=True, namespaces=0)
        return
    seen = set()
    for name in info.categories:
        for page in pywikibot.Category(site, name).articles(namespaces=0):
            if page.title() not in seen:
                seen.add(page.title())
                yield page


def preloaded(site, pages: Iterable[pywikibot.Page],
              groupsize: int = PRELOAD_SIZE) -> Iterator[pywikibot.Page]:
    """Load pages in batches, with the list of templates each uses.
    Page.save() needs that list for its {{bots}}/{{nobots}} check, and
    without it asks the API once for every page it saves."""
    pages = iter(pages)
    while batch := list(itertools.islice(pages, groupsize)):
        yield from site.preloadpages(batch, groupsize=groupsize, templates=True)


# -- command line ----------------------------------------------------------------

def approve_rules(skip_prompt: bool) -> bool:
    """Whether to go ahead with the rules just listed: asks the operator
    unless -skip-rules-prompt was given.  -always doesn't skip this; it
    only stops the bot asking before each save."""
    if skip_prompt:
        pywikibot.info('\nUsing these rules without asking (-skip-rules-prompt).')
        return True
    return pywikibot.input_yn('\nUse these rules?', default=False, automatic_quit=False)


def _split(arg: str) -> tuple[str, str]:
    """``--template:"X"`` or ``-template=X`` -> ('template', 'X')."""
    body = arg.lstrip('-')
    cut = min((i for i in (body.find(':'), body.find('=')) if i >= 0), default=len(body))
    return body[:cut].lower(), body[cut + 1:]


def main(*args: str) -> int:
    args = args or tuple(sys.argv[1:])
    if any(a.lstrip('-') in ('help', 'h') for a in args):
        print(__doc__)
        return 0
    local_args = pywikibot.handle_args(args, do_help=False)
    # Pywikibot names the product after the running script, which comes out
    # as "-" or "__main__" when run as `python -m infoboxfix`.
    config.user_agent_format = config.user_agent_format.replace(
        '{script_product}', f'infoboxfix/{__version__}')
    site = pywikibot.Site()
    gen_factory = pagegenerators.GeneratorFactory(site)
    template = None
    extra: list[Rule] = []
    show_rules = scan_all = skip_rules_prompt = False
    report = None
    options = {}
    for arg in local_args:
        opt, value = _split(arg)
        if opt == 'template':
            template = value
        elif opt == 'rename':
            old, eq, new = value.partition('=')
            if not eq or not old.strip() or not new.strip():
                pywikibot.error(f'-rename needs OLD=NEW, got {value!r}')
                return 2
            extra.append(Rule(old.strip(), new.strip(), 'command line'))
        elif opt == 'remove':
            extra.append(Rule(value.strip(), None, 'command line'))
        elif opt == 'rules':
            try:
                extra += parse_rules_text(Path(value).read_text(encoding='utf-8'), value)
            except (OSError, ValueError) as e:
                pywikibot.error(str(e))
                return 2
        elif opt == 'show-rules':
            show_rules = True
        elif opt == 'all':
            scan_all = True
        elif opt == 'cosmetic':
            options['cosmetic'] = True
        elif opt == 'no-align':
            options['align'] = False
        elif opt == 'summary':
            options['summary'] = value
        elif opt == 'report':
            report = value
        elif opt == 'always':
            options['always'] = True
        elif opt == 'skip-rules-prompt':
            skip_rules_prompt = True
        elif not gen_factory.handle_arg(arg):
            pywikibot.error(f'Unknown option {arg!r}; see --help')
            return 2
    if not template:
        pywikibot.error('Say which template to fix, e.g. --template:"Infobox venue"')
        return 2
    try:
        options.setdefault('summary', user_config_setting(SUMMARY_SETTING) or DEFAULT_SUMMARY)
        edit_summary('Infobox example', PageResult(renamed=[('old', 'new')]), options['summary'])
    except (KeyError, IndexError, ValueError) as e:
        pywikibot.error(f'Bad edit summary ({e!r}). It can use {{template}}, {{changes}} and {{counts}}; '
                        'write a literal { or } as {{ or }}.')
        return 2
    try:
        run_page = user_config_setting(RUN_PAGE_SETTING)
    except ValueError as e:
        pywikibot.error(str(e))
        return 2

    # Pywikibot only logs in to save, but Wikimedia throttles logged-out
    # reads hard (HTTP 429), so log in before reading anything.
    site.login()
    pywikibot.info(f'Logged in to {site} as {site.user()}')
    if not site.has_right('bot'):
        pywikibot.warning(f'{site.user()} does not have the bot right: its edits will not be '
                          'marked as bot edits, and its API limits are lower')
    run_page = run_page or DEFAULT_RUN_PAGE.format(username=site.user())
    reason = run_page_stop_reason(run_page, read_run_page(site, run_page))
    if reason and not show_rules:
        pywikibot.error(f'{reason}. Stopping; nothing was changed.')
        return 1
    pywikibot.info(f'Run page: {run_page} ' + (f'({reason})' if reason else 'says "yes"'))

    info = load_info(site, template, extra_rules=extra)
    show_info(info)
    if not info.declared:
        pywikibot.error(
            f'Template:{info.name} does not list any deprecated parameters in its code '
            f'(no {{{{#invoke:{DEPRECATED_MODULE}|check|...}}}} call with entries). Its '
            'deprecations may have been removed on purpose, or ParamBot may already be handling '
            'them. Stopping; nothing was changed.')
        return 1
    if not info.rules:
        pywikibot.error(f'None of the deprecated parameters Template:{info.name} lists could be '
                        'used (see the warnings above). Stopping; nothing was changed.')
        return 1
    for name in info.categories:
        size = pywikibot.Category(site, name).categoryinfo.get('pages', 0)
        pywikibot.info(f'Category:{name}: {size} page(s)')
    if gen_factory.gens:
        gen = gen_factory.getCombinedGenerator()
    else:
        gen = gen_factory.getCombinedGenerator(gen=default_pages(site, info, scan_all))
    gen = preloaded(site, gen)
    if show_rules:
        return 0
    if not approve_rules(skip_rules_prompt):
        return 0

    bot = InfoboxFixBot(info, generator=gen, site=site, run_page=run_page, **options)
    bot.run()
    if report and bot.review:
        lines = [f'== Pages using {{{{tl|{info.name}}}}} that need a human ==']
        lines += [f'* [[{title}]]: {"; ".join(problems)}' for title, problems in bot.review]
        Path(report).write_text('\n'.join(lines) + '\n', encoding='utf-8')
        pywikibot.info(f'Wrote {report}')
    return 1 if bot.stopped else 0


if __name__ == '__main__':
    sys.exit(main())
