import json, os, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'pkg'))
if not os.path.isdir(os.path.join(os.path.dirname(__file__), 'pkg', 'reclaimer_control')):
    import pytest
    pytest.skip('needs a copy of the live reclaimer_control package in pkg/ (see ../README.md)', allow_module_level=True)
import pytest
from reclaimer_control import fleet_rules_admin as FA

HOOK = "https://discord.com/api/webhooks/123456789012345678/" + "A" * 68
RULE = {'id': 'w', 'name': 'Welcome', 'when': {'type': 'join'}, 'then': [{'type': 'tell_player', 'message': 'Hi {player}'}]}


def console(tmp_path, fail=()):
    (tmp_path / 'fleet').mkdir()
    (tmp_path / 'state').mkdir()
    (tmp_path / 'fleet' / 'fleet.json').write_text(json.dumps({'nodes': [{'id': 'node-a', 'ssh': 'k'}, {'id': 'node-b', 'ssh': 'p'}]}))
    calls = []

    def transport(spec, action, timeout=25, **args):
        calls.append((spec['id'], action, args))
        if spec['id'] in fail:
            raise ConnectionError('down')
        if action == 'configure_rules':
            return {'ok': True, 'signature': 'sig-' + spec['id'], 'rules': len(args['doc']['rules'])}
        if action == 'rules_status':
            return {'ok': True, 'history': [{'at': 1, 'rule': 'w', 'result': 'done'}]}
    c = types.SimpleNamespace(state=tmp_path / 'state', fleet=tmp_path / 'fleet', transport=transport)
    return c, calls


def test_save_validates_merges_pushes_and_masks(tmp_path):
    c, calls = console(tmp_path, fail=('node-b',))
    out = FA.save(c, 'tester', {'enabled': True, 'rules': [RULE], 'webhooks': {'alerts': HOOK},
                                'roles': {'b' * 64: {'name': 'Bravo', 'roles': ['vip', 'nope']}}})
    assert out['enabled'] and out['rules'][0]['id'] == 'w' and out['roles']['b' * 64]['roles'] == ['vip']
    assert out['webhooks'] == {'alerts': 'https://discord.com/api/webhooks/123456789012345678/••••••'}
    assert out['nodes']['node-a']['ok'] and not out['nodes']['node-b']['ok'] and out['nodes']['node-b']['error'] == 'ConnectionError'
    pushed = [a for n, act, a in calls if act == 'configure_rules']
    assert pushed and pushed[0]['doc']['webhooks']['alerts'] == HOOK  # nodes get the real URL
    assert {h['node'] for h in out['history']} == {'node-a'}
    # a partial change keeps the rest; "keep" keeps a webhook's secret without resending it
    out = FA.save(c, 'tester', {'enabled': False, 'webhooks': {'alerts': 'keep'}})
    assert not out['enabled'] and out['rules'][0]['id'] == 'w' and 'alerts' in out['webhooks']
    stored = json.loads((tmp_path / 'state' / 'fleet-rules.json').read_text())
    assert stored['webhooks']['alerts'] == HOOK and stored['by'] == 'tester'
    out = FA.save(c, 'tester', {'webhooks': {'alerts': None}})
    assert out['webhooks'] == {}


@pytest.mark.parametrize('bad', [{'enabled': 'yes'}, {'rules': [{'when': {'type': 'kick'}}]},
                                 {'webhooks': {'x': 'https://example.com/hook'}}, {'roles': {'short': {'roles': ['admin']}}},
                                 {'rules': [RULE, RULE]}, 'nope'])
def test_bad_changes_are_refused_and_nothing_is_pushed(tmp_path, bad):
    c, calls = console(tmp_path)
    with pytest.raises(ValueError):
        FA.save(c, 'tester', bad)
    assert not calls and not (tmp_path / 'state' / 'fleet-rules.json').exists()


def test_operator_console_routes_rules(tmp_path):
    from reclaimer_control import operator_console as oc
    src = open(oc.__file__).read()
    assert "'/api/rules'" in src and 'fleet_rules_admin import save' in src and 'fleet_rules_admin import view' in src
