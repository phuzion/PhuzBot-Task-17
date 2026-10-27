import tempfile
import unittest
from pathlib import Path
from unittest import mock

# infoboxfix first: importing it hides Pywikibot's warning about the bot's
# own user-config.py settings, which Pywikibot reads as soon as it's imported
from infoboxfix import cli  # isort: skip
import pywikibot  # isort: skip
from pywikibot import config  # isort: skip


from infoboxfix.cli import (InfoboxFixBot, _split, approve_rules, edit_summary, read_run_page,
                           run_page_stop_reason, user_config_setting)
from infoboxfix.fixer import PageResult, fix_page
from infoboxfix.luapattern import LuaPatternError, compile_lua, lua_replacement
from infoboxfix.rules import Rule, build_info, map_lists, parse_rules_text, tracking_categories
from infoboxfix.wikitext import parse

CHECK = ('{{#invoke:Check for unknown parameters|check|unknown={{main other|'
         '[[Category:Pages using infobox thing with unknown parameters|_VALUE_{{PAGENAME}}]]}}'
         '|preview=Page using [[Template:Infobox thing]] with unknown parameter "_VALUE_"'
         '|ignoreblank=y| {names} }}')


def template(names, body='', extra=''):
    return '{{Infobox\n' + body + '\n}}' + extra + CHECK.replace('{names}', ' | '.join(names))


def info(names, **kwargs):
    kwargs.setdefault('redirects', ['Infobox stuff', 'Thingbox'])
    return build_info('Infobox thing', template(names), **kwargs)


class LuaPatternTest(unittest.TestCase):

    def test_classes_and_quantifiers(self):
        self.assertTrue(compile_lua('image%d+').match('image12'))
        self.assertFalse(compile_lua('image%d+').match('image'))
        self.assertTrue(compile_lua('blank[%d]*_name').match('blank_name'))
        self.assertTrue(compile_lua('a.-b').match('axxb'))
        self.assertFalse(compile_lua('a%-b').match('axb'))

    def test_replacement(self):
        pat = compile_lua('blank(%d+)_name')
        self.assertEqual(pat.sub(lua_replacement('custom_label%1'), 'blank3_name'), 'custom_label3')

    def test_unsupported(self):
        with self.assertRaises(LuaPatternError):
            compile_lua('%b()')


class RulesTest(unittest.TestCase):

    def test_deprecated_check(self):
        source = template(['name', 'image_size', 'label1', 'label2'],
                          extra='{{#invoke:Check for deprecated parameters|check|_category=[[Category:X]]'
                                '| imagesize = image_size | _remove = foo; bar'
                                '| _regexp1 = blank(%d+)_name = label%1 }}')
        got = build_info('Infobox thing', source)
        rules = {r.old: r.new for r in got.rules}
        self.assertEqual(rules['imagesize'], 'image_size')
        self.assertIsNone(rules['foo'])
        self.assertIsNone(rules['bar'])
        self.assertEqual(got.resolve('blank2_name').new, 'label2')

    def test_rule_to_unaccepted_name_is_dropped(self):
        got = info(['name'], extra_rules=[Rule('a', 'b', 'test')])
        self.assertEqual(got.rules, [])
        self.assertTrue(any('does not accept |b=' in w for w in got.warnings))

    def test_declared(self):
        dep = '{{#invoke:Check for deprecated parameters|check|tenant=tenants}}'
        self.assertTrue(build_info('Infobox thing', template(['tenants'], extra=dep)).declared)
        self.assertFalse(info(['tenants']).declared)
        # Rules from the operator don't count as the template listing any
        self.assertFalse(info(['tenants'], extra_rules=[Rule('tenant', 'tenants', 'test')]).declared)
        empty = '{{#invoke:Check for deprecated parameters|check|_category=[[Category:X]]}}'
        self.assertFalse(build_info('Infobox thing', template(['tenants'], extra=empty)).declared)

    def test_nothing_inferred(self):
        dep = '{{#invoke:Check for deprecated parameters|check|tenant=tenants}}'
        got = build_info('Infobox thing', template(['full_name', 'tenants'], extra=dep))
        self.assertIsNone(got.resolve('fullname'))
        self.assertIsNone(got.resolve('Full name'))
        self.assertEqual(got.resolve('tenant').new, 'tenants')

    def test_chain(self):
        dep = ('{{#invoke:Check for deprecated parameters|check'
               '|othernames=other_names|other_names=nickname}}')
        got = build_info('Infobox thing', template(['nickname'], extra=dep))
        self.assertEqual(got.resolve('othernames').new, 'nickname')
        self.assertEqual(got.resolve('other_names').new, 'nickname')

    def test_map_settings(self):
        lists = map_lists("local mapframe_params = {\n\t'mapframe',\n\t'mapframe-zoom'\n}")
        source = template(['name']).replace('ignoreblank=y', 'ignoreblank=y|mapframe_args=y')
        self.assertIn('mapframe-zoom', build_info('Infobox thing', source, maps=lists).known)
        unread = build_info('Infobox thing', source)
        self.assertTrue(any('mapframe_args' in w for w in unread.warnings))

    def test_tracking_categories(self):
        code = parse(template(['name']))
        self.assertEqual(tracking_categories(code, 'Infobox thing'),
                         ['Pages using infobox thing with unknown parameters'])

    def test_rules_file(self):
        rules = parse_rules_text('# comment\n\nold = new\ngone =\n', 'f')
        self.assertEqual([(r.old, r.new) for r in rules], [('old', 'new'), ('gone', None)])
        with self.assertRaises(ValueError):
            parse_rules_text('nonsense', 'f')


class FixerTest(unittest.TestCase):

    def setUp(self):
        self.info = info(['name', 'full_name', 'image', 'capacity', 'child', 'embedded'],
                         extra_rules=[Rule('fullname', 'full_name', 'test'), Rule('seating', 'capacity', 'test'),
                                      Rule('junk', None, 'test')])

    def test_full_fix(self):
        text = ('Intro.\n{{thingbox|name=Arena\n\n| fullname   = The Arena|image=[[File:A.jpg|thumb]]\n'
                '|seating=500}}\nMore.')
        got = fix_page(text, self.info)
        self.assertEqual(got.text, 'Intro.\n{{Infobox thing\n'
                                   '| name      = Arena\n'
                                   '| full_name = The Arena\n'
                                   '| image     = [[File:A.jpg|thumb]]\n'
                                   '| capacity  = 500\n'
                                   '}}\nMore.')
        self.assertEqual(got.renamed, [('fullname', 'full_name'), ('seating', 'capacity')])
        self.assertEqual(got.bypassed, ['Thingbox'])
        self.assertTrue(got.substantive)

    def test_only_one_logic(self):
        cases = {
            '|seating=|capacity=5': ('|capacity=5', False),            # old empty: drop it
            '|seating=5|capacity= 5': ('|capacity= 5', False),         # same value: drop old
            '|seating=5|capacity=': ('|capacity=5', True),             # new empty: drop new, rename
        }
        for params, (expected, _) in cases.items():
            got = fix_page('{{Infobox thing' + params + '}}', self.info, align=False)
            self.assertEqual(got.text, '{{Infobox thing' + expected + '}}', params)
        got = fix_page('{{Infobox thing|seating=5|capacity=6}}', self.info, align=False)
        self.assertEqual(got.text, '{{Infobox thing|seating=5|capacity=6}}')
        self.assertTrue(got.conflicts)
        self.assertFalse(got.substantive)

    def test_removal(self):
        got = fix_page('{{Infobox thing\n| name = A\n| junk = x\n}}', self.info)
        self.assertEqual(got.text, '{{Infobox thing\n| name = A\n}}')
        self.assertEqual(got.removed, ['junk'])
        self.assertTrue(got.substantive)

    def test_cosmetic_only(self):
        got = fix_page('{{Thingbox|name=A|seating=}}', self.info)
        self.assertEqual(got.text, '{{Infobox thing\n| name     = A\n| capacity = \n}}')
        self.assertFalse(got.substantive)

    def test_unresolved(self):
        got = fix_page('{{Infobox thing|name=A|colour=red|blank=}}', self.info)
        self.assertEqual(got.unresolved, ['colour'])

    def test_no_align_when_text_would_be_lost(self):
        text = '{{Infobox thing|name=A|positional|seating=5}}'
        got = fix_page(text, self.info)
        self.assertEqual(got.text, '{{Infobox thing|name=A|positional|capacity=5}}')
        self.assertEqual(got.aligned, 0)

    def test_comments_and_multiline_values_kept(self):
        text = ('{{Infobox thing\n|name=A <!-- note -->\n<!-- Section -->\n|capacity=\n* 1\n* 2\n'
                '|image=<ref name="x">a|b</ref>}}')
        got = fix_page(text, self.info)
        self.assertEqual(got.text, '{{Infobox thing\n| name     = A <!-- note -->\n<!-- Section -->\n'
                                   '| capacity = * 1\n* 2\n| image    = <ref name="x">a|b</ref>\n}}')

    def test_nested_child_infobox(self):
        text = '{{Infobox thing|name=A|embedded={{Infobox stuff|child=yes|seating=5}}}}'
        got = fix_page(text, self.info)
        self.assertEqual(got.text, '{{Infobox thing\n| name     = A\n| embedded = {{Infobox thing\n'
                                   '| child    = yes\n| capacity = 5\n}}\n}}')

    def test_other_templates_and_nowiki_untouched(self):
        text = ('{{Infobox other|seating=5}}<nowiki>{{Thingbox|seating=5}}</nowiki>'
                '<!-- {{Thingbox|seating=5}} -->')
        self.assertEqual(fix_page(text, self.info).text, text)

    def test_summary(self):
        result = PageResult(renamed=[('a', 'b')], removed=['c'], bypassed=['X'], aligned=1)
        self.assertEqual(edit_summary('Infobox venue', result),
                         '[[Template:Infobox venue|Infobox venue]]: renamed deprecated parameters: '
                         'a → b; removed c; bypassed template redirect; aligned parameters')
        long = PageResult(renamed=[(f'param{i}', f'new_param{i}') for i in range(40)])
        self.assertIn('renamed 40 deprecated parameters', edit_summary('Infobox venue', long))

    def test_summary_format(self):
        fmt = ('Updating deprecated parameters - [[Template:{template}]] - '
               '[[Wikipedia:Bots/Requests for approval/PhuzBot 15|PhuzBot 15]]')
        self.assertEqual(edit_summary('Infobox French commune', PageResult(renamed=[('a', 'b')]), fmt),
                         'Updating deprecated parameters - [[Template:Infobox French commune]] - '
                         '[[Wikipedia:Bots/Requests for approval/PhuzBot 15|PhuzBot 15]]')
        self.assertEqual(edit_summary('X', PageResult(removed=['c']), '{changes} ({{x}})'), 'removed c ({x})')
        with self.assertRaises(KeyError):
            edit_summary('X', PageResult(), '{nonsense}')

    def test_summary_counts(self):
        fmt = ('{counts} - [[Template:{template}]] - '
               '[[Wikipedia:Bots/Requests for approval/PhuzBot 15|PhuzBot 15]]')
        result = PageResult(renamed=[(f'p{i}', f'n{i}') for i in range(25)], removed=['x', 'y'], aligned=1)
        self.assertEqual(edit_summary('Infobox French commune', result, fmt),
                         'Updating 27 deprecated parameters, aligned parameters - '
                         '[[Template:Infobox French commune]] - '
                         '[[Wikipedia:Bots/Requests for approval/PhuzBot 15|PhuzBot 15]]')
        self.assertEqual(edit_summary('X', PageResult(renamed=[('a', 'b')], bypassed=['Y']), '{counts}'),
                         'Updating 1 deprecated parameter, bypassed template redirect')
        self.assertEqual(edit_summary('X', PageResult(aligned=1), '{counts}'), 'Aligned parameters')


class RunPageTest(unittest.TestCase):

    def test_stop_reason(self):
        for text in ('yes', ' Yes \n', 'YES', 'yes <!-- change to "no" to stop the bot -->'):
            self.assertIsNone(run_page_stop_reason('User:Bot/Run', text), text)
        for text in ('no', '', '<!-- yes -->', 'yes please', 'yes\nno', '#REDIRECT [[User:Bot/Run2]]'):
            self.assertIsNotNone(run_page_stop_reason('User:Bot/Run', text), text)
        self.assertIn('does not exist', run_page_stop_reason('User:Bot/Run', None))
        self.assertIn("says 'no'", run_page_stop_reason('User:Bot/Run', 'no'))

    def test_read_run_page(self):
        def site_returning(page):
            site = mock.Mock()
            site.simple_request.return_value.submit.return_value = {'query': {'pages': [page]}}
            return site
        found = site_returning({'title': 'User:Bot/Run',
                                'revisions': [{'slots': {'main': {'content': 'yes'}}}]})
        self.assertEqual(read_run_page(found, 'User:Bot/Run'), 'yes')
        self.assertIsNone(read_run_page(site_returning({'title': 'User:Bot/Run', 'missing': True}),
                                        'User:Bot/Run'))

    def test_bot_stops_before_saving(self):
        site = pywikibot.Site('en', 'wikipedia')
        page = pywikibot.Page(site, 'Some article')
        page.text = '{{Infobox thing|name=A|seating=5}}'
        info_ = info(['name', 'capacity'], extra_rules=[Rule('seating', 'capacity', 'test')])
        for run_text, should_save in (('yes', True), ('no', False)):
            bot = InfoboxFixBot(info_, generator=iter([page]), site=site, run_page='User:Bot/Run',
                                always=True)
            bot.generator = (p for p in [page])
            with mock.patch.object(cli, 'read_run_page', return_value=run_text), \
                    mock.patch.object(InfoboxFixBot, 'put_current') as put:
                bot.current_page = page
                bot.treat_page()
            self.assertEqual(put.called, should_save, run_text)
            self.assertEqual(bot.stopped, not should_save, run_text)
            if not should_save:
                self.assertIsNone(next(bot.generator, None))    # the run ends here


class CommandLineTest(unittest.TestCase):

    def test_split(self):
        self.assertEqual(_split('--template:Infobox venue'), ('template', 'Infobox venue'))
        self.assertEqual(_split('-template=Infobox venue'), ('template', 'Infobox venue'))
        self.assertEqual(_split('-rename:a=b'), ('rename', 'a=b'))
        self.assertEqual(_split('--always'), ('always', ''))
        self.assertEqual(_split('-skip-rules-prompt'), ('skip-rules-prompt', ''))

    def test_approve_rules(self):
        with mock.patch('pywikibot.input_yn', return_value=False) as ask:
            self.assertFalse(approve_rules(False))
            ask.assert_called_once()
        with mock.patch('pywikibot.input_yn', return_value=True) as ask:
            self.assertTrue(approve_rules(False))
        with mock.patch('pywikibot.input_yn') as ask, mock.patch('pywikibot.info'):
            self.assertTrue(approve_rules(True))     # -skip-rules-prompt
            ask.assert_not_called()

    def test_user_config_setting(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(config, 'base_dir', d), \
                mock.patch.object(config, 'user_config_file', 'user-config.py'):
            path = Path(d, 'user-config.py')
            self.assertIsNone(user_config_setting('infoboxfix_summary'))      # no file
            path.write_text("usernames['wikipedia']['en'] = 'Bot'\n"
                            "infoboxfix_summary = ('Fixing {template} '\n    'params')\n", encoding='utf-8')
            self.assertEqual(user_config_setting('infoboxfix_summary'), 'Fixing {template} params')
            self.assertIsNone(user_config_setting('something_else'))
            path.write_text('infoboxfix_summary = 3\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                user_config_setting('infoboxfix_summary')
            path.write_text('infoboxfix_summary = "a" + str(1)\n', encoding='utf-8')    # never run
            with self.assertRaises(ValueError):
                user_config_setting('infoboxfix_summary')


if __name__ == '__main__':
    unittest.main()
