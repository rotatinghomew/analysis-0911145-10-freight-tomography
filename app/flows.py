from app.counters import box_interval
from app.samples import sample_intervals
from app.store import unique


def incidence(case, closed_edges=()):
    unique(case['paths'], 'id')
    active = [path for path in case['paths'] if path['allowed'][0] <= case['day'] < path['allowed'][1]
              and not set(path['edges']).intersection(closed_edges)]
    edges = sorted({edge for path in active for edge in path['edges']})
    return {'paths': [path['id'] for path in active], 'edges': edges,
            'matrix': [[path['edges'].count(edge) for path in active] for edge in edges],
            'od': [path['od'] for path in active]}


def project_path_flows(case, path_flows):
    matrix = incidence(case)
    values = [path_flows.get(identifier, 0) for identifier in matrix['paths']]
    if any(value < 0 for value in values):
        raise ValueError('negative path flow')
    return {edge: sum(coefficient * value for coefficient, value in zip(row, values))
            for edge, row in zip(matrix['edges'], matrix['matrix'])}


def preview(case):
    matrix = incidence(case)
    return {'snapshot': case['_snapshot'], 'incidence': matrix,
            'counters': [{'id': counter['id'], 'edge': counter['edge'],
                          'box_interval': box_interval(counter), 'holdout': counter['holdout']}
                         for counter in case['counters']],
            'sample_bands': sample_intervals(case['samples'], set(matrix['paths'])),
            'flow_estimates_not_yet_available': True}
