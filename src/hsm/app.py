import hmac
import json
import logging
import os
from datetime import datetime, timezone
from flask import Flask, g, jsonify, request
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.exceptions import HTTPException
from .config import Config
from .database import engine_for, transaction
from .models import Task, Pane, AgentSession, Event, Approval, Incident, Deliverable, DeliverableRevision, ValidationEvidence, Checkpoint, Remediation
from .schemas import ProjectCreate, TaskCreate, TaskPatch, Transition, SessionCreate, PaneCreate, RuntimePatch, Empty, ApprovalCreate, ApprovalResolve, ApprovalConsume
from .services import Service, Problem, serialize
from .responses import Envelope
from .state_machine import TRANSITIONS
from .models import ApprovalObservation
from .approval_observations import ObservationCreate, ingest


class JsonLog(logging.Formatter):
    def format(self, record):
        return json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(),
                           'level': record.levelname, 'message': record.getMessage()})


def create_app(config=None):
    config = config or Config()
    config.validate()
    app = Flask(__name__, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = 65536
    app.json.sort_keys = True
    engine = engine_for(config.database_url)
    app.extensions['hsm_engine'] = engine
    app.extensions['hsm_config'] = config
    app.logger.setLevel(os.getenv('HSM_LOG_LEVEL', 'INFO'))
    for handler in app.logger.handlers:
        handler.setFormatter(JsonLog())

    @app.before_request
    def authenticate():
        if request.path == '/api/v1/health/live' and request.method == 'GET':
            return
        authorization = request.headers.get('Authorization', '')
        candidate = authorization[7:] if authorization.startswith('Bearer ') else ''
        principal = None
        for credential in config.credentials:
            if hmac.compare_digest(candidate.encode(), credential['token'].encode()):
                principal = credential
        if not principal:
            raise Problem(401, 'unauthenticated', 'Valid bearer credential required')
        g.principal = principal

    @app.after_request
    def audit_request(response):
        # Do not log URL parameters, bodies, credentials, or raw exceptions.
        app.logger.info(json.dumps({'method': request.method,
                                    'route': str(request.url_rule) if request.url_rule else 'unmatched',
                                    'status': response.status_code}))
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if response.status_code == 401:
            response.headers['WWW-Authenticate'] = 'Bearer'
        return response

    @app.errorhandler(Problem)
    def problem(error):
        return jsonify(error={'code': error.code, 'message': error.message, 'details': []}), error.status

    @app.errorhandler(ValidationError)
    def validation(error):
        details = [{'location': list(e['loc']), 'type': e['type']} for e in error.errors()]
        return jsonify(error={'code': 'validation', 'message': 'Request validation failed', 'details': details}), 422

    @app.errorhandler(IntegrityError)
    def integrity(_):
        return problem(Problem(409, 'constraint_conflict', 'Resource conflicts with existing state'))

    @app.errorhandler(OperationalError)
    def operational(_):
        return problem(Problem(503, 'database_unavailable', 'Database unavailable, busy, or migration required'))

    @app.errorhandler(HTTPException)
    def http_error(error):
        return problem(Problem(error.code, error.name.lower().replace(' ', '_'), error.name))

    @app.errorhandler(Exception)
    def unexpected(error):
        app.logger.error('Unhandled request error: %s', type(error).__name__)
        return problem(Problem(500, 'internal_error', 'Unexpected internal error'))

    def body(schema):
        if not request.is_json:
            raise Problem(400, 'json_required', 'JSON object required')
        return schema.model_validate(request.get_json()).model_dump(exclude_unset=True)

    def run(operation, write=False):
        with transaction(engine, write) as db:
            service = Service(db, g.principal)
            result = service.guarded(lambda: operation(service)) if write else operation(service)
            if isinstance(result, tuple):
                data, status = result
            else:
                data, status = result, 200
        if status == 409 and isinstance(data, dict) and 'error' in data:
            return jsonify(data), status
        try:
            validated = Envelope.model_validate(data).model_dump(exclude_none=True)
        except ValidationError:
            raise Problem(500, 'response_validation', 'Internal response validation failed')
        return jsonify(validated), status

    def query_options(model):
        allowed = {'project', 'limit', 'offset'}
        if model is Task:
            allowed |= {'state', 'profile', 'parent_id', 'created_after', 'created_before'}
        if model in {Event, Deliverable, DeliverableRevision, ValidationEvidence, Checkpoint, Remediation, Incident}:
            allowed |= {'task_id'}
        if model in {Approval, Incident, ApprovalObservation}:
            allowed |= {'status'}
        if model is AgentSession:
            allowed |= {'profile'}
        if any(k not in allowed for k in request.args) or any(len(request.args.getlist(k)) != 1 for k in request.args):
            raise Problem(422, 'query_validation', 'Unknown or repeated query parameter')
        try:
            limit = int(request.args.get('limit', 50))
            offset = int(request.args.get('offset', 0))
            if not 1 <= limit <= 200 or offset < 0:
                raise ValueError
            filters = request.args.to_dict()
            for key in ('created_after', 'created_before'):
                if key in filters:
                    value = datetime.fromisoformat(filters[key].replace('Z', '+00:00'))
                    if value.tzinfo is None:
                        raise ValueError
                    filters[key] = value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
        except (ValueError, OverflowError):
            raise Problem(422, 'query_validation', 'Invalid pagination or UTC date filter')
        if 'parent_id' in filters and filters['parent_id'] != 'none':
            from uuid import UUID
            try:
                filters['parent_id'] = str(UUID(filters['parent_id']))
            except ValueError:
                raise Problem(422, 'query_validation', 'parent_id must be a UUID or none')
        if filters.get('state') and filters['state'] not in TRANSITIONS:
            raise Problem(422, 'query_validation', 'Unknown task state')
        return filters, limit, offset

    @app.get('/api/v1/health/live')
    def live():
        return jsonify(data={'status': 'alive'})

    @app.get('/api/v1/health')
    @app.get('/api/v1/health/ready')
    def ready():
        with transaction(engine) as db:
            revision = db.execute(text('SELECT version_num FROM alembic_version')).scalar()
            if revision != '0006':
                raise Problem(503, 'schema_not_ready', 'Schema migration required')
        return jsonify(data={'status': 'ready', 'schema_revision': revision})

    @app.route('/api/v1/approval-observations', methods=['GET', 'POST'])
    def approval_observations():
        if request.method == 'GET':
            filters, limit, offset = query_options(ApprovalObservation)
            if filters.get('status') not in {None, 'pending', 'outcome'}:
                raise Problem(422, 'query_validation', 'Unknown observation status')
            return run(lambda s: s.listing(ApprovalObservation, filters, limit, offset))
        data = body(ObservationCreate)
        return run(lambda s: {'data': ingest(s, data)}, True)

    @app.route('/api/v1/projects', methods=['GET', 'POST'])
    def projects():
        if request.method == 'GET':
            return run(lambda s: {'data': [serialize(p) for p in s.projects()]})
        data = body(ProjectCreate)
        return run(lambda s: ({'data': s.create_project(data)}, 201), True)

    @app.route('/api/v1/tasks', methods=['GET', 'POST'])
    def tasks():
        if request.method == 'GET':
            filters, limit, offset = query_options(Task)
            return run(lambda s: s.listing(Task, filters, limit, offset))
        data = body(TaskCreate)
        def create(s):
            s.project(data['project_id'], 'tasks:write')
            return s.replay('POST /tasks', request.headers.get('Idempotency-Key'), data,
                            lambda: ({'data': s.create_task(data)}, 201))
        return run(create, True)

    @app.route('/api/v1/tasks/<uuid:ident>', methods=['GET', 'PATCH'])
    def task(ident):
        ident = str(ident)
        if request.method == 'GET':
            return run(lambda s: {'data': s.task_view(s.get(Task, ident))})
        data = body(TaskPatch)
        return run(lambda s: {'data': s.patch_task(ident, data)}, True)

    @app.get('/api/v1/tasks/<uuid:ident>/scope')
    def task_scope(ident):
        from .interventions import scope
        return run(lambda s: {'data': scope(s, str(ident))})

    @app.get('/api/v1/tasks/<uuid:ident>/recovery')
    def task_recovery(ident):
        from .reporting import recovery
        if request.args:
            raise Problem(422, 'query_validation', 'Recovery accepts no query parameters')
        return run(lambda s: {'data': recovery(s, str(ident))})

    @app.get('/api/v1/governance/metrics')
    @app.get('/api/v1/tasks/<uuid:ident>/metrics')
    def governance_metrics(ident=None):
        from .reporting import metrics
        if (any(k != 'project' for k in request.args) or len(request.args.getlist('project')) > 1
                or ident and request.args):
            raise Problem(422, 'query_validation', 'Metrics accepts only an optional project filter, or exact task')
        return run(lambda s: {'data': metrics(s, request.args.get('project'), str(ident) if ident else None)})

    @app.post('/api/v1/tasks/<uuid:ident>/pause')
    @app.post('/api/v1/tasks/<uuid:ident>/resume')
    def task_intervention(ident):
        from .schemas import Intervention
        from .interventions import intervene
        ident, data = str(ident), body(Intervention)
        paused = request.path.endswith('/pause')
        def change(s):
            item = s.get(Task, ident, 'tasks:write')
            s.permit(item.project_id, 'tasks:write', operator=True)
            return s.replay('POST ' + request.path, request.headers.get('Idempotency-Key'), data,
                lambda: ({'data': intervene(s, ident, data, paused)}, 200))
        return run(change, True)

    @app.route('/api/v1/tasks/<uuid:ident>/contract', methods=['GET', 'POST'])
    def task_contract(ident):
        from .schemas import ContractWrite
        from .governance import history, write_contract
        ident = str(ident)
        if request.method == 'GET':
            return run(lambda s: {'data': history(s, ident)})
        data = body(ContractWrite)
        def revise(s):
            item = s.get(Task, ident, 'tasks:write')
            s.permit(item.project_id, 'tasks:write', operator=True)
            return s.replay(f'POST /tasks/{ident}/contract', request.headers.get('Idempotency-Key'), data,
                            lambda: ({'data': write_contract(s, ident, data)}, 201))
        return run(revise, True)

    @app.get('/api/v1/tasks/tree')
    @app.get('/api/v1/tasks/<uuid:ident>/tree')
    def task_tree(ident=None):
        if any(k != 'project' for k in request.args) or len(request.args.getlist('project')) > 1:
            raise Problem(422, 'query_validation', 'Task trees accept only an optional project filter')
        return run(lambda s: {'data': s.task_tree(request.args.get('project'), str(ident) if ident else None)})

    @app.post('/api/v1/tasks/<uuid:ident>/transition')
    def transition(ident):
        ident, data = str(ident), body(Transition)
        def change(s):
            s.get(Task, ident, 'tasks:write')
            return s.replay(f'POST /tasks/{ident}/transition', request.headers.get('Idempotency-Key'), data,
                            lambda: ({'data': s.patch_task(ident, data, True)}, 200))
        return run(change, True)

    @app.get('/api/v1/tasks/<uuid:ident>/events')
    def task_events(ident):
        filters, limit, offset = query_options(Event)
        filters['task_id'] = str(ident)
        def events_for(s):
            s.get(Task, str(ident))
            return s.listing(Event, filters, limit, offset)
        return run(events_for)

    @app.get('/api/v1/events')
    def events():
        filters, limit, offset = query_options(Event)
        return run(lambda s: s.listing(Event, filters, limit, offset))

    def register_runtime_routes(resource, model, schema):
        def collection():
            if request.method == 'GET':
                filters, limit, offset = query_options(model)
                return run(lambda s: s.listing(model, filters, limit, offset))
            data = body(schema)
            return run(lambda s: ({'data': s.register(model, data)}, 201), True)
        def detail(ident):
            if request.method == 'GET':
                return run(lambda s: {'data': serialize(s.get(model, str(ident)))})
            data = body(RuntimePatch)
            return run(lambda s: {'data': s.runtime_update(model, str(ident), data)}, True)
        def heartbeat(ident):
            body(Empty)
            return run(lambda s: {'data': s.runtime_update(model, str(ident), {}, True)}, True)
        app.add_url_rule(f'/api/v1/{resource}', resource, collection, methods=['GET', 'POST'])
        app.add_url_rule(f'/api/v1/{resource}/<uuid:ident>', resource + '_detail', detail, methods=['GET', 'PATCH'])
        app.add_url_rule(f'/api/v1/{resource}/<uuid:ident>/heartbeat', resource + '_heartbeat', heartbeat, methods=['POST'])

    register_runtime_routes('panes', Pane, PaneCreate)
    register_runtime_routes('agent-sessions', AgentSession, SessionCreate)


    @app.route('/api/v1/approvals', methods=['GET', 'POST'])
    def approvals():
        from .approvals import request_approval
        if request.method == 'GET':
            filters, limit, offset = query_options(Approval)
            return run(lambda s: s.listing(Approval, filters, limit, offset))
        data = body(ApprovalCreate)
        def create(s):
            s.project(data['project_id'], 'approvals:request')
            return s.replay('POST /approvals', request.headers.get('Idempotency-Key'), data,
                            lambda: ({'data': request_approval(s, data)}, 201))
        return run(create, True)

    @app.get('/api/v1/approvals/<uuid:ident>')
    def approval(ident):
        from .approvals import view
        return run(lambda s: {'data': s.resource_view(s.get(Approval, str(ident)))})

    @app.post('/api/v1/approvals/<uuid:ident>/resolve')
    def approval_resolve(ident):
        from .approvals import resolve
        data = body(ApprovalResolve)
        return run(lambda s: {'data': resolve(s, str(ident), data)}, True)

    @app.post('/api/v1/approvals/<uuid:ident>/consume')
    def approval_consume(ident):
        from .approvals import consume
        data = body(ApprovalConsume)
        return run(lambda s: {'data': consume(s, str(ident), data)}, True)

    @app.get('/api/v1/incidents')
    def incidents():
        filters, limit, offset = query_options(Incident)
        return run(lambda s: s.listing(Incident, filters, limit, offset))

    @app.get('/api/v1/state')
    @app.get('/api/v1/state/projects/<project>')
    def state(project=None):
        if any(k not in {'project', 'recent_events'} for k in request.args) or any(len(request.args.getlist(k)) != 1 for k in request.args):
            raise Problem(422, 'query_validation', 'Unknown snapshot parameter')
        try:
            recent = int(request.args.get('recent_events', config.recent_events))
            if not 1 <= recent <= 200:
                raise ValueError
        except ValueError:
            raise Problem(422, 'query_validation', 'recent_events must be 1..200')
        if project and request.args.get('project') and project != request.args['project']:
            raise Problem(422, 'query_validation', 'Conflicting project filters')
        return run(lambda s: {'data': s.snapshot(project or request.args.get('project'), recent)})

    # These closures all enter the established Service transaction/replay boundary.
    from . import durable, schemas

    def durable_collection(resource, model):
        def collection():
            filters, limit, offset = query_options(model)
            if model is Incident:
                filters['incident_type'] = 'task_exception'
            return run(lambda s: s.listing(model, filters, limit, offset))
        def detail(ident):
            def read(s):
                item = s.get(model, str(ident))
                if model is Incident and item.incident_type != 'task_exception':
                    raise Problem(404, 'not_found', 'Task exception not found')
                return {'data': s.resource_view(item)}
            return run(read)
        app.add_url_rule('/api/v1/' + resource, resource + '_list', collection)
        app.add_url_rule('/api/v1/' + resource + '/<uuid:ident>', resource + '_get', detail)

    for resource, model in [('deliverables', Deliverable), ('deliverable-revisions', DeliverableRevision),
                            ('validation-evidence', ValidationEvidence), ('checkpoints', Checkpoint),
                            ('exceptions', Incident), ('remediations', Remediation)]:
        durable_collection(resource, model)

    def durable_write(path, model, schema, operation, approval_permission=False):
        def endpoint(ident):
            ident, data = str(ident), body(schema)
            def mutate(s):
                item = s.get(model, ident, 'tasks:write')
                # Replay must retain the operation's reference-read permission checks
                # without rerunning version checks or successful mutations.
                reads_references = operation in (durable.record_evidence, durable.supersede) or (
                    operation is durable.create_checkpoint and (
                        data['revision_ids'] or data.get('next_authorized_action') == 'record_evidence'))
                if reads_references:
                    s.permit(item.project_id, 'read')
                if approval_permission:
                    s.permit(item.project_id, 'approvals:consume')
                return s.replay('POST ' + request.path, request.headers.get('Idempotency-Key'), data,
                    lambda: ({'data': operation(s, ident, data)}, 201))
            return run(mutate, True)
        app.add_url_rule('/api/v1/' + path, path, endpoint, methods=['POST'])

    for path, model, schema, operation in [
        ('tasks/<uuid:ident>/deliverables', Task, schemas.DeliverableCreate, durable.create_deliverable),
        ('deliverables/<uuid:ident>/revisions', Deliverable, schemas.DeliverableRevise, durable.revise_deliverable),
        ('deliverables/<uuid:ident>/evidence', Deliverable, schemas.EvidenceCreate, durable.record_evidence),
        ('deliverables/<uuid:ident>/supersede', Deliverable, schemas.Supersede, durable.supersede),
        ('tasks/<uuid:ident>/checkpoints', Task, schemas.CheckpointCreate, durable.create_checkpoint),
        ('tasks/<uuid:ident>/exceptions', Task, schemas.ExceptionCreate, durable.record_exception),
        ('exceptions/<uuid:ident>/inspect', Incident, schemas.Versioned, durable.inspect_exception),
        ('exceptions/<uuid:ident>/resolve', Incident, schemas.ExceptionResolve, durable.resolve_exception),
    ]:
        durable_write(path, model, schema, operation)
    durable_write('exceptions/<uuid:ident>/remediations', Incident, schemas.RemediationRequest,
                  durable.request_remediation, True)
    durable_write('remediations/<uuid:ident>/authorize', Remediation, schemas.RemediationAuthorize,
                  durable.authorize_remediation, True)

    return app
