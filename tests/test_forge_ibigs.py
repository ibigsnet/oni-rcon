import asyncio
import hashlib
from unittest.mock import Mock
import pytest
from oni_rcon.forge import ForgeClient, ForgeError, Secret, author_of, compatible, compat_of, kind_of, latest_of, recent_of, version_label
from oni_rcon.install import File, InstallError, LocalTarget, plan
from oni_rcon.managed_forge import ManagedForgeClient
from oni_rcon.safety import clean
from oni_rcon.stats import Rounds


def official():
    asset = {'id': 'asset', 'asset_type': 'forged_map', 'original_filename': 'Encompass.json',
             'size_bytes': 3, 'sha256': hashlib.sha256(b'abc').hexdigest(),
             'download_url': 'https://files.reclaimerforge.net/files/assets/asset/Encompass.json'}
    return {'id': 'listing', 'owner_username': 'Merlin', 'recent_downloads': {'last_24h': 2, 'last_7d': 8},
            'versions': [{'id': 'draft', 'sequence': 9, 'status': 'draft'},
                         {'id': 'older', 'sequence': 1, 'status': 'published'},
                         {'id': 'release', 'sequence': 2, 'status': 'published', 'version_label': 'V2',
                          'assets': [asset], 'compatibility': {'required_reclaimer_version': '>=0.9.9'}}]}


def test_real_api_fields_release_selection_and_native_content_folders():
    item = official()
    version = latest_of(item)
    assert version['id'] == 'release' and version_label(version) == 'V2'
    assert author_of(item) == 'Merlin' and kind_of(item) == 'map'
    assert recent_of(item, '7d') == 8 and recent_of(item, '30d') is None
    assert compatible(compat_of(item), '0.9.8') is False
    assert compatible({'layouts': [], 'required_reclaimer_version': None}, '0.9.9') is None
    files = plan({'schema_version': 1, 'assets': version['assets']})
    assert files[0].size == 3 and files[0].path.startswith('Maps/Forge/rf-')
    changed = {**version['assets'][0], 'sha256': 'a' * 64}
    assert plan({'assets': [changed]})[0].path != files[0].path


def test_manifest_must_match_selected_release(tmp_path):
    async def run():
        client = ForgeClient(Secret('rfk_test_placeholder'), cache_dir=tmp_path)
        async def response(*args, **kw):
            return {'schema_version': 1, 'listing_id': 'wrong', 'version_id': 'release'}
        client.get = response
        with pytest.raises(ForgeError, match='does not match'):
            await client.manifest('listing', 'release')
    asyncio.run(run())


def test_asset_origins_and_symlinks_are_restricted(tmp_path):
    client = ForgeClient(Secret('rfk_test_placeholder'))
    assert client.allowed_url('https://files.reclaimerforge.net/files/assets/id/a.json')
    for url in ('https://127.0.0.1/admin', 'https://100.64.0.1/private', 'https://example.org/a.json',
                'https://files.reclaimerforge.net/private', 'https://user@www.reclaimerforge.net/api/listings'):
        assert not client.allowed_url(url)
    root = tmp_path / 'content'; root.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    (root / 'Maps').symlink_to(outside, target_is_directory=True)
    source = tmp_path / 'source'; source.write_bytes(b'abc')
    file = File('Maps/sub/new.json', '', 3, hashlib.sha256(b'abc').hexdigest())
    with pytest.raises(InstallError, match='symlink'):
        asyncio.run(LocalTarget(str(root)).put(source, file))
    assert not list(outside.iterdir())


def test_unknown_phase_never_counts_match_completion(tmp_path):
    rounds = Rounds(tmp_path / 'rounds.jsonl')
    rounds.observe('one', {'phase': 'in_game', 'map': 'a', 'mode': 'b', 'players': 5})
    assert rounds.observe('one', {'phase': 'unknown'}) is None
    assert not (tmp_path / 'rounds.jsonl').exists()


def test_controller_catalog_has_no_key_and_no_installer():
    async def run():
        fleet = Mock()
        fleet.request.return_value = {'mode': 'managed-read-only', 'items': [official()], 'at': 1}
        client = ManagedForgeClient(fleet)
        rows, _, reply = await client.listings(query='merlin')
        assert len(rows) == 1 and reply['_stale'] > 0
        assert (await client.listing('listing'))['id'] == 'listing'
        fleet.request.assert_called_once_with('/api/forge')
        with pytest.raises(ForgeError):
            await client.manifest('listing', 'release')
    asyncio.run(run())


def test_untrusted_terminal_controls_removed_recursively():
    assert clean({'name': '\x1b]52;c;payload\x07', 'rows': ['hi\x9b2J']}) == {'name': ']52;c;payload', 'rows': ['hi2J']}
