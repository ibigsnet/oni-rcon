"""Bounded, evidence-based observations; never an input to moderation actions.

Shared by the native console and the private fleet observer. Event time, not
relay arrival time, drives windows. Gaps reset pattern state, not report history.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re
import time
import unicodedata

HISTORY = 200
RETENTION = 3600
VOTE_WINDOW = 300
CHAT_REPEAT = 120
FAST_MODES = re.compile(r'\b(?:infection|infected|swat|fat[ _-]?kid)\b', re.I)


def words(value, limit=240):
    text = ''.join(c for c in str(value or '') if not unicodedata.category(c).startswith('C'))
    text = re.sub(r'\brfk_[A-Za-z0-9_-]+', '[API KEY]', text)
    return ' '.join(text.split())[:limit]


def quiet_kill_mode(context=None):
    """SWAT often reports its engine mode as Slayer; include the configured lobby tags/name."""
    context = context or {}
    tags = context.get('tags') or []
    if isinstance(tags, str):
        tags = [tags]
    return bool(FAST_MODES.search(' '.join(str(v or '') for v in
                (context.get('mode'), context.get('name'), *tags))))


@dataclass(frozen=True)
class ChatRequest:
    kind: str
    topic: str
    target: str = ''


def chat_request(text):
    """Recognise explicit requests/allegations, not isolated game-mod or cheat keywords.

    This is a conservative English-language triage rule, never a misconduct verdict.
    A topic groups repeat requests from different speakers on the same server.
    """
    text = unicodedata.normalize('NFKC', words(text, 700)).casefold().replace('’', "'")
    role = r'(?:admins?|moderators?|mods?)'
    bad = r'(?:hack(?:ing|er|s)?|cheat(?:ing|er|s)?|aimbot(?:ting|ter)?|wallhack(?:ing)?|speedhack(?:ing)?)'
    help_request = None
    for clause in re.split(r'[.!?;]+\s*|\bbut\b', text):
        clause = clause.strip(' ,:')
        if not clause:
            continue
        # Negated requests, denials and instructions about commands are not reports.
        if re.search(r"\b(?:don't|dont|do not|doesn't|isn't|aren't|not|never|stop|no need)\b", clause):
            continue
        if (re.fullmatch(r'(?:hey |please |pls |plz )?'+role+r'(?: please| pls| plz)?', clause)
                or re.search(r'\b(?:need|want|looking for|paging|calling) (?:an? |the )?(?:admin|moderator)s?\b', clause)
                or re.search(r'\b(?:any|is there an?|are there any) '+role+r'(?:\s+(?:online|around|here|available))?$', clause)
                or re.search(r'\b'+role+r'\s+(?:online|around|here|available|help)\b', clause)
                or re.fullmatch(r'(?:i |we )?need a mod(?: please| pls| plz)?', clause)):
            help_request = ChatRequest('admin_help', 'admin')
        if (re.search(r'\b(?:need|looking for|could use) (?:some )?(?:help|assistance)\b', clause)
                or re.fullmatch(r'(?:please |pls |plz )?help(?: me| us)?(?: please| pls| plz)?', clause)
                or re.search(r'\b(?:can|could|would) (?:someone|anyone|you|an admin|a mod) (?:please )?help\b', clause)):
            help_request = help_request or ChatRequest('admin_help', 'help')
        request = re.match(
            r'^(?:'+role+r'[, :]+)?(?:(?:can|could|would) (?:someone|anyone|you|we|an admin|a mod) (?:please )?'
            r'|(?:please|pls|plz) )?(?:vote[ -]?)?(kick|ban|mute)\s+(.+)', clause)
        if request:
            target = re.split(r'\b(?:because|for|please|pls|plz)\b', request[2], maxsplit=1)[0].strip(' ,:')
            if target and not re.match(r'(?:is|was|are|the (?:command|button|option|system|feature)|command|button)\b', target):
                return ChatRequest('moderation_request', 'player:'+target[:80], target[:80])
        allegation = re.search(r'^(.{1,80}?)\s+(?:is|are|keeps|was|has been)\s+(?:(?:clearly|obviously|definitely|a|an)\s+)*'
                               r'(?:'+bad+r'|using (?:an? )?(?:aimbot|wallhack))\b', clause)
        if allegation:
            target = allegation[1].strip()[:80]
            return ChatRequest('player_report', 'player:'+target, target)
        removal = re.match(r'^(.{1,80}?)\s+(?:should|needs? to) be (?:kicked|banned|muted)\b', clause)
        if removal:
            target = removal[1].strip()[:80]
            return ChatRequest('moderation_request', 'player:'+target, target)
        if re.search(r"\b(?:there(?:'s| is)|we (?:have|got)) (?:a |an )?(?:hacker|cheater|aimbotter)\b", clause):
            return ChatRequest('player_report', 'unidentified-player')
    return help_request


def public_roster(players):
    """Only public identity fields can travel with a finding; never addresses or fingerprints."""
    result = []
    for player in (players if isinstance(players, list) else [])[:64]:
        if not isinstance(player, dict):
            continue
        guests = player.get('guest_players')
        for p in [player, *(guests[:4] if isinstance(guests, list) else [])]:
            if isinstance(p, dict):
                result.append({k: words(p.get(k), 128) for k in ('id', 'player_id', 'engine_id', 'name')})
    return result


def subject(value, names=None, roster=()):
    """Resolve a slot/name at event time. Navigation later revalidates the full public ID."""
    names = names or {}
    if isinstance(value, dict):
        ident = words(value.get('player_id') or value.get('id'), 128)
        name = words(value.get('name') or names.get(ident), 80)
    else:
        ident = words(value, 128)
        name = words(names.get(ident, value), 80)
    matches = [p for p in roster if ident and ident in (p.get('id'), p.get('player_id'), p.get('engine_id'))]
    if not matches and name:
        matches = [p for p in roster if words(p.get('name')).casefold() == name.casefold()]
    if len(matches) == 1:
        p = matches[0]
        return {'id': p.get('player_id') or p.get('id') or '', 'name': p.get('name') or name}
    # Do not retain reusable engine slots, a guessed ID, or select duplicate names.
    stable = ident if re.fullmatch(r'[0-9a-fA-F]{32,128}', ident) and not matches else ''
    return {'id': stable, 'name': name or 'Unknown player'}


@dataclass(frozen=True)
class Finding:
    id: str
    server: str
    at: float
    kind: str
    level: str
    title: str
    evidence: str
    source: str = 'Native RCON events'
    subjects: tuple = ()
    reporter: dict = field(default_factory=dict)

    def row(self):
        return asdict(self)


@dataclass
class Observations:
    votes: deque = field(default_factory=lambda: deque(maxlen=128))
    kills: deque = field(default_factory=lambda: deque(maxlen=512))
    recent: dict = field(default_factory=dict)
    cooldowns: dict = field(default_factory=dict)
    last: float = 0
    combat: tuple = ()


class Findings:
    def __init__(self, now=time.time):
        self.now = now
        self.history = deque(maxlen=HISTORY)
        self.states = {}
        self.acknowledged_at = 0

    def reset(self, server):
        # A round change or reconnect breaks evidence windows, not alert suppression.
        if server in self.states:
            self.states[server] = Observations(cooldowns=self.states[server].cooldowns)

    def acknowledge(self):
        self.acknowledged_at = self.now()

    def recent(self):
        now = self.now()
        return [r for r in reversed(self.history) if 0 <= now - r.at <= RETENTION]

    def add(self, finding):
        if not 0 <= self.now() - finding.at <= RETENTION or any(r.id == finding.id for r in self.history):
            return False
        self.history.append(finding)
        return True

    def ingest(self, server, event, names=None, context=None, roster=()):
        now, names, context = self.now(), names or {}, context or {}
        stamp = event.get('time', now)
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 <= now - stamp <= 30:
            return []
        kind = event.get('event')
        if kind in ('lagged', 'phase') or (kind == 'control' and event.get('action') == 'kill_feed' and not event.get('ok')):
            self.reset(server)
            return []
        if server not in self.states:
            if len(self.states) >= 256:
                self.states.pop(next(iter(self.states)))
            self.states[server] = Observations()
        state = self.states[server]
        combat = (context.get('map'), context.get('mode'), quiet_kill_mode(context))
        if combat != state.combat:
            state.kills.clear()
            state.combat = combat
        if stamp < state.last:
            return []
        state.last = stamp
        if 'time' in event:
            signature = hashlib.sha256(json.dumps(event, sort_keys=True, default=str).encode()).hexdigest()
            state.recent = {k: t for k, t in state.recent.items() if stamp - t <= 30}
            if signature in state.recent:
                return []
            if len(state.recent) >= 1024:
                state.recent.pop(next(iter(state.recent)))
            state.recent[signature] = stamp
        state.cooldowns = {k: t for k, t in state.cooldowns.items() if stamp - t < VOTE_WINDOW}
        found = []

        def player(value):
            if isinstance(value, dict):
                key = value.get('id') or value.get('player_id') or value.get('name')
                label = value.get('name') or names.get(str(key)) or key
            else:
                key, label = value, names.get(str(value), value)
            return words(key, 100), words(label, 80) or 'Unknown player'

        def report(key, category, level, title, evidence, interval=60, *, subjects=(), reporter=None):
            if stamp - state.cooldowns.get(key, float('-inf')) < interval:
                return False
            if len(state.cooldowns) >= 512:
                state.cooldowns.pop(next(iter(state.cooldowns)))
            state.cooldowns[key] = stamp
            ident = hashlib.sha256(json.dumps([server, stamp, key, evidence]).encode()).hexdigest()[:24]
            row = Finding(ident, str(server), stamp, category, level, words(title), words(evidence, 700),
                          subjects=tuple(subject(v, names, roster) for v in subjects),
                          reporter=subject(reporter, names, roster) if reporter is not None else {})
            if self.add(row):
                found.append(row)
                return True
            return False

        if kind == 'vote':
            text = words(event.get('text'))
            caller = event.get('caller') or event.get('name') or event.get('player')
            target = event.get('target') or event.get('target_id')
            started = event.get('action') in ('start', 'started') and event.get('vote_type') == 'kick'
            match = re.fullmatch(r'called a vote to kick (.+)', text, re.I)
            if match:
                target, started = match[1].strip('"'), True
            actor, actor_name = player(caller)
            victim, victim_name = player(target)
            # Results, yes/no votes and chat about votes do not count as starts.
            if started and actor and victim and actor != victim:
                state.votes.append((stamp, actor, victim, actor_name, victim_name))
                while state.votes and stamp - state.votes[0][0] > VOTE_WINDOW:
                    state.votes.popleft()
                attempts = [v for v in state.votes if v[2] == victim]
                callers = sorted({v[3] for v in attempts})
                if len(attempts) >= 3:
                    report(('target', victim), 'repeated_votes', 'review', f'Repeated kick votes against {victim_name}',
                           f'{len(attempts)} vote starts in 5 minutes from {len(callers)} caller(s): '+', '.join(callers)+'. Repeated targeting warrants review; it does not establish misconduct.', 120, subjects=(target,))
                forward = [v for v in state.votes if v[1:3] == (actor, victim)]
                reverse = [v for v in state.votes if v[1:3] == (victim, actor)]
                if len(forward) >= 2 and len(reverse) >= 2:
                    report(('fight', *sorted((actor, victim))), 'vote_fight', 'review',
                           f'Back-and-forth kick votes: {actor_name} / {victim_name}',
                           f'{len(forward)} starts in one direction and {len(reverse)} in the other within 5 minutes. Check the vote and chat history before acting.', 120, subjects=(target, caller))
        elif kind == 'kill':
            if combat[2]:
                return []  # Ordinary streaks and kill-count suspicion are unreliable in these modes.
            killer, name = player(event.get('killer'))
            victim, _ = player(event.get('victim'))
            if killer and victim and killer != victim and not any(event.get(k) is True for k in ('suicide', 'betrayal', 'teamkill')):
                state.kills.append((stamp, killer, victim))
                while state.kills and stamp - state.kills[0][0] > 10:
                    state.kills.popleft()
                kills = [k for k in state.kills if k[1] == killer]
                if len(kills) >= 5 and len({k[2] for k in kills}) >= 3 and stamp-state.cooldowns.get(('burst-server',), float('-inf')) >= 60:
                    if report(('burst', killer), 'kill_burst', 'highlight', f'{name}: {len(kills)} kills in 10 seconds',
                              f'Observed {len(kills)} kill events against {len({k[2] for k in kills})} opponents. A highlight, not evidence of cheating.', 300, subjects=(event.get('killer'),)):
                        state.cooldowns[('burst-server',)] = stamp
                fast = [k for k in kills if stamp-k[0] <= 3]
                if 'time' in event and len(fast) >= 12 and len({k[2] for k in fast}) >= 6:
                    report(('rate', killer), 'unusual_rate', 'review', f'Unusual kill burst: {name}',
                           f'{len(fast)} native-timestamped kills against {len({k[2] for k in fast})} opponents in 3 seconds. Explosives, custom modes or delayed server telemetry can explain this. Review gameplay; this is not a cheat verdict.', 120, subjects=(event.get('killer'),))
        elif kind == 'cheat':
            ident, name = player(event.get('player_id') or event.get('id') or event.get('name') or event.get('player'))
            report(('native', ident), 'native_flag', 'review', f'Server anti-cheat flag: {name}',
                   words(event.get('text') or event.get('reason') or 'No reason supplied')+'. Native server report; investigate before deciding on moderation.',
                   subjects=({'id': ident, 'name': event.get('name') or name},))
        elif kind == 'chat' and str(event.get('channel', '')).casefold() != 'server':
            request = chat_request(event.get('text'))
            if request:
                reporter = {'id': event.get('player_id') or event.get('id') or event.get('player'), 'name': event.get('name')}
                ident, name = player(reporter)
                targets = (request.target,) if request.target else (reporter,) if request.kind == 'admin_help' else ()
                actor_key = ('chat-actor', ident, request.kind)
                if stamp-state.cooldowns.get(actor_key, float('-inf')) >= 60:
                    title = {'admin_help': 'Player needs help', 'moderation_request': 'Player requests moderation',
                             'player_report': 'Player reported possible misconduct'}[request.kind]
                    heading = f'{title}: {name}'
                    if request.target:
                        heading = f"{title}: {subject(request.target, names, roster)['name']} · from {name}"
                    if report(('chat-topic', request.topic), request.kind, 'review', heading,
                              'Unverified player chat: '+words(event.get('text'), 500)+
                              ' — Review the conversation before acting.', CHAT_REPEAT, subjects=targets, reporter=reporter):
                        state.cooldowns[actor_key] = stamp
        elif kind in ('medal', 'award'):
            # A kill/death ratio is insufficient: require an explicit native award.
            medal = words(event.get('medal') or event.get('award')).casefold()
            if medal == 'perfection':
                ident, name = player(event.get('player_id') or event.get('player') or event.get('name'))
                report(('perfection', ident), 'perfection', 'highlight', f'Server reported Perfection: {name}',
                       'Explicit Perfection award in the native event feed. Never inferred from a partial match.', 60,
                       subjects=({'id': ident, 'name': name},))
        return found
