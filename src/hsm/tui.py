"""Read-only, REST-backed operator display. No runtime polling or SQLite access."""
import asyncio
import json
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Footer, Header, Input, Static, TabbedContent, TabPane
from .client import Client, ClientError


class Watch(App):
    TITLE = 'HSM — cached operational state'
    BINDINGS = [('q', 'quit', 'Quit'), ('r', 'reload', 'Refresh')]
    CSS = '''
    #summary { height: 3; }
    #project { height: 3; }
    TabbedContent { height: 1fr; }
    DataTable { height: 1fr; }
    #details-scroll { height: 10; border-top: solid $primary; }
    '''

    def __init__(self, client=None, project=None):
        super().__init__()
        self.client = client or Client()
        self.project = project
        self.snapshot = None
        self.records = {}
        self.loading = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static('Connecting to HSM…', id='summary', markup=False)
        yield Input(value=self.project or '', placeholder='Project filter (empty = all authorized), Enter to apply', id='project')
        with TabbedContent():
            for label, ident in [('Tasks', 'tasks'), ('Agents', 'agents'), ('Panes', 'panes'),
                                 ('Approvals', 'approvals'), ('Events', 'events'), ('Incidents', 'incidents'), ('Adapters', 'adapters')]:
                with TabPane(label):
                    yield DataTable(id=ident, cursor_type='row')
        with VerticalScroll(id='details-scroll'):
            yield Static('Select a row and press Enter for exact details. Runtime observations are cached.', id='details', markup=False)
        yield Footer()

    async def on_mount(self):
        await self.refresh_state()
        self.set_interval(5, self.refresh_state)

    async def refresh_state(self):
        if self.loading:
            return
        self.loading = True
        try:
            result = await asyncio.to_thread(self.client.request, '/state', {'project': self.project})
            self.snapshot = data = result['data']
            self.query_one('#summary', Static).update(Text(
                f"Project: {data['project'] or 'all authorized'} | event cursor: {data['revision']} | snapshot: {data['generated_at']}\n"
                + ('; '.join(data['warnings']) or 'Displaying persisted observations; inspect timestamps for freshness.')))
            groups = {
                'tasks': (data['active_tasks'], ['project_id', 'task_key', 'title', 'state', 'owner_session_id', 'version']),
                'agents': (data['agent_sessions'], ['project_id', 'profile', 'runtime_status', 'last_observed_at']),
                'panes': (data['active_panes'], ['project_id', 'external_pane_id', 'runtime_status', 'task_id', 'last_observed_at']),
                'approvals': (data.get('outstanding_approvals', data['pending_approvals']), ['project_id', 'operation', 'target', 'status', 'expires_at']),
                'events': (data['recent_events'], ['cursor', 'timestamp', 'event_type', 'actor']),
                'incidents': (data['unresolved_incidents'], ['project_id', 'incident_type', 'resource_id', 'last_seen_at']),
                'adapters': (data.get('adapter_health', []), ['project_id', 'provider', 'status', 'observed_at']),
            }
            for name, (rows, columns) in groups.items():
                table = self.query_one('#' + name, DataTable)
                position = table.cursor_row
                table.clear(columns=True)
                table.add_columns(*columns)
                for row in rows:
                    table.add_row(*(Text(str(row.get(c, ''))) for c in columns), key=row['id'])
                if rows:
                    table.move_cursor(row=min(position, len(rows) - 1))
                self.records[name] = {r['id']: r for r in rows}
        except (ClientError, KeyError, TypeError) as error:
            self.query_one('#summary', Static).update(Text(f'HSM unavailable: {error}. Last successful data remains visible. r refresh; q quit.'))
        finally:
            self.loading = False

    async def action_reload(self):
        await self.refresh_state()

    async def on_input_submitted(self, event: Input.Submitted):
        self.project = event.value.strip() or None
        await self.refresh_state()

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        record = self.records.get(event.data_table.id, {}).get(event.row_key.value)
        if record:
            self.query_one('#details', Static).update(Text(json.dumps(record, indent=2, sort_keys=True)))
