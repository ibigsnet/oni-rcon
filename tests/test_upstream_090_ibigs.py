"""Health keeps the managed command boundary and customer power shortcuts."""
import asyncio
import sys

import pytest

from oni_rcon import health
from oni_rcon.config import Server, load_config
from oni_rcon.demo import serve_fakes
from oni_rcon.tenant import PowerPanel, TenantApp


def test_health_rejects_shell_strings_and_oversized_reports(tmp_path):
    config = tmp_path / 'c.toml'
    config.write_text('health_cmd = "echo unsafe; touch /tmp/unsafe"\n[[server]]\nport = 1234\n')
    with pytest.raises(SystemExit, match='explicit argument list'):
        load_config(config)

    async def check():
        with pytest.raises(ValueError, match='explicit argument list'):
            await health.run('echo unsafe')
        with pytest.raises(health.HealthError, match='exceeded'):
            await health.run([sys.executable, '-c', 'print("x" * 3000000)'])
        with pytest.raises(health.HealthError, match='invalid SSH'):
            await health.run(['echo', 'test'], ssh='-oProxyCommand=bad')

    asyncio.run(check())


def test_f10_health_coexists_with_f7_power(monkeypatch):
    async def check():
        ports, keep = await serve_fakes(tick=False)
        app = TenantApp([Server(port=p, password='demo') for p in ports], by='pytest', intro=False)
        async def status(*args):
            return 0, '[{"service":"h3-01","name":"Test","running":false,"players":[]}]'
        monkeypatch.setattr(app, 'power_run', status)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.press('f10')
            await pilot.pause(0.2)
            assert app.query_one('#tabs').active == 'health'
            await pilot.press('f7')
            for _ in range(30):
                await pilot.pause(0.05)
                if isinstance(app.screen, PowerPanel):
                    break
            assert isinstance(app.screen, PowerPanel)
            assert app.query_one('#tabs').active == 'health'

    asyncio.run(check())
