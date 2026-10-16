TRANSITIONS = {
    'queued': {'planning', 'blocked', 'cancelled', 'failed'},
    'planning': {'running', 'awaiting_approval', 'blocked', 'failed', 'cancelled'},
    'running': {'review', 'awaiting_approval', 'blocked', 'failed', 'cancelled'},
    'awaiting_approval': {'planning', 'running', 'review', 'blocked', 'failed', 'cancelled'},
    'blocked': {'planning', 'running', 'review', 'awaiting_approval', 'failed', 'cancelled'},
    'review': {'running', 'awaiting_approval', 'blocked', 'completed', 'failed', 'cancelled'},
    'completed': set(), 'failed': set(), 'cancelled': set(),
}
ACTIVE = [s for s, destinations in TRANSITIONS.items() if destinations]
