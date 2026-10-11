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
CALL = re.compile(r"\b(admins?|mods?|hack\w*|cheat\w*|aimbot|wallhack)\b", re.I)


def words(value, limit=240):
    text = ''.join(c for c in str(value or '') if not unicodedata.category(c).startswith('C'))
    text = re.sub(r'\brfk_[A-Za-z0-9_-]+', '[API KEY]', text)
    return ' '.join(text.split())[:limit]


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

    def row(self):
        return asdict(self)


@dataclass
class Observations:
    votes: deque = field(default_factory=lambda: deque(maxlen=128))
    kills: deque = field(default_factory=lambda: deque(maxlen=512))
    recent: dict = field(default_factory=dict)
    cooldowns: dict = field(default_factory=dict)
    last: float = 0


class Findings:
    def __init__(self, now=time.time):
        self.now = now
        self.history = deque(maxlen=HISTORY)
        self.states = {}
        self.acknowledged_at = 0

    def reset(self, server):
        self.states.pop(server, None)

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

    def ingest(self, server, event, names=None):
        now, names = self.now(), names or {}
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

        def report(key, category, level, title, evidence, interval=60):
            if stamp - state.cooldowns.get(key, float('-inf')) < interval:
                return
            state.cooldowns[key] = stamp
            ident = hashlib.sha256(json.dumps([server, stamp, key, evidence]).encode()).hexdigest()[:24]
            row = Finding(ident, str(server), stamp, category, level, words(title), words(evidence, 700))
            if self.add(row):
                found.append(row)

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
                           f'{len(attempts)} vote starts in 5 minutes from {len(callers)} caller(s): '+', '.join(callers)+'. Repeated targeting warrants review; it does not establish misconduct.', 120)
                forward = [v for v in state.votes if v[1:3] == (actor, victim)]
                reverse = [v for v in state.votes if v[1:3] == (victim, actor)]
                if len(forward) >= 2 and len(reverse) >= 2:
                    report(('fight', *sorted((actor, victim))), 'vote_fight', 'review',
                           f'Back-and-forth kick votes: {actor_name} / {victim_name}',
                           f'{len(forward)} starts in one direction and {len(reverse)} in the other within 5 minutes. Check the vote and chat history before acting.', 120)
        elif kind == 'kill':
            killer, name = player(event.get('killer'))
            victim, _ = player(event.get('victim'))
            if killer and victim and killer != victim and not any(event.get(k) is True for k in ('suicide', 'betrayal', 'teamkill')):
                state.kills.append((stamp, killer, victim))
                while state.kills and stamp - state.kills[0][0] > 10:
                    state.kills.popleft()
                kills = [k for k in state.kills if k[1] == killer]
                if len(kills) >= 5 and len({k[2] for k in kills}) >= 3:
                    report(('burst', killer), 'kill_burst', 'highlight', f'{name}: {len(kills)} kills in 10 seconds',
                           f'Observed {len(kills)} kill events against {len({k[2] for k in kills})} opponents. A highlight, not evidence of cheating.', 30)
                fast = [k for k in kills if stamp-k[0] <= 3]
                if 'time' in event and len(fast) >= 12 and len({k[2] for k in fast}) >= 6:
                    report(('rate', killer), 'unusual_rate', 'review', f'Unusual kill burst: {name}',
                           f'{len(fast)} native-timestamped kills against {len({k[2] for k in fast})} opponents in 3 seconds. Explosives, custom modes or delayed server telemetry can explain this. Review gameplay; this is not a cheat verdict.', 120)
        elif kind == 'cheat':
            ident, name = player(event.get('player_id') or event.get('id') or event.get('name') or event.get('player'))
            report(('native', ident), 'native_flag', 'review', f'Server anti-cheat flag: {name}',
                   words(event.get('text') or event.get('reason') or 'No reason supplied')+'. Native server report; investigate before deciding on moderation.')
        elif kind == 'chat' and event.get('channel') != 'server' and CALL.search(str(event.get('text', ''))):
            ident, name = player(event.get('id') or event.get('name') or event.get('player'))
            report(('call', ident), 'player_report', 'review', f'Player asked for review: {name}',
                   'Unverified player chat: '+words(event.get('text')))
        elif kind in ('medal', 'award'):
            # A kill/death ratio is insufficient: require an explicit native award.
            medal = words(event.get('medal') or event.get('award')).casefold()
            if medal == 'perfection':
                ident, name = player(event.get('player_id') or event.get('player') or event.get('name'))
                report(('perfection', ident), 'perfection', 'highlight', f'Server reported Perfection: {name}',
                       'Explicit Perfection award in the native event feed. Never inferred from a partial match.', 60)
        return found
