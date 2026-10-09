import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest
from textual.widgets import Select, TabbedContent

from oni_rcon.forge import ForgeClient, ForgeError, Secret, author_of, latest_of, recent_of, versions_of
from oni_rcon.fleet import FleetApp
from oni_rcon.managed_forge import ManagedForgeClient
from oni_rcon.state import ForgeState


def test_sequence_and_live_schema_aliases_survive_withdrawal_support():
    listing = {
        'owner_username': 'Original',
        'recent_downloads': {'last_7d': 12},
        'latest_version': 'withdrawn',
        'versions': [
            {'id': 'old', 'sequence': 1, 'status': 'published'},
            {'id': 'latest', 'sequence': 2, 'status': 'published'},
            {'id': 'withdrawn', 'sequence': 3, 'status': 'withdrawn'},
            {'id': 'draft', 'sequence': 4, 'status': 'draft'},
        ],
    }
    assert latest_of(listing)['id'] == 'latest'
    assert [v['id'] for v in versions_of(listing)] == ['draft', 'withdrawn', 'latest', 'old']
    assert author_of(listing) == 'Original'
    assert recent_of(listing, '7d') == 12 and recent_of(listing, '30d') is None
    listing['authors'] = [
        {'user_id': 'co', 'username': 'Coauthor', 'is_owner': False},
        {'user_id': 'owner', 'username': 'Owner\x1b\u202e', 'is_owner': True},
    ]
    assert author_of(listing) == 'Owner, Coauthor'


def test_several_withdrawn_versions_remain_flagged_until_all_affected_lobbies_move(tmp_path):
    state = ForgeState(tmp_path / 'forge-state.json')
    first = {'listing_id': 'listing', 'version_id': 'one'}
    second = {**first, 'version_id': 'two'}
    newer = {**first, 'version_id': 'three'}
    state.record(['a'], first)
    state.record(['b'], second)
    assert state.withdraw('listing', 'one')
    state.acknowledge('listing')
    assert state.withdraw('listing', 'two') and state.alarms() == ['listing']
    state = ForgeState(state.path)
    assert state.pulled('listing', first) and state.pulled('listing', second)
    assert not state.pulled('listing', newer) and not state.blocks('listing')
    assert not state.withdraw('listing', 'one')
    state.record(['a'], newer)
    assert state.withdrawn_versions('listing') == {'two'}
    assert state.pulled('listing', second) and state.is_withdrawn('listing')
    state.record(['b'], newer)
    assert not state.is_withdrawn('listing')
    # Whole-listing withdrawal includes every release; repeated per-version updates do not weaken it.
    assert state.withdraw('listing') and not state.withdraw('listing', 'three')
    assert state.blocks('listing') and state.pulled('listing', newer)


@pytest.mark.parametrize('status', ['withdrawn', 'draft'])
def test_manifest_cannot_make_a_withdrawn_or_draft_release_installable(status):
    async def run():
        client = ForgeClient(Secret('rfk_test_placeholder'))
        client.get = AsyncMock(return_value={'listing_id': 'listing', 'version_id': 'version', 'status': status})
        with pytest.raises(ForgeError, match='withdrawn or unpublished'):
            await client.manifest('listing', 'version')
    asyncio.run(run())


def test_managed_catalog_uses_votes_and_does_not_treat_unknown_as_unrated():
    async def run():
        fleet = Mock()
        fleet.request.return_value = {'mode': 'managed-read-only', 'at': time.time(), 'items': [
            {'id': 'obsolete-stars', 'rating_average': 5, 'rating_count': 100},
            {'id': 'rated', 'upvote_count': 5, 'downvote_count': 1, 'vote_count': 6},
            {'id': 'better', 'upvote_count': 5, 'downvote_count': 0, 'vote_count': 5},
            {'id': 'unrated', 'vote_count': 0},
            {'id': 'withdrawn', 'status': 'withdrawn', 'upvote_count': 100},
        ]}
        client = ManagedForgeClient(fleet)
        rated, _, _ = await client.listings(sort='rated')
        assert [x['id'] for x in rated[:2]] == ['better', 'rated']
        assert all(x['id'] != 'withdrawn' for x in rated)
        assert (await client.listing('withdrawn'))['status'] == 'withdrawn'
        unrated, _, _ = await client.listings(sort='unrated')
        assert [x['id'] for x in unrated] == ['unrated']
        fleet.request.assert_called_once_with('/api/forge')
    asyncio.run(run())


def test_managed_favourites_are_read_only_timed_and_explicit_when_unavailable():
    async def run():
        fleet = Mock()
        fleet.request.return_value = {'mode': 'managed-read-only', 'at': time.time(), 'items': []}
        client = ManagedForgeClient(fleet)
        with pytest.raises(ForgeError, match='not available'):
            await client.favourites()
        client.data['favourites'] = [
            {'id': 'current', 'title': 'Curated', 'listing_ids': ['map', 'withdrawn'],
             'listings': [{'id': 'map'}, {'id': 'withdrawn', 'status': 'withdrawn'}]},
            {'id': 'expired', 'ends_at': '2000-01-01T00:00:00Z'},
            {'id': 'future', 'starts_at': '2100-01-01T00:00:00Z'},
        ]
        collections, reply = await client.favourites()
        assert [c['id'] for c in collections] == ['current'] and reply['_stale'] == 0
        assert [x['id'] for x in collections[0]['listings']] == ['map']
        client.data['favourites'] = []
        assert (await client.favourites())[0] == []
        fleet.request.assert_called_once_with('/api/forge')
        fleet.submit.assert_not_called()
    asyncio.run(run())


def test_managed_rising_keeps_first_rank_ahead_of_unranked_listings():
    async def run():
        fleet = Mock()
        fleet.request.return_value = {'mode': 'managed-read-only', 'at': time.time(), 'items': [
            {'id': 'missing'}, {'id': 'second', 'rising_rank': 1},
            {'id': 'first', 'rising_rank': 0}, {'id': 'unknown', 'rising_rank': None},
        ]}
        rows, _, _ = await ManagedForgeClient(fleet).listings(sort='rising')
        assert [row['id'] for row in rows] == ['first', 'second', 'missing', 'unknown']
    asyncio.run(run())


def test_fleet_f9_favourites_and_superintendent_keep_lifecycle_and_target_safe():
    async def run():
        rows = {f'node-b-{49000+i}': dict(
            id=f'node-b-{49000+i}', node='node-b', port=49000+i, name=f'Test {i}', players=i,
            runtime_cap=32, configured_cap=32, health='healthy', fresh=True, node_fresh=True,
            running=True, rcon_up=True, image='official:0.9.9', process_generation='v1', config_hash='cfg',
        ) for i in range(18)}
        listing = {'id': 'map', 'title': 'Featured', 'upvote_count': 3, 'downvote_count': 0,
                   'versions': [{'id': 'v1', 'status': 'published', 'version_label': '1.0'}]}
        fleet = Mock(rows=rows, updated=time.monotonic())
        fleet.read = AsyncMock(return_value={'ok': True, 'data': {'players': []}})
        fleet.request.return_value = {'mode': 'managed-read-only', 'at': time.time(), 'items': [listing],
                                     'favourites': [{'id': 'curated', 'title': 'Curated',
                                                     'listing_ids': ['map'], 'listings': [listing]}]}
        app = FleetApp(fleet)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(.3)
            selected = app.cur
            await pilot.press('f9')
            app.query_one('#forge-sort', Select).value = 'favourites'
            await pilot.pause(.4)
            assert app.query_one(TabbedContent).active == 'forge'
            assert [r['id'] for r in app.listings] == ['map']
            assert app.featured == {'map': 'Curated'}
            assert app.cur is selected and app.fleet is fleet
            assert all(not app.query_one(selector).display for selector in
                       ('#fg-install', '#fg-load', '#fg-key', '#fg-ack'))
            assert app.query_one('#sidebar').region.contains_region(app.query_one('#super').region)
            assert app.query_one('#uplink').region.bottom <= app.query_one('#sidebar').region.bottom
            assert app.sup.last == 'Native event feed not connected'
            fleet.submit.assert_not_called()
    asyncio.run(run())
