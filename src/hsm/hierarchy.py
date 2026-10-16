"""Deterministic task grouping and progress. Never changes lifecycle or ownership."""
from collections import Counter, defaultdict
from .state_machine import ACTIVE


def summary(counts):
    return {'total': sum(counts.values()), 'completed': counts['completed'],
            'active': sum(counts[s] for s in ACTIVE), 'blocked': counts['blocked'],
            'awaiting_approval': counts['awaiting_approval'], 'failed': counts['failed'],
            'cancelled': counts['cancelled'], 'by_state': dict(sorted(counts.items()))}


def task_forest(tasks):
    """Flat preorder with depths and separate direct-child/descendant summaries.

    Iterative traversal supports deep decomposition without Python recursion limits.
    Input contains every task in the selected project(s), including terminal work.
    """
    nodes = {task['id']: dict(task) for task in tasks}
    children = defaultdict(list)
    for task in nodes.values():
        parent = task.get('parent_id')
        children[parent if parent in nodes else None].append(task['id'])
    def order(ident):
        task = nodes[ident]
        return task['project_id'], task['task_key'], ident
    for ids in children.values():
        ids.sort(key=order)
    preorder = []
    stack = [(ident, 0, ident) for ident in reversed(children[None])]
    visited = set()
    while stack:
        ident, depth, root = stack.pop()
        if ident in visited:
            raise ValueError('Invalid task hierarchy')
        visited.add(ident)
        node = nodes[ident]
        node.update(depth=depth, root_id=root,
                    parent_key=nodes.get(node.get('parent_id'), {}).get('task_key'),
                    child_ids=list(children[ident]))
        preorder.append(node)
        stack.extend((child, depth + 1, root) for child in reversed(children[ident]))
    if len(visited) != len(nodes):
        raise ValueError('Invalid task hierarchy')
    descendants = {}
    for node in reversed(preorder):
        direct = Counter(nodes[c]['state'] for c in node['child_ids'])
        counts = direct.copy()
        for child in node['child_ids']:
            counts.update(descendants[child])
        descendants[node['id']] = counts
        node['child_summary'], node['descendant_summary'] = summary(direct), summary(counts)
    return preorder


def subtree(nodes, ident):
    """Select a root and all descendants; normalize displayed depth to this root."""
    start = next(i for i, node in enumerate(nodes) if node['id'] == ident)
    depth = nodes[start]['depth']
    end = start + 1
    while end < len(nodes) and nodes[end]['depth'] > depth:
        end += 1
    return [{**node, 'depth': node['depth'] - depth} for node in nodes[start:end]]
