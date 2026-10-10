"""Startup integration keeps tenant selection and private preferences after the 0.8 merge."""
import pytest

from oni_rcon import __main__ as cli
from oni_rcon.app import OniApp
from oni_rcon.tenant import TenantApp


@pytest.mark.parametrize('tenant,flags,mode', [(True, [], 'full'), (True, ['--intro', 'quick'], 'quick'),
                                             (True, ['--intro=off'], 'off'),
                                             (True, ['--intro', 'full', '--no-intro'], 'off'),
                                             (False, ['--no-intro'], 'off')])
def test_cli_keeps_tenant_controls_and_intro_precedence(monkeypatch, tmp_path, tenant, flags, mode):
    config = tmp_path / 'oni-rcon.toml'
    monkeypatch.setattr(cli, 'user_config', lambda: config)
    monkeypatch.setattr(cli, 'resolve_passwords', lambda servers: None)
    monkeypatch.setattr(cli.sys, 'argv', ['oni-rcon', '127.0.0.1:49175', *flags])
    monkeypatch.setenv('IBIGS_ONI_TENANT', '1' if tenant else '0')
    got = []
    def capture(self):
        got.append(self)
        return None
    monkeypatch.setattr(OniApp, 'run', capture)
    cli.main()
    app, = got
    assert isinstance(app, TenantApp) is tenant
    assert app.intro == mode
    assert app.prefs.path == config.with_name('prefs.json')
    assert not app.prefs.path.exists()  # launching has not written or replaced the Forge credentials
