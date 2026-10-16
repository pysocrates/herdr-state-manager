import json
import os
import sqlite3
import time
from pathlib import Path
import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from .client import Client, ClientError

app = typer.Typer(invoke_without_command=True, help='Herdr State Manager — persistent state, no orchestration.')
console = Console()


def fetch(path, params=None):
    try:
        return Client().request(path, params)
    except ClientError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1)


def render(result, as_json=False):
    if as_json:
        typer.echo(json.dumps(result, sort_keys=True))
        return
    data = result['data']
    if isinstance(data, dict) and 'active_tasks' in data:
        console.print(f"[bold]HSM[/bold]  project={data['project'] or 'all authorized'}  revision={data['revision']}")
        console.print(f"Tasks: {len(data['active_tasks'])}  Agents: {len(data['agent_sessions'])}  Panes: {len(data['active_panes'])}  Pending approvals: {len(data['pending_approvals'])}")
        if 'task_hierarchy' in data:
            render_task_tree(data['task_hierarchy'])
        else:
            render({'data': data['active_tasks']})
        for warning in data['warnings']:
            console.print(warning, style='yellow', markup=False)
    elif isinstance(data, list):
        data = [{**row, 'subtasks': f"{row['descendant_summary']['completed']}/{row['descendant_summary']['total']}"} if 'descendant_summary' in row else row for row in data]
        table = Table()
        fields = [k for k in ('id', 'project_id', 'task_key', 'title', 'parent_key', 'state', 'owner_profile', 'subtasks', 'profile', 'external_pane_id', 'runtime_status', 'event_type', 'actor') if any(k in row for row in data)]
        for field in fields:
            table.add_column(field)
        for row in data:
            table.add_row(*(Text(str(row.get(k, ''))) for k in fields))
        console.print(table if data else 'No records.')
        if result.get('pagination', {}).get('has_more'):
            console.print('More records available; use --offset and --limit.', style='yellow')
    else:
        console.print_json(data=data)


@app.callback()
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        render(fetch('/state'))


@app.command()
def status(project: str | None = None, json: bool = False):
    """Recover current authorized execution state."""
    render(fetch('/state', {'project': project}), json)


def render_task_tree(data):
    tree = Tree('Tasks')
    branches = {}
    for node in data['nodes']:
        counts = node['descendant_summary']
        label = Text(f"{node['project_id']}/{node['task_key']}  {node['title']}  [{node['state']}]")
        label.append(f"  owner={node.get('owner_profile') or 'unassigned'}", style='cyan')
        if counts['total']:
            label.append(f"  {counts['completed']}/{counts['total']} descendants completed", style='green')
            for state in ('blocked', 'failed', 'cancelled', 'awaiting_approval'):
                if counts[state]:
                    label.append(f"  {state}={counts[state]}", style='yellow')
        parent = branches.get(node['parent_id']) if node['depth'] else None
        branches[node['id']] = (parent or tree).add(label)
    console.print(tree if data['nodes'] else 'No tasks.')


@app.command()
def tasks(project: str | None = None, state: str | None = None, json: bool = False,
          limit: int = 50, offset: int = 0, tree: bool = False, parent: str | None = None):
    """List tasks, filter direct children, or show the complete authorized tree."""
    if tree:
        if state is not None or parent is not None or offset != 0 or limit != 50:
            raise typer.BadParameter('--tree uses the complete hierarchy; use --state/--parent/pagination with the flat list')
        result = fetch('/tasks/tree', {'project': project})
        if json:
            render(result, True)
        else:
            render_task_tree(result['data'])
        return
    parent_id = ('none' if parent == 'none' else resolve_task(parent, project)['id']) if parent else None
    render(fetch('/tasks', {'project': project, 'state': state, 'limit': limit, 'offset': offset, 'parent_id': parent_id}), json)


def resolve_task(task_id, project=None):
    """Resolve a UUID or project-local human key through authenticated REST."""
    from uuid import UUID
    from urllib.parse import quote
    try:
        UUID(task_id)
        result = fetch('/tasks/' + quote(task_id, safe=''))
    except ValueError:
        if not project:
            typer.echo('Human task identifiers require --project.', err=True)
            raise typer.Exit(2)
        offset = 0
        while True:
            page = fetch('/tasks', {'project': project, 'offset': offset, 'limit': 200})
            found = next((t for t in page['data'] if t['task_key'] == task_id), None)
            if found:
                result = {'data': found}
                break
            if not page['pagination']['has_more']:
                typer.echo('Task not found.', err=True)
                raise typer.Exit(1)
            offset += 200
    if project is not None and result['data']['project_id'] != project:
        raise typer.BadParameter('Task does not belong to --project')
    return result['data']


@app.command()
def task(task_id: str, project: str | None = None, json: bool = False, tree: bool = False):
    """Inspect a task or its complete subtree. Human keys require --project."""
    item = resolve_task(task_id, project)
    if tree:
        result = fetch('/tasks/' + item['id'] + '/tree')
        if json:
            render(result, True)
        else:
            render_task_tree(result['data'])
    else:
        render({'data': item}, json)


@app.command()
def subtask(parent: str, key: str = typer.Option(...), title: str = typer.Option(...),
            objective: str = typer.Option(...), idempotency_key: str = typer.Option(...),
            project: str | None = None, owner_session: str | None = None, json: bool = False):
    """Create a queued child of a confirmed parent. Does not spawn or transition agents."""
    item = resolve_task(parent, project)
    payload = {'project_id': item['project_id'], 'parent_id': item['id'], 'task_key': key,
               'title': title, 'objective': objective}
    if owner_session:
        payload['owner_session_id'] = owner_session
    try:
        result = Client().request('/tasks', method='POST', data=payload, idempotency_key=idempotency_key)
    except ClientError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1)
    verified = fetch('/tasks/' + result['data']['id'])
    render(verified, json)


@app.command()
def agents(project: str | None = None, json: bool = False, limit: int = 50, offset: int = 0):
    render(fetch('/agent-sessions', {'project': project, 'limit': limit, 'offset': offset}), json)


@app.command()
def panes(project: str | None = None, json: bool = False, limit: int = 50, offset: int = 0):
    render(fetch('/panes', {'project': project, 'limit': limit, 'offset': offset}), json)


@app.command()
def events(project: str | None = None, json: bool = False, follow: bool = False, limit: int = 50, offset: int = 0):
    """Inspect events; follow uses the durable cursor, oldest unseen first."""
    seen = 0
    try:
        while True:
            result = fetch('/events', {'project': project, 'limit': limit, 'offset': offset})
            if not follow:
                render(result, json)
                return
            unseen = [e for e in result['data'] if e['cursor'] > seen]
            page_offset = offset + limit
            while result['pagination']['has_more'] and result['data'][-1]['cursor'] > seen:
                result = fetch('/events', {'project': project, 'limit': limit, 'offset': page_offset})
                unseen.extend(e for e in result['data'] if e['cursor'] > seen)
                page_offset += limit
            for event in sorted(unseen, key=lambda e: e['cursor']):
                render({'data': event}, json)
                seen = max(seen, event['cursor'])
            time.sleep(2)
    except KeyboardInterrupt:
        return


@app.command()
def health(json: bool = False):
    render(fetch('/health/ready'), json)


@app.command('init')
def initialize():
    """Apply schema migrations; does not erase an existing database."""
    from .config import Config
    from .database import migrate
    os.umask(0o077)
    cfg = Config()
    cfg.validate()
    migrate(cfg.database_url)
    typer.echo('Database schema is current. Provision credentials separately.')


@app.command()
def serve():
    """Development server. Production uses Gunicorn."""
    from .app import create_app
    create_app().run(host=os.getenv('HSM_HOST', '127.0.0.1'), port=int(os.getenv('HSM_PORT', '8787')))


@app.command()
def backup(destination: Path):
    """Administrative online SQLite backup; refuses overwrite."""
    from .config import Config
    from sqlalchemy.engine import make_url
    cfg = Config()
    cfg.validate()
    path = Path(make_url(cfg.database_url).database)
    if not path.is_file():
        raise typer.BadParameter('Source database does not exist')
    try:
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        raise typer.BadParameter('Backup destination already exists')
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    typer.echo(f'Backup created: {destination}')


@app.command()
def reconcile(json: bool = False):
    """Administrative observational reconciliation (same filesystem trust as init)."""
    from .config import Config
    from .database import engine_for
    from .reconciliation import reconcile as perform
    from .services import Problem
    cfg = Config()
    cfg.validate()
    engine = engine_for(cfg.database_url)
    try:
        render({'data': perform(engine, cfg)}, json)
    except Problem as error:
        typer.echo(error.message, err=True)
        raise typer.Exit(1)
    finally:
        engine.dispose()


@app.command()
def approvals(project: str | None = None, json: bool = False, limit: int = 50, offset: int = 0):
    render(fetch('/approvals', {'project': project, 'limit': limit, 'offset': offset}), json)


@app.command()
def approval(approval_id: str, json: bool = False):
    from urllib.parse import quote
    render(fetch('/approvals/' + quote(approval_id, safe='')), json)


def decide(approval_id, decision, note):
    from urllib.parse import quote
    path = '/approvals/' + quote(approval_id, safe='')
    item = fetch(path)['data']
    # Full exact operation is shown before any confirmation or mutation.
    render({'data': item})
    if item['effective_status'] != 'pending':
        typer.echo('Approval is no longer pending.', err=True)
        raise typer.Exit(1)
    if not typer.confirm(f"{decision.capitalize()} this exact operation?", default=False):
        raise typer.Abort()
    try:
        render(Client().request(path + '/resolve', method='POST',
                               data={'expected_version': item['version'], 'decision': decision, 'note': note}))
    except ClientError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1)


@app.command()
def approve(approval_id: str, note: str | None = None):
    """Review and explicitly confirm; never consumes authorization."""
    decide(approval_id, 'approved', note)


@app.command()
def reject(approval_id: str, note: str | None = None):
    decide(approval_id, 'rejected', note)


@app.command()
def watch(project: str | None = None):
    """Interactive cached-state view. q exits, r refreshes, Enter inspects."""
    from .tui import Watch
    try:
        Watch(project=project).run()
    except ClientError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1)


@app.command('mcp')
def mcp_server():
    """Optional stdio interface using HSM_API_URL and HSM_TOKEN."""
    try:
        from .mcp.server import main
    except ImportError:
        typer.echo('Install herdr-state-manager[mcp] to use MCP.', err=True)
        raise typer.Exit(1)
    main()
