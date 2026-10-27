# Copy to user-config.py (in this folder, or in the folder PYWIKIBOT_DIR
# points at) and fill in the bot account.  Log in once with
#   python -m pywikibot.scripts.login
# using a bot password from Special:BotPasswords, kept in user-password.py.
family = 'wikipedia'
mylang = 'en'
usernames['wikipedia']['en'] = 'YourBotAccount'
password_file = 'user-password.py'
# Contact details for Wikimedia's user-agent policy: the bot's user page
user_agent_description = 'https://en.wikipedia.org/wiki/User:YourBotAccount'
put_throttle = 10   # seconds between edits
maxlag = 5

# infoboxfix's edit summary (optional).  {template} is the template's name,
# {changes} lists what the bot did to the page, and {counts} counts it
# ("Updating 27 deprecated parameters, aligned parameters").  Without it the
# summary is "[[Template:{template}|{template}]]: {changes}".
# infoboxfix_summary = ('{counts} - [[Template:{template}]] - '
#                       '[[Wikipedia:Bots/Requests for approval/YourBot 1|YourBot 1]]')

# infoboxfix's run page (optional).  The bot only runs while this page says
# "yes", and checks it before every save.  Default: User:<bot account>/Run.
# infoboxfix_run_page = 'User:YourBotAccount/Run'
