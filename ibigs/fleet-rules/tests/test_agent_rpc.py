import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'pkg'))
if not os.path.isdir(os.path.join(os.path.dirname(__file__), 'pkg', 'reclaimer_control')):
    import pytest
    pytest.skip('needs a copy of the live reclaimer_control package in pkg/ (see ../README.md)', allow_module_level=True)


def test_rpc_configure_and_status(tmp_path, monkeypatch):
    monkeypatch.setenv('RECLAIMER_STATE', str(tmp_path))
    import importlib
    import reclaimer_control.agent as agent
    importlib.reload(agent)
    req = {'action': 'configure_rules', 'request_id': 'req-12345678', 'expires_at': time.time() + 600,
           'doc': {'enabled': False, 'rules': [], 'roles': {}, 'webhooks': {}}}
    out = agent.rpc(req)
    assert out['ok'] and out['enabled'] is False
    again = agent.rpc(req)  # the journal makes a repeated request a no-op that returns the first answer
    assert again == out
    st = agent.rpc({'action': 'rules_status'})
    assert st['ok'] and st['enabled'] is False
    assert json.loads((tmp_path / 'rules' / 'policy.json').read_text())['enabled'] is False
