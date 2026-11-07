"""Graph cancellation is explicit and distinct from unfinished execution."""


def cancelled(event):
    return event.get('cancelled', False) is True


def completed(event):
    return event.get('completed', True) is True and not cancelled(event)


def issued(event):
    return event.get('issued', True) is True


def physical_edge(event):
    return event.get('physical_edge', False) is True


def unresolved_payload(event, field):
    # Pending cancellation consumed no payload. Partly executed canceled work
    # requires separate progress instrumentation before its payload is known.
    return (not completed(event) and not cancelled(event) or
            cancelled(event) and issued(event) and event[field] != 0)


def validate_lifecycle(event, cycles, partial):
    for flag in ('completed', 'cancelled', 'squashed', 'issued', 'physical_edge', 'physical_acceptance', 'pc_known'):
        if flag in event and type(event[flag]) is not bool:
            raise ValueError('invalid graph lifecycle flag: ' + flag)
    physical = event.get('physical_acceptance', False)
    if physical or physical_edge(event):
        if (physical is not True or cancelled(event) or not issued(event)
                or not isinstance(event.get('physical_source'), str) or not event['physical_source'].strip()
                or type(event.get('pc_known')) is not bool):
            raise ValueError('accepted physical work requires source and explicit PC ownership')
        policy = 'same-source-edge' if physical_edge(event) else 'matching-source-response'
        if event.get('completion_policy') != policy:
            raise ValueError('physical completion policy disagrees with event shape')
    elif 'physical_source' in event or 'completion_policy' in event:
        raise ValueError('physical metadata requires actual physical acceptance')
    if event.get('pc_known') is False and event['pc'] != 0:
        raise ValueError('unknown PC requires a zero placeholder')
    if physical_edge(event):
        if event.get('completed') is not True or not event['submitted'] == event['start'] == event['end']:
            raise ValueError('physical edge requires completed point work with no queue or span')
    if cancelled(event):
        if event.get('completed') is not False or type(event.get('issued')) is not bool:
            raise ValueError('canceled event requires explicit issued and completed=false')
        tick = event.get('cancel_cycle')
        if (type(tick) is not int or not 0 <= tick <= cycles or event['end'] != tick
                or not event['submitted'] <= event['start'] <= tick):
            raise ValueError('canceled event must clip its span at cancel_cycle')
        if not issued(event) and event['start'] != tick:
            raise ValueError('canceled pending event cannot consume a busy span')
        if not isinstance(event.get('cancel_reason'), str) or not event['cancel_reason'].strip():
            raise ValueError('canceled event requires an explicit reason')
    else:
        if 'cancel_cycle' in event or 'cancel_reason' in event:
            raise ValueError('cancellation metadata on a noncanceled event')
        if not completed(event) and not partial:
            raise ValueError('incomplete event requires a partial graph')
        accepted_snapshot = (physical and not physical_edge(event) and not completed(event) and partial
                             and event['submitted'] == event['start'] == event['end'] == cycles)
        if (not issued(event) or event['end'] < event['start']
                or event['end'] == event['start'] and not (physical_edge(event) or accepted_snapshot)
                or event['submitted'] > event['start']):
            raise ValueError('invalid event timing')
    if event['end'] > cycles:
        raise ValueError('event exceeds run duration')


def cancelled_payload_unknown(event, field, lower=None, upper=None):
    """Unknown progress matters only where issued canceled work could execute.

    A counter boundary has no payload sub-edge ordering proof, so contact at
    either boundary remains unresolved. Work issued strictly after a region
    contributes queue wait there, not unknown executed payload.
    """
    if not cancelled(event) or not issued(event) or event[field] == 0:
        return False
    return lower is None or (event['start'] <= upper and event['end'] >= lower)


def validate_events(graph):
    """Validate one physical model DAG, including nodes canceled before issue."""
    from collections import deque

    if type(graph.get('schema_version')) is not int or graph['schema_version'] != 1:
        raise ValueError('unsupported graph schema')
    harts, cycles = graph.get('harts'), graph.get('cycles')
    if type(harts) is not int or harts <= 0 or type(cycles) is not int or cycles < 0:
        raise ValueError('invalid graph dimensions')
    partial = graph.get('partial', False)
    pending = graph.get('pending_operations', 0)
    if type(partial) is not bool or type(pending) is not int or pending < 0:
        raise ValueError('invalid partial graph metadata')
    if not partial and pending:
        raise ValueError('final graph has unfinished operations')
    resources = graph.get('resources', {})
    if not isinstance(resources, dict) or any(
            not isinstance(name, str) or not name.strip() or type(lanes) is not int or lanes <= 0
            for name, lanes in resources.items()):
        raise ValueError('invalid configured resource lanes')
    events = graph.get('events')
    if not isinstance(events, list):
        raise ValueError('graph events must be a list')
    nodes = {}
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('graph event must be an object')
        for key in ('id', 'hart', 'start', 'end', 'submitted', 'lane', 'bytes', 'macs', 'pc'):
            if type(event.get(key)) is not int or event[key] < 0:
                raise ValueError('invalid graph field: ' + key)
        if event['hart'] >= harts or event['pc'] >= 1 << 64 or event['id'] in nodes:
            raise ValueError('invalid hart/PC or duplicate graph node')
        for key in ('name', 'resource'):
            if not isinstance(event.get(key), str) or not event[key].strip():
                raise ValueError('invalid graph field: ' + key)
        if event.get('region') is not None and not isinstance(event['region'], str):
            raise ValueError('invalid graph field: region')
        if resources and (event['resource'] not in resources or event['lane'] >= resources[event['resource']]):
            raise ValueError('event uses an unconfigured resource or issue lane')
        dependencies = event.get('deps')
        if (not isinstance(dependencies, list) or any(type(dep) is not int or dep < 0 for dep in dependencies)
                or len(set(dependencies)) != len(dependencies)):
            raise ValueError('invalid or duplicate dependency')
        validate_lifecycle(event, cycles, partial)
        nodes[event['id']] = event
    indegree = {node: len(event['deps']) for node, event in nodes.items()}
    children = {node: [] for node in nodes}
    for node, event in nodes.items():
        for dependency in event['deps']:
            if dependency not in nodes or dependency == node:
                raise ValueError('missing or self dependency')
            children[dependency].append(node)
    ready = deque(node for node, count in indegree.items() if count == 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for child in children[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(nodes):
        raise ValueError('cyclic graph dependency')
    for event in events:
        if not issued(event):
            continue
        for dependency in event['deps']:
            parent = nodes[dependency]
            if not completed(parent) or parent['end'] > event['start']:
                raise ValueError('issued operation depends on unfinished or canceled work')
    return nodes
