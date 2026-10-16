"""Read-only, REST-backed operator display. No runtime polling or SQLite access."""
import asyncio
import json
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Footer, Header, Input, Select, Static, TabbedContent, TabPane
from .client import Client, ClientError


class Watch(App):
    APPROVAL_PAGE_SIZE = 50
    TITLE = 'HSM — cached operational state'
    BINDINGS = [('q', 'quit', 'Quit'), ('r', 'reload', 'Refresh')]
    CSS = '''
    #summary { height: 3; }
    #project { height: 3; }
    #approval-pagination { height: 3; }
    #approval-page { height: 2; }
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
        self.approval_offset = 0
        self.approval_has_more = False
        self.approval_project = project
        self.approval_source = 'hsm'

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static('Connecting to HSM…', id='summary', markup=False)
        yield Input(value=self.project or '', placeholder='Project filter (empty = all authorized), Enter to apply', id='project')
        with TabbedContent():
            for label, ident in [('Tasks', 'tasks'), ('Agents', 'agents'), ('Panes', 'panes'),
                                 ('Approvals', 'approvals'), ('Events', 'events'), ('Incidents', 'incidents'), ('Adapters', 'adapters')]:
                with TabPane(label):
                    if ident == 'approvals':
                        yield Select([('HSM authorization grants', 'hsm'), ('Native Hermes decisions (observed only)', 'native')], value='hsm', allow_blank=False, id='approval-source')
                        yield Static('Approval history not loaded.', id='approval-page', markup=False)
                        with Horizontal(id='approval-pagination'):
                            yield Button('Previous page', id='approval-previous', disabled=True)
                            yield Button('Next page', id='approval-next', disabled=True)
                    yield DataTable(id=ident, cursor_type='row')
        with VerticalScroll(id='details-scroll'):
            yield Static('Select a row and press Enter for exact details. Runtime observations are cached.', id='details', markup=False)
        yield Footer()

    async def on_mount(self):
        await self.refresh_state()
        self.set_interval(5, self.refresh_state)

    async def refresh_state(self, approval_offset=None):
        if self.loading:
            return
        self.loading = True
        project = self.project
        source = self.approval_source
        offset = self.approval_offset if approval_offset is None else approval_offset
        if project != self.approval_project:
            offset = 0
        try:
            result = await asyncio.to_thread(self.client.request, '/state', {'project': project})
            # One bounded page per refresh, never a crawl of the full history.
            approvals = await asyncio.to_thread(self.client.request, '/approvals' if source == 'hsm' else '/approval-observations', {
                'project': project, 'limit': self.APPROVAL_PAGE_SIZE, 'offset': offset})
            if source == 'hsm':
                approval_rows = [{**row, 'outcome': 'consumed' if row.get('consumed_at')
                                  else row['effective_status']} for row in approvals['data']]
                approval_columns = ['project_id', 'operation', 'target', 'outcome', 'status', 'expires_at']
            else:
                approval_rows = [{**row, 'outcome': row.get('choice') or
                                  ('unpaired request' if row.get('correlation') == 'missing_request_id' else 'pending')}
                                 for row in approvals['data']]
                approval_columns = ['project_id', 'surface', 'outcome', 'correlation', 'requested_at', 'responded_at']
            has_more = approvals['pagination']['has_more']
            if project != self.project or source != self.approval_source:
                return  # Do not overwrite a changed filter with an in-flight result.
            self.snapshot = data = result['data']
            self.query_one('#summary', Static).update(Text(
                f"Project: {data['project'] or 'all authorized'} | event cursor: {data['revision']} | snapshot: {data['generated_at']}\n"
                + ('; '.join(data['warnings']) or 'Displaying persisted observations; inspect timestamps for freshness.')))
            task_nodes = data.get('task_hierarchy', {}).get('nodes', data['active_tasks'])
            task_rows = []
            for node in task_nodes:
                counts = node.get('descendant_summary', {})
                task_rows.append({**node, 'task_tree': '  ' * node.get('depth', 0) + node['task_key'],
                                  'progress': f"{counts.get('completed', 0)}/{counts.get('total', 0)}" if counts.get('total') else '',
                                  'owner': node.get('owner_profile') or node.get('owner_session_id') or 'unassigned'})
            groups = {
                'tasks': (task_rows, ['project_id', 'task_tree', 'title', 'parent_key', 'state', 'owner', 'progress', 'version']),
                'agents': (data['agent_sessions'], ['project_id', 'profile', 'runtime_status', 'last_observed_at']),
                'panes': (data['active_panes'], ['project_id', 'external_pane_id', 'runtime_status', 'task_id', 'last_observed_at']),
                'approvals': (approval_rows, approval_columns),
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
            self.approval_offset = offset
            self.approval_has_more = has_more
            self.approval_project = project
            self.query_one('#approval-page', Static).update(Text(
                f"Persisted {source} approval history | Project: {project or 'all authorized'} | "
                f"offset {offset}, showing {len(approval_rows)} (page limit {self.APPROVAL_PAGE_SIZE}) | "
                + ('More records available — Next page.' if has_more else 'End of history.')))
            self.query_one('#approval-previous', Button).disabled = offset == 0
            self.query_one('#approval-next', Button).disabled = not has_more
        except (ClientError, KeyError, TypeError) as error:
            self.query_one('#summary', Static).update(Text(f'HSM unavailable: {error}. Last successful data remains visible. r refresh; q quit.'))
        finally:
            self.loading = False

    async def action_reload(self):
        await self.refresh_state()

    async def on_input_submitted(self, event: Input.Submitted):
        self.project = event.value.strip() or None
        await self.refresh_state()

    async def on_select_changed(self, event: Select.Changed):
        if event.select.id == "approval-source" and event.value != self.approval_source:
            self.approval_source = str(event.value)
            self.approval_offset = 0
            await self.refresh_state()

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == 'approval-next' and self.approval_has_more:
            await self.refresh_state(self.approval_offset + self.APPROVAL_PAGE_SIZE)
        elif event.button.id == 'approval-previous' and self.approval_offset:
            await self.refresh_state(max(0, self.approval_offset - self.APPROVAL_PAGE_SIZE))

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        record = self.records.get(event.data_table.id, {}).get(event.row_key.value)
        if record:
            self.query_one('#details', Static).update(Text(json.dumps(record, indent=2, sort_keys=True)))
