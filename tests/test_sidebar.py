"""The server list owns its scroll; navigation must not move the status footer."""
import asyncio

import pytest
from textual.widgets import Input, ListView, Select

from oni_rcon.config import Server
from oni_rcon.demo import serve_fakes
from oni_rcon.fleet import FleetApp
from oni_rcon.tenant import TenantApp
from test_terminal_performance import FixtureFleet


def footer(app):
    side, listing, face, uplink = (app.query_one(s) for s in
                                  ('#sidebar', '#stations', '#super', '#uplink'))
    assert side.region.contains_region(face.region)
    assert side.region.contains_region(uplink.region)
    assert face.region.height >= app.sup_rows()
    assert listing.region.height >= 2
    assert listing.region.bottom <= face.region.y
    assert uplink.region.bottom == side.region.bottom
    assert side.scroll_y == 0
    return face.region, uplink.region


@pytest.mark.parametrize('size', [(80, 24), (120, 30), (160, 48)])
def test_fleet_scroll_filter_sort_and_growth_leave_footer_anchored(size):
    async def run():
        fleet = FixtureFleet()
        base = next(iter(fleet.rows.values()))
        fleet.rows = {f'node-b-{49175+i}': dict(base, id=f'node-b-{49175+i}', port=49175+i,
                      name=f'Lobby {i:03}', players=i % 32) for i in range(200)}
        fleet.rows['node-b-49374']['name'] = 'Unique test lobby'
        app = FleetApp(fleet)
        async with app.run_test(size=size) as pilot:
            await pilot.pause(.3)
            before = footer(app)
            listing = app.query_one('#stations', ListView)
            listing.focus()
            selected = listing.children[-1].station
            listing.index = len(listing.children) - 1
            # Wait for the requested selection before testing that sorting preserves it.
            async with asyncio.timeout(10):
                while app.cur is not selected:
                    await pilot.pause(.05)
            await pilot.pause(.2)
            assert listing.scroll_y > 0
            assert footer(app) == before
            app.query_one('#fleet-sort', Select).value = 'name'
            await pilot.pause(.1)
            app.query_one('#fleet-search', Input).value = 'Unique test lobby'
            await pilot.pause(.1)
            assert len(app.visible_ids) == 1
            assert footer(app) == before
            assert app.cur is selected
            app.query_one('#fleet-search', Input).value = 'no-such-server'
            await pilot.pause(.1)
            assert not app.visible_ids
            assert footer(app) == before
            app.reset_browser()
            fleet.rows['node-b-49500'] = dict(base, id='node-b-49500', port=49500, name='New dynamic')
            await app.refresh_inventory()
            await pilot.pause(.2)
            assert len(app.stations) == 201
            assert app.cur is selected
            for tab in ('f3', 'f5', 'f9', 'f1'):
                await pilot.press(tab)
                assert footer(app) == before
    asyncio.run(run())


@pytest.mark.parametrize('size', [(80, 24), (120, 30), (160, 48)])
def test_customer_controls_and_last_server_cannot_push_footer_offscreen(size):
    async def run():
        ports, keep = await serve_fakes(tick=False, specs=[(f'Lobby {i}', 16, 2) for i in range(24)])
        app = TenantApp([Server(port=p, password='demo') for p in ports], by='IBIGS Admin', intro=False)
        async with app.run_test(size=size) as pilot:
            await pilot.pause(.3)
            before = footer(app)
            listing = app.query_one('#stations', ListView)
            listing.focus()
            listing.index = len(listing.children) - 1
            await pilot.pause(.2)
            assert listing.scroll_y > 0
            assert footer(app) == before
            for selector in ('#power-btn', '#jobs-btn', '#updates-btn', '#add-server'):
                button = app.query_one(selector)
                button.focus()
                await pilot.pause(.05)
                assert app.query_one('#sidebar').region.contains_region(button.region)
                assert button.region.bottom <= app.query_one('#super').region.y
                assert footer(app) == before
    asyncio.run(run())
