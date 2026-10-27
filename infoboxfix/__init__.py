"""A Pywikibot bot that fixes deprecated parameters in calls to an infobox."""

import warnings

__version__ = '0.1.0'

# Pywikibot warns about any user-config.py variable it doesn't know.  The
# infoboxfix_* ones are the bot's own (see cli.user_config_setting).
warnings.filterwarnings('ignore', message=r'(?s).*"infoboxfix_\w+"\s+is\s+defined',
                        category=UserWarning)
