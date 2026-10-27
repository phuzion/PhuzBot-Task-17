# infoboxfix

A Pywikibot bot that cleans up every call to an infobox in one go:

```
infoboxfix --template:"Infobox venue"
```

It reads the deprecated parameters listed in the template's code, then for
each article in the template's tracking categories it:

- **renames deprecated parameters** to their new names;
- **removes parameters** marked for removal;
- **renames calls to redirects** (`{{Infobox stadium}}`) to the template itself (`{{Infobox venue}}`);
- **lines up the infobox**: one `| name = value` per line, `=` signs aligned, blank lines dropped, `}}` on its own line.

It's a port of Primefac's AWB module
([Infobox parameter changes](https://en.wikipedia.org/wiki/User:PrimeBOT/code_dump#Infobox_parameter_changes)),
with the hand-written lists replaced by the list in the template's code.

## Setup

```
pip install -e .
copy user-config.example.py user-config.py
```

Fill in the bot account in `user-config.py`, then log in once with
`python -m pywikibot.scripts.login`. Pywikibot finds `user-config.py` in the
current folder, or in the folder the `PYWIKIBOT_DIR` environment variable
points at. Without installing, `python -m infoboxfix ...` works the same as
`infoboxfix ...`.

Create the bot's [run page](#the-run-page), `User:<bot account>/Run`,
containing just `yes`. The bot won't start without it.

## Running it

```
infoboxfix --template:"Infobox venue" --show-rules     # see what it would do, then stop
infoboxfix --template:"Infobox venue" -simulate        # go through the pages without saving
infoboxfix --template:"Infobox venue"                  # ask before each save
infoboxfix --template:"Infobox venue" -always          # approve the rules, then save without asking
infoboxfix --template:"Infobox venue" -always -skip-rules-prompt   # unattended: no questions at all
```

It lists the rules first and asks before it uses them. Then it shows each
page's diff and asks before saving. `-always` only skips the questions before
each save: the rules are still listed and approved once before any page is
touched. For a run with no one at the keyboard (a scheduled job, say),
`-skip-rules-prompt` skips the rules question too; the rules are still listed,
so they end up in the log.

| Option | What it does |
|---|---|
| `-template:NAME` | the template to fix (required) |
| `-rename:OLD=NEW` | an extra rename rule; can be repeated |
| `-remove:NAME` | an extra parameter to remove; can be repeated |
| `-rules:FILE` | extra rules from a file: one `old = new` per line, or `old =` to remove |
| `-show-rules` | list the rules and the tracking categories' sizes, then stop |
| `-all` | work through every article that uses the template, not just its tracking categories |
| `-cosmetic` | also save pages where the only changes are cosmetic |
| `-no-align` | don't line up the infobox |
| `-summary:TEXT` | the edit summary for this run, overriding `infoboxfix_summary` (see [Edit summary](#edit-summary)) |
| `-report:FILE` | also write the pages that need a human to FILE, as wikitext |
| `-always` | save each page without asking (the rules are still listed and approved once first) |
| `-skip-rules-prompt` | don't ask before using the rules (they are still listed); for unattended runs, usually with `-always` |

A single or double dash both work, and so do `:` and `=`. Any Pywikibot page
generator (`-page:`, `-cat:`, `-file:`, `-transcludes:`, ...) replaces the
default list of pages, and its filters (`-ns:`, `-limit:`, ...) work as
usual. So do global options like `-simulate`, `-lang:` and `-family:`.

## Where the rules come from

The rules come only from the template's current
`{{#invoke:Check for deprecated parameters|check|...}}` call:

- `old = new` pairs;
- `_remove = a; b`;
- `_regexpN = pattern = replacement`.

Chains in the list are followed: `othernames = other_names` plus
`other_names = nickname` sends `othernames` straight to `nickname`.

**If the template's code lists no deprecated parameters, the bot says so and
stops without changing anything.** It never works them out from the
template's history or from similar-looking names. A deprecation may have
been taken out of the code for a good reason. And once a deprecation is
finished, ParamBot may have a rule for it and be handling those parameters
already.

`-rename`, `-remove` and `-rules` add to the template's list and win where
they disagree with it. They don't stand in for it, so the bot still stops if
the template lists nothing.

Every rule must rename to a name the template accepts (per its
`Check for unknown parameters` list), or the bot drops the rule with a
warning. If none of the listed rules can be used, the bot stops.

## Edit summary

Set the summary in `user-config.py`:

```python
infoboxfix_summary = ('{counts} - [[Template:{template}]] - '
                      '[[Wikipedia:Bots/Requests for approval/PhuzBot 15|PhuzBot 15]]')
```

which gives summaries like

> Updating 27 deprecated parameters, aligned parameters - [[Template:Infobox French commune]] - [[Wikipedia:Bots/Requests for approval/PhuzBot 15|PhuzBot 15]]

- The placeholders:
  - `{template}`: the template's name;
  - `{counts}`: what the bot did, counted (`Updating 27 deprecated parameters,
    aligned parameters`). Renamed and removed parameters both count, and it
    adds `bypassed template redirect` when that happened;
  - `{changes}`: what the bot did, by name (`renamed deprecated parameters:
    a → b; removed c; bypassed template redirect; aligned parameters`).
- Write a literal `{` or `}` as `{{` or `}}`.
- Without the setting, the summary is `[[Template:{template}|{template}]]: {changes}`.
  `-summary:TEXT` overrides both for one run.
- A summary over 500 characters has `{changes}` cut down to counts.
- The bot checks the summary before it logs in, and stops on an unknown
  placeholder.

Pywikibot ignores settings it doesn't know, so the bot reads this one from
`user-config.py` itself. It parses the file without running it, so the value
must be a plain string; parentheses and adjacent strings are fine.

## The run page

The bot only runs while its run page says `yes`, so any editor can stop it.
The page is `User:<bot account>/Run` (`User:PhuzBot/Run`), or whatever
`infoboxfix_run_page` names in `user-config.py`:

```python
infoboxfix_run_page = 'User:PhuzBot/Run'
```

- **When it's checked:** once after logging in, and again, read fresh from
  the wiki, just before every save.
- **What counts as "yes":** the page must say `yes` and nothing else.
  Case, surrounding whitespace and HTML comments are ignored, so
  `yes <!-- change to "no" to stop the bot -->` works. Anything else stops
  the bot: `no`, an empty page, a redirect, or no page at all.
- **At startup:** if the page doesn't say `yes`, the bot stops before
  reading the template. `-show-rules` only notes it, since it never edits.
- **During a run:** the bot stops before the next save, says why, prints
  its usual end-of-run report (and writes `-report:FILE`), and exits with
  code 1.
- **Cost:** one small request per save, well within the 20 edits a minute
  the bot is limited to.

Leave the page unprotected, so that anyone who spots a problem can stop the
bot.

## Safeguards

- **No duplicates.** A rename never leaves two copies of a name. If the new
  name is already there:
  - an empty or identical old parameter is removed;
  - an empty new parameter is removed before the rename;
  - two different values are left alone and listed for a human.

  The AWB module only did this for the names in its `array5`; here it's
  always on.
- **No cosmetic-only edits** (WP:COSMETICBOT). A page is only saved if a
  parameter with a value was renamed or removed. Bypassing a redirect,
  lining up, and changes to empty parameters are made only alongside a real
  fix, unless you pass `-cosmetic`.
- **No lost text.** The infobox isn't lined up if it has unnamed or repeated
  parameters, or if rebuilding it would lose any text. The AWB module's
  fingerprint check is used for this.
- **Only real calls.** Calls inside comments, `<nowiki>`, `<pre>` and similar
  are left alone. Child infoboxes (`|embedded={{Infobox venue|child=yes...}}`)
  are fixed too.
- **Problems are listed.** Pages with conflicts, or with unknown parameters
  the bot couldn't place, are listed at the end of the run (and in
  `-report:FILE`).
- **Anyone can stop it.** The bot checks its [run page](#the-run-page)
  before every save and stops as soon as it no longer says `yes`.
- Pywikibot honours `{{bots}}` and `{{nobots}}`, `put_throttle` and `maxlag`.

## Tests

```
python -m unittest discover -s tests -t .
```
