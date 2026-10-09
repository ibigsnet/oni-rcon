"""Optional join-time latency from server logs, with explicit commands and bounded process lifetime.

The managed fleet never launches these followers: it has neither game logs nor SSH/Docker credentials. Standalone
and tenant operators can opt in to a command they can already run. Only player IDs and integer milliseconds are
retained; log text and addresses are not sent to the terminal or persisted.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import shutil
import signal

from .config import ping_command
from .install import SSH_OPTS

JOIN_PING = re.compile(r'player ID ([0-9a-f]{1,128}), ping (\d{1,7}) ms')


def remote_command(argv: list[str]) -> str:
    """A fixed POSIX supervisor; every configured argument is shell-quoted as data.

    The remote host needs setsid. Closing SSH's stdin ends the reader and terminates the follower's process group,
    including children such as a log helper. The local SSH process has its own process group as well.
    """
    return ("supervisor=$$; reader=; exec 3<&0; setsid " + shlex.join(argv) + " </dev/null & follower=$!; "
            "trap 'kill -TERM -- -\"$follower\" 2>/dev/null || true; "
            "kill -KILL -- -\"$follower\" 2>/dev/null || true; "
            "if [ -n \"$reader\" ]; then kill -TERM \"$reader\" 2>/dev/null || true; fi' EXIT; "
            "trap 'exit 0' HUP INT TERM; "
            "(while IFS= read -r line <&3; do :; done; kill -TERM \"$supervisor\" 2>/dev/null || true) "
            "& reader=$!; wait \"$follower\"")


async def stop_follower(proc):
    if proc.stdin:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            proc.stdin.close()
    # Terminate the complete local follower group, even if its parent already exited but left children behind.
    with contextlib.suppress(ProcessLookupError):
        if os.name == 'posix':
            os.killpg(proc.pid, signal.SIGTERM)
        elif proc.returncode is None:
            proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        if os.name != 'posix' and proc.returncode is None:
            proc.kill()
    finally:
        # A child can outlive a parent that exited promptly. Clean up the remaining group too.
        with contextlib.suppress(ProcessLookupError):
            if os.name == 'posix':
                os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()


async def follow_join_pings(server, pings: dict) -> None:
    """Restart a dropped log source with bounded backoff; cancellation always stops the follower."""
    if server.url:
        return  # includes fleet://; no process or extra permission in the management terminal
    argv = ping_command(server.ping_log, server.port)
    if server.ssh:
        if server.ssh.startswith('-') or any(c in server.ssh for c in '\x00\r\n'):
            raise ValueError('Invalid SSH destination for join-ping logs')
        ssh = shutil.which('ssh')
        if not ssh:
            return
        argv = [ssh, '-T', *SSH_OPTS, '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
                '--', server.ssh, remote_command(argv)]
    delay = 2
    while True:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, limit=1 << 20, start_new_session=os.name == 'posix',
                stdin=asyncio.subprocess.PIPE if server.ssh else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            async for line in proc.stdout:
                match = JOIN_PING.search(line.decode(errors='replace'))
                if match and (ms := int(match[2])) <= 60000:
                    delay = 2
                    pings[match[1]] = ms
                    if len(pings) > 5000:
                        del pings[next(iter(pings))]
        except (OSError, ValueError):
            # A missing command, lost connection, or oversized line never exposes configured arguments or logs.
            pass
        finally:
            if proc is not None:
                await stop_follower(proc)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)
