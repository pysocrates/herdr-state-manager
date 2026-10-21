"""REST-backed operator display. Confirmed governance only; no process control."""
import asyncio
import json
from uuid import uuid4
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Select, Static, TabbedContent, TabPane
from .client import Client, ClientError


class Intervention(ModalScreen):
    """Pin the displayed task/version and operation until explicit confirmation."""
    BINDINGS = [('escape', 'cancel', 'Cancel')]
    DEFAULT_CSS = '''
    Intervention { align: center middle; }
    #intervention { width: 76; height: auto; padding: 1 2; border: solid $primary; background: $surface; }
    #intervention Horizontal { height: 3; }
    '''

    def __init__(self, task, operation):
        super().__init__()
        self.task_record = task
        self.operation = operation
        self.key = str(uuid4())

    def compose(self):
        with VerticalScroll(id='intervention'):
            yield Static(f"{self.operation.upper()} {self.task_record['project_id']}/{self.task_record['task_key']}\n"
                         f"Task: {self.task_record['id']} | expected version: {self.task_record['version']}\n"
                         f"Idempotency key: {self.key}\n"
                         "Runtime control unsupported/unacknowledged: no processes stop or start.\n"
                         "Resume clears only the local pause; ancestor policy still applies.", markup=False)
            yield Input(placeholder='Required reason (recorded in audit)', id='intervention-reason')
            yield Static('Enter a reason, then explicitly confirm.', id='intervention-error', markup=False)
            with Horizontal():
                yield Button('Cancel', id='intervention-cancel')
                yield Button('Confirm intervention', id='intervention-confirm', variant='warning')

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id == 'intervention-cancel':
            self.dismiss(None)
        elif event.button.id == 'intervention-confirm':
            reason = self.query_one('#intervention-reason', Input).value.strip()
            if not reason:
                self.query_one('#intervention-error', Static).update('A nonblank reason is required.')
                return
            self.dismiss({'task_id': self.task_record['id'], 'operation': self.operation,
                          'expected_version': self.task_record['version'], 'reason': reason,
                          'idempotency_key': self.key})


class Watch(App):
    APPROVAL_PAGE_SIZE = 50
    TITLE = 'HSM — cached operational state'
    BINDINGS = [('q', 'quit', 'Quit'), ('r', 'reload', 'Refresh'),
                ('space', 'toggle_branch', 'Expand/collapse')]
    CSS = '''
    #summary { height: 3; }
    #project { height: 3; }
    #approval-pagination { height: 3; }
    #approval-page { height: 2; }
    #task-actions { height: 3; }
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
        self.collapsed = set()
        self.task_record_nodes = []
        self.intervening = False
        self.detail_generation = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static('Connecting to HSM…', id='summary', markup=False)
        yield Input(value=self.project or '', placeholder='Project filter (empty = all authorized), Enter to apply', id='project')
        with TabbedContent():
            for label, ident in [('Tasks', 'tasks'), ('Agents', 'agents'), ('Panes', 'panes'),
                                 ('Approvals', 'approvals'), ('Events', 'events'), ('Incidents', 'incidents'), ('Adapters', 'adapters')]:
                with TabPane(label):
                    if ident == 'tasks':
                        with Horizontal(id='task-actions'):
                            yield Button('Expand/collapse', id='task-toggle')
                            yield Button('Scope', id='task-scope')
                            yield Button('Pause', id='task-pause')
                            yield Button('Resume', id='task-resume')
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
        if self.loading or self.intervening:
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
            if self.intervening or project != self.project or source != self.approval_source:
                return  # Do not overwrite a changed filter with an in-flight result.
            self.snapshot = data = result['data']
            self.query_one('#summary', Static).update(Text(
                f"Project: {data['project'] or 'all authorized'} | event cursor: {data['revision']} | snapshot: {data['generated_at']}\n"
                + ('; '.join(data['warnings']) or 'Displaying persisted observations; inspect timestamps for freshness.')))
            self.task_record_nodes = data.get('task_hierarchy', {}).get('nodes', data['active_tasks'])
            self.render_tasks()
            groups = {
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
            if not self.intervening:
                self.query_one('#summary', Static).update(Text(f'HSM unavailable: {error}. Last successful data remains visible. r refresh; q quit.'))
        finally:
            self.loading = False

    async def action_reload(self):
        await self.refresh_state()

    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id != 'project':
            return
        self.detail_generation += 1
        self.project = event.value.strip() or None
        await self.refresh_state()

    async def on_select_changed(self, event: Select.Changed):
        if event.select.id == "approval-source" and event.value != self.approval_source:
            self.approval_source = str(event.value)
            self.approval_offset = 0
            await self.refresh_state()

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == 'task-toggle':
            self.action_toggle_branch()
        elif event.button.id == 'task-scope':
            await self.show_scope()
        elif event.button.id in {'task-pause', 'task-resume'}:
            await self.prepare_intervention(event.button.id.removeprefix('task-'))
        elif event.button.id == 'approval-next' and self.approval_has_more:
            await self.refresh_state(self.approval_offset + self.APPROVAL_PAGE_SIZE)
        elif event.button.id == 'approval-previous' and self.approval_offset:
            await self.refresh_state(max(0, self.approval_offset - self.APPROVAL_PAGE_SIZE))

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        self.detail_generation += 1
        record = self.records.get(event.data_table.id, {}).get(event.row_key.value)
        if record:
            self.query_one('#details', Static).update(Text(json.dumps(record, indent=2, sort_keys=True)))

    def selected_task(self):
        table = self.query_one('#tasks', DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return self.records.get('tasks', {}).get(key)

    def render_tasks(self, selected=None):
        table = self.query_one('#tasks', DataTable)
        old = self.selected_task()
        selected = selected or (old['id'] if old else None)
        nodes = {n['id']: n for n in self.task_record_nodes}
        rows = []
        for node in self.task_record_nodes:
            parent, seen, hidden = node.get('parent_id'), set(), False
            while parent in nodes and parent not in seen:
                seen.add(parent)
                if parent in self.collapsed:
                    hidden = True
                    break
                parent = nodes[parent].get('parent_id')
            if hidden:
                continue
            counts = node.get('descendant_summary', {})
            gov = node.get('governance') or {}
            remaining = gov.get('remaining_children')
            rows.append({**node, 'task_tree': '  ' * node.get('depth', 0) + node['task_key'],
                         'progress': f"{counts.get('completed', 0)}/{counts.get('total', 0)}" if counts.get('total') else '',
                         'owner': node.get('owner_profile') or node.get('owner_session_id') or 'unassigned',
                         'branch': 'collapsed' if node['id'] in self.collapsed else 'expanded',
                         'pause': ('local' if gov.get('paused') else 'inherited' if gov.get('effective_paused') else 'no') if gov else 'unknown',
                         'children_left': ('unlimited' if remaining is None else remaining) if 'remaining_children' in gov else 'unknown',
                         'deliverables': f"{gov['ready_deliverables']}/{gov['required_deliverables']}" if 'ready_deliverables' in gov and 'required_deliverables' in gov else 'unknown',
                         'exceptions': gov.get('unresolved_exceptions', 'unknown')})
        columns = ['project_id', 'task_tree', 'title', 'parent_key', 'state', 'owner', 'progress',
                   'version', 'branch', 'pause', 'children_left', 'deliverables', 'exceptions']
        table.clear(columns=True)
        table.add_columns(*columns)
        for row in rows:
            table.add_row(*(Text(str(row.get(c, ''))) for c in columns), key=row['id'])
        self.records['tasks'] = {r['id']: r for r in rows}
        if rows:
            table.move_cursor(row=next((i for i, row in enumerate(rows) if row['id'] == selected), 0))

    def action_toggle_branch(self):
        # Space belongs to text inputs and other tables, not to a global tree shortcut.
        if not (self.query_one('#tasks', DataTable).has_focus or
                getattr(self.focused, 'id', None) == 'task-toggle'):
            return
        item = self.selected_task()
        if item and any(n.get('parent_id') == item['id'] for n in self.task_record_nodes):
            self.collapsed.symmetric_difference_update({item['id']})
            self.render_tasks(item['id'])

    async def show_scope(self):
        item = self.selected_task()
        if not item:
            return
        self.detail_generation += 1
        generation, project = self.detail_generation, self.project
        try:
            result = await asyncio.to_thread(self.client.request, '/tasks/' + item['id'] + '/scope')
            current = self.selected_task()
            if generation != self.detail_generation or project != self.project or not current or current['id'] != item['id']:
                return
            self.query_one('#details', Static).update(Text(json.dumps(result['data'], indent=2, sort_keys=True)))
        except ClientError as error:
            if generation == self.detail_generation and project == self.project:
                self.query_one('#details', Static).update(Text(f'Scope unavailable: {error}. Previous snapshot may be stale.'))

    async def prepare_intervention(self, operation):
        item = self.selected_task()
        if not item or self.intervening:
            return
        self.intervening = True
        project = self.project
        try:
            task = (await asyncio.to_thread(self.client.request, '/tasks/' + item['id']))['data']
            current = self.selected_task()
            if project != self.project or not current or current['id'] != item['id']:
                self.intervening = False
                return
            self.push_screen(Intervention(task, operation), self.finish_intervention)
        except (ClientError, KeyError, TypeError) as error:
            self.intervening = False
            self.query_one('#details', Static).update(Text(f'Cannot prepare intervention: {error}'))

    async def finish_intervention(self, request):
        if request is None:
            self.intervening = False
            return
        path = '/tasks/' + request['task_id']
        error = None
        try:
            await asyncio.to_thread(self.client.request, path + '/' + request['operation'], method='POST',
                                    data={'expected_version': request['expected_version'], 'reason': request['reason']},
                                    idempotency_key=request['idempotency_key'])
        except ClientError as exc:
            error = str(exc)
        try:
            verified = await asyncio.to_thread(self.client.request, path)
            message = ('Intervention failed/denied: ' + error if error else 'Intervention recorded; independent readback:')
            message += '\nRuntime control unsupported/unacknowledged; no processes stopped or started.\n'
            message += json.dumps(verified['data'], indent=2, sort_keys=True)
        except ClientError as exc:
            message = f'Intervention outcome unverified. Write error: {error or "none reported"}. Readback failed: {exc}. Do not blindly retry.'
        finally:
            self.intervening = False
        self.query_one('#details', Static).update(Text(message))
        await self.refresh_state()
