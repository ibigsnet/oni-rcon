"""Customer console: direct RCON for the lobbies discovered inside this customer's VM."""
from textual.binding import Binding
from .app import OniApp


class TenantApp(OniApp):
    TITLE = 'IBIGS Server Console'
    BINDINGS = [*OniApp.BINDINGS, Binding('ctrl+f', 'goto', 'Find server', priority=True)]

    def welcome(self):
        super().welcome()
        self.notify('Managing this VM’s lobbies. Ctrl+F finds a server; F6 opens ReclaimerForge. '
                    'Use your own Forge key. Installing content never restarts a game.', title='IBIGS', timeout=8)
