# Automation rules

Rules the desktop console follows for you: **WHEN** something happens, **ONLY IF** it holds, **THEN DO** these things.
Open them with **F9**, or Ctrl+P → *Automation rules*.

Adapted from the rules in [ibigsnet/oni-rcon](https://github.com/ibigsnet/oni-rcon) by RifleJock.

## What a rule looks like

> **WHEN** a player reaches a killing spree (5, 10, 15, 20, 25, 30)
> **THEN** say in chat: `{player} is on a {medal}! ({kills} kills)`

- **WHEN** (one): a medal, spree or multi-kill; a weapon kill, first blood, N kills; a join or leave; a `!command`;
  a vote starting; a match starting or ending; a server going down or coming back; a server filling or emptying; the
  player count crossing N; a server's frame rate dropping under, or back over, N; every N minutes; or at a time of day
  (cron-style, with a timezone).
- **ONLY IF** (up to 8, all must hold): the player count, an admin online (or none), the player's role, their K/D,
  the day or time of day, the server's frame rate, another server's player count or state, the map, the mode.
- **THEN DO** (1 to 8, in order): say in chat (here, everywhere, or on another server), message the player, the admins
  or everyone with a role privately, cancel the vote, shuffle the teams, wait, post to Discord or Telegram.
- **Limits.** A *cooldown* (seconds before it can fire again for the same server and player) and a *per-hour* count.
  Moments such as a medal or a join default to no cooldown; states such as a server filling default to 30 seconds.

Messages can use `{player}`, `{server}`, `{count}`, `{max}`, `{medal}`, `{kills}`, `{weapon}`, `{victim}`, `{kd}`,
`{fps}`, `{map}`, `{mode}`, `{admins}`, `{target}`, `{args}`, `{down}` and `{time}`.

## Making one

- **TEMPLATE** starts from a ready-made rule and walks through it so you can change the words. There are about sixty:
  chat hype (sprees, multi-kills, weapon kills, betrayals), match announcers, timed reminders, join greetings, `!commands`
  (`!help`, `!rules`, `!players`, `!map`, `!fps`, `!admins`, `!report`, `!bug`), feeds and alerts to Discord or Telegram
  (medals, joins, matches, busy or empty servers, lag, outages, kick votes, suspicious K/D), and vote blockers. A template
  that posts to Discord asks which of your webhooks to use.
- **NEW** builds one a step at a time: name, which servers, WHEN, ONLY IF, THEN DO, limits, then a read-back.
- **TEST** shows what the rule would do if it fired right now on a server you pick (its conditions and limits aside),
  and offers to really do it.
- **TURN OFF** keeps a rule but stops it; **DELETE** removes it.

## Roles

**ROLES** says who rules treat as **ADMIN**, **VIP** or **MOD**. ADMIN here is for rules only (who an *admin online*
condition or a *message the admins* step means); the server's own admin list is under F4. While anyone has the VIP
role, a built-in rule cancels any vote to kick them.

## Discord

**DISCORD** adds webhooks by name. In Discord: the channel's gear → Integrations → Webhooks → New Webhook → Copy
Webhook URL. The URL is kept by the app in `discord.json` beside the config (readable only by you) and the window only
ever sees it with its secret part hidden. Rules post to it by name; **SEND A TEST** checks it. Posts are at least 2
seconds apart and never ping `@everyone`, roles or users.

**Several webhooks.** Add as many as you like (a public channel, a staff channel, one per server). A *Post to Discord* step
lets you tick which webhooks it goes to, up to 10, and posts to each one. Templates ask the same way, so nothing is sent to
a webhook you didn't pick. Each webhook keeps its own 2-second pacing and queue, so a busy one doesn't hold up the others.

## Telegram

**TELEGRAM** adds a bot and a chat under a name, for rules to post to.

1. In Telegram, message **@BotFather**, send `/newbot` and pick a name. It replies with a bot token.
2. Open a chat with your bot and send it a message. For a group, add the bot and send a message there; for a channel,
   make the bot an admin that can post, then post once.
3. In the console choose **TELEGRAM → + Add a chat**, paste the token and pick your chat from the list. The console finds
   it from the bot's recent messages. If nothing shows yet, message the bot and choose *Look again*, or type the chat's
   number (a group's starts with `-`) or a public channel's `@name`. Then name it, like `alerts`.

The token is kept by the app in `telegram.json` beside the config (readable only by you) and the window only ever sees
the bot's number and the chat. Rules post to it by name; **SEND A TEST MESSAGE** checks it, and a refusal says why (a
revoked token, a bot removed from the chat). Posts are plain text, never previewed, and at least 3 seconds apart, inside
Telegram's limit of 20 a minute to a group. Anyone with the token can post as the bot: send `/revoke` to @BotFather to
cancel it. If something else is already reading the bot's messages (a webhook, or another program), they can't be listed
to find the chat; type the chat's number instead.

**Check the kind of chat you pick.** Anyone can message a bot, or add it to a group, and then appears in the list under a
name they chose. The line starts with the kind (`PRIVATE`, `GROUP` or `CHANNEL`), which is Telegram's and can't be set by
them, and invisible or direction-changing characters in names are dropped. SEND A TEST MESSAGE shows where it lands. To
stop strangers adding the bot to groups at all, send `/setjoingroups` to @BotFather and choose Disable.

**What gets sent.** The words you wrote go as written. An `@` in a player's name or in their `!admin` text is replaced with a
look-alike (`＠`) so it can't become a mention that pings someone. Everything else players type is relayed as typed, and
Telegram makes links in it clickable, so treat them as you would in-game chat.

**When Telegram is slow.** If Telegram says it is rate limiting (it says for how long, up to 30 seconds) or its gateway
stumbles, the post is tried once more. Any other answer, a refusal included, is final and shown in the command log.

The ready-made templates that post to Discord post to Telegram instead when a chat is all you've set up, with the words
that say "Discord" changed to "Telegram" to match.

If `telegram.json` is ever damaged (a bad hand edit), the console says so and won't write over it, since that would lose
every other token in it. Fix it or delete it and add the chats again.

This is one-way: the console tells Telegram things. The bot doesn't take commands back, so nobody in the chat can reach a
server through it.

## Safety limits

- Chat from rules is held to **20 lines a minute per server**, across every rule.
- Posts to Discord or Telegram wait their turn, 2 and 3 seconds apart. At most **20 wait for one service**; past that a
  post is skipped and the command log says so, since a backlog would only deliver stale alerts, late.
- *Shuffle teams* only runs while a match is being played.
- A step that fails never stops the others. Each run, and what happened to each step, is written to the command log
  as `AUTO` and shown under RECENT in the list.
- Nothing runs in demo mode against a real Discord channel or Telegram chat.

## What to know

- Rules run **while the console is open**. They act through the same connection as the buttons do, so a closed
  console, or a server it has lost, does nothing.
- Rules live in `automation.json` beside the config; a rule that no longer checks out is skipped and said so.
- A vote can't be switched off on the dedicated server, so the *Votes off* template cancels each vote the moment it
  starts and says so in chat.
- **`!commands` stay out of chat on Reclaimer 0.10.3+.** On connect, and whenever the rules change, the console
  registers each enabled `!command` rule's word with the server (`commands add`, up to 16, rules first, then the words
  Studio scripts hook with `command()`). A player's `!kills` or `/kills` then reaches only the server, never everyone's
  chat, and the game's `/help` lists it under the rule's name. The server's own `role` comes with it, so *The player has
  a role* works for staff the console's roles don't list. Players on games older than 0.10.3, and servers that refuse
  `commands`, keep the old way: the word goes to chat and the rule matches it there. The command log shows the words
  registered (`PRIVATE COMMANDS`). Registrations last until the server restarts, so with the console closed a player's
  `/kills` gets no answer.
