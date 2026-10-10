import asyncio
import json
import stat

import pytest
from textual.widgets import Button, Input, Select

from oni_rcon.app import Confirm
from oni_rcon.config import Server
from oni_rcon.jobs import Store
from oni_rcon.jobs_ui import JobForm, JobsPanel, UpdatesPanel
from oni_rcon.tenant import TenantApp


async def until(pilot, predicate):
    for _ in range(80):
        await pilot.pause(.03)
        if predicate():
            return
    assert predicate()


@pytest.mark.parametrize('size', [(80,32),(160,48)])
def test_customer_can_save_pause_and_delete_a_job_without_shell(size,tmp_path,monkeypatch):
    store=Store(tmp_path/'jobs')
    monkeypatch.setattr('oni_rcon.jobs_ui.Store',lambda:store)
    tool=tmp_path/'oni'
    tool.write_text('#!/bin/sh\nprintf \'%s\\n\' \'[{"service":"h3-01"}]\'\n')
    tool.chmod(0o755)
    monkeypatch.setenv('IBIGS_ONI_POWER',str(tool))

    async def go():
        app=TenantApp([Server(name='h3-01',port=1,password='demo')],by='test',intro=False)
        async with app.run_test(size=size) as pilot:
            await pilot.press('f8')
            await until(pilot,lambda:isinstance(app.screen,JobsPanel))
            for button in app.screen.query(Button):
                assert button.region.intersection(app.screen.region)==button.region
            await pilot.click('#job-new')
            await until(pilot,lambda:isinstance(app.screen,JobForm))
            app.screen.query_one('#field-name',Input).value='Welcome message'
            await pilot.click('#yes')
            await until(pilot,lambda:bool(app.screen.query('#field-message')))
            app.screen.query_one('#field-message',Input).value='Welcome to the lobby!'
            await pilot.click('#yes')
            await until(pilot,lambda:isinstance(app.screen,Confirm))
            assert 'This schedule remains active after you close oni.' in app.screen.b
            await pilot.click('#yes')
            await until(pilot,lambda:isinstance(app.screen,JobsPanel))
            saved=store.load()
            assert len(saved)==1 and saved[0]['message']=='Welcome to the lobby!'
            assert saved[0]['trigger']=='interval' and saved[0]['schedule']=='30'
            await pilot.click('#job-toggle')
            await until(pilot,lambda:isinstance(app.screen,JobsPanel) and not store.load()[0]['enabled'])
            await pilot.click('#job-delete')
            await until(pilot,lambda:isinstance(app.screen,Confirm))
            await pilot.click('#yes')
            await until(pilot,lambda:isinstance(app.screen,JobsPanel) and store.load()==[])
    asyncio.run(go())


def test_updates_require_opt_in_and_save_customer_schedule(tmp_path,monkeypatch):
    store=Store(tmp_path/'jobs')
    monkeypatch.setattr('oni_rcon.jobs_ui.Store',lambda:store)
    async def go():
        app=TenantApp([Server(port=1,password='demo')],by='test',intro=False)
        async with app.run_test(size=(100,40)) as pilot:
            await pilot.press('f9')
            await until(pilot,lambda:isinstance(app.screen,UpdatesPanel))
            assert store.load()==[]
            app.screen.query_one('#update-choice',Select).value='game-auto'
            await pilot.click('#update-go')
            await until(pilot,lambda:isinstance(app.screen,Confirm))
            await pilot.click('#yes')
            await until(pilot,lambda:len(store.load())==1)
            j=store.load()[0]
            assert j['id']=='auto-game' and j['enabled'] and j['action']=='update-game'
    asyncio.run(go())
