"""Ownership paths: exclude investor and natural-person endpoints; detect cycles."""

def analyze_ownership(graph):
    if not isinstance(graph, dict):
        return {'intermediate_legal_entity_count': None, 'cycle_detected': False, 'errors': ['Missing ownership graph']}
    nodes = graph.get('nodes', [])
    edges = graph.get('edges', [])
    root = graph.get('investor_id')
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return {'intermediate_legal_entity_count': None, 'cycle_detected': False, 'errors': ['Invalid ownership graph']}
    kinds, links, errors = {}, {}, []
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str) or node.get('entity_type') not in {'LegalEntity', 'NaturalPerson'}:
            errors.append('Every node needs an ID and entity_type'); continue
        if node['id'] in kinds: errors.append('Duplicate node ID')
        kinds[node['id']] = node['entity_type']
    for edge in edges:
        if not isinstance(edge, dict) or edge.get('entity_id') not in kinds or edge.get('owner_id') not in kinds:
            errors.append('Unknown ownership edge endpoint'); continue
        links.setdefault(edge['entity_id'], set()).add(edge['owner_id'])
    if root not in kinds or kinds.get(root) != 'LegalEntity': errors.append('Invalid investor endpoint')
    if errors: return {'intermediate_legal_entity_count': None, 'cycle_detected': False, 'errors': errors}
    # Explicit stack avoids recursive overflow; memoization reuses shared subgraphs.
    stack, active, memo, cycle = [(root, False)], set(), {}, False
    while stack:
        node, finish = stack.pop()
        if node in memo: continue
        if finish:
            active.discard(node)
            if kinds[node] == 'NaturalPerson':
                memo[node] = 0
            elif not links.get(node):
                errors.append('Legal entity path does not reach a natural person'); memo[node] = None
            else:
                lengths = [memo.get(owner) for owner in links[node]]
                memo[node] = None if any(n is None for n in lengths) else max(lengths) + (0 if node == root else 1)
            continue
        if node in active:
            cycle = True; errors.append('Ownership cycle requires human review'); break
        active.add(node); stack.append((node, True))
        if kinds[node] == 'NaturalPerson' and links.get(node):
            errors.append('Natural-person endpoint cannot have upstream owners')
        for owner in links.get(node, []):
            if owner in active:
                cycle = True; errors.append('Ownership cycle requires human review'); break
            if owner not in memo: stack.append((owner, False))
        if cycle: break
    return {'intermediate_legal_entity_count': None if cycle or errors else memo.get(root),
            'cycle_detected': cycle, 'errors': errors}
