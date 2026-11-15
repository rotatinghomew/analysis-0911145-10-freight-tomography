"""Demand inversion from heterogeneous traffic observations.

The engine treats non-negative path flows as decision variables and turns
counters, path samples and business known_bounds into linear constraints on
the same polytope.  Evidence is kept in strictly separated layers:

* identification layer ...... fitted counters + known business bounds (hard
  evidence).  Every quantity of interest gets a data-identified range by
  minimizing and maximizing it over this polytope.
* assumption layer .......... adds the inverse-probability sample bands, which
  depend on sampling assumptions and are never presented as unbiased counts.

Point estimates are analytic centres of the feasible polytope (with an LP
centre as fallback), never claims that the data identify a unique flow.  When
the hard evidence is contradictory the minimum weighted violation is located,
the involved constraints are widened by exactly that violation, and every
output is marked as coming from a relaxed fit.

Scenarios for corridor closures are forward projections of the historical fit:
no counter is re-fitted there, so the demand-unchained hypothesis and the
reroute-fraction uncertainty stay separate from the historical estimation.
"""

from app.counters import box_interval
from app.flows import incidence
from app.samples import sample_intervals
from app.store import finite_number, unique

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, minimize

TOL = 1e-9
RELAX_TOL = 1e-7
ROUND = 6


def _clean(value):
    """Round for JSON output and suppress numerically tiny negatives."""
    if value is None:
        return None
    if abs(value) < 1e-8:
        return 0.0
    return round(float(value), ROUND)


class Polytope:
    """Dense linear inequality polytope over non-negative path flows."""

    def __init__(self, n):
        self.n = n
        self.rows = []          # (vector, rhs, meta)
        self.equalities = []    # (vector, rhs, meta) kept for provenance

    def add(self, vector, rhs, sense, meta):
        vector = np.asarray(vector, dtype=float)
        if sense == '<=':
            self.rows.append((vector, float(rhs), dict(meta, equality=False)))
        elif sense == '>=':
            self.rows.append((-vector, float(-rhs), dict(meta, equality=False)))
        elif sense == '==':
            self.equalities.append((vector, float(rhs), meta))
            self.rows.append((vector, float(rhs), dict(meta, equality=True)))
            self.rows.append((-vector, float(-rhs), dict(meta, equality=True)))
        else:
            raise ValueError('unknown constraint sense')

    def matrices(self):
        if not self.rows:
            return np.empty((0, self.n)), np.empty(0)
        matrix = np.vstack([row[0] for row in self.rows])
        rhs = np.array([row[1] for row in self.rows], dtype=float)
        return matrix, rhs

    def clone(self):
        copy = Polytope(self.n)
        copy.rows = [row for row in self.rows]
        copy.equalities = [row for row in self.equalities]
        return copy

    def inequality_matrices(self):
        selected = [row for row in self.rows if not row[2].get('equality')]
        if not selected:
            return np.empty((0, self.n)), np.empty(0)
        matrix = np.vstack([row[0] for row in selected])
        rhs = np.array([row[1] for row in selected], dtype=float)
        return matrix, rhs

    def equality_matrices(self):
        if not self.equalities:
            return np.empty((0, self.n)), np.empty(0)
        matrix = np.vstack([row[0] for row in self.equalities])
        rhs = np.array([row[1] for row in self.equalities], dtype=float)
        return matrix, rhs


def _bounds(n):
    return [(0.0, None)] * n


def _solve(c, matrix, rhs, bounds, eq_matrix=None, eq_rhs=None):
    result = linprog(c, A_ub=matrix if matrix.size else None,
                     b_ub=rhs if rhs.size else None,
                     A_eq=eq_matrix, b_eq=eq_rhs, bounds=bounds,
                     method='highs')
    if result.status == 0:
        return result.x
    if result.status in (2, 3):
        # 2: infeasible, 3: unbounded — both mean this LP yields no usable point.
        return None
    raise ValueError('linear program failed: ' + str(result.message))


def is_feasible(poly):
    matrix, rhs = poly.matrices()
    return _solve(np.zeros(poly.n), matrix, rhs, _bounds(poly.n)) is not None


def extreme(poly, vector, maximum=False):
    """Min (or max) of a linear quantity over the polytope."""
    matrix, rhs = poly.matrices()
    objective = -np.asarray(vector, dtype=float) if maximum else np.asarray(vector, dtype=float)
    point = _solve(objective, matrix, rhs, _bounds(poly.n))
    if point is None:
        raise ValueError('polytope is infeasible')
    value = float(np.dot(vector, point))
    return max(value, 0.0) if not maximum else value


def value_range(poly, vector):
    lower = extreme(poly, vector, maximum=False)
    upper = extreme(poly, vector, maximum=True)
    return lower, upper


def _lp_center(poly, ref_scale):
    """Maximum margin centre used as a fallback for fully degenerate polytopes.

    Each inequality a.x <= b becomes a.x + s*t <= b with s the shared scale
    max(|b|_median, 1).  On a polytope with a strict interior the analytic
    centre is preferred; this LP centre remains exact when no interior exists.
    It is a deterministic, regularisation-free reference, not a statistically
    identified estimate.
    """
    matrix, rhs = poly.matrices()
    n = poly.n
    scale = max(ref_scale, 1.0)
    widths = np.full(rhs.shape, scale)
    extended = np.column_stack([matrix, widths])
    objective = np.zeros(n + 1)
    objective[-1] = -1.0
    bounds = _bounds(n) + [(None, None)]
    point = _solve(objective, extended, rhs, bounds)
    if point is None:
        raise ValueError('polytope is infeasible')
    return np.maximum(point[:n], 0.0)


def _interior_start(poly):
    """Strictly feasible start from a Chebyshev LP.

    Equality rows stay exact.  Over the inequalities we maximize the minimum
    absolute slack in one common scale, which yields a point strictly inside
    the inequality region whenever one exists.  On a fully fixed polytope no
    strict interior exists and None is returned so the LP centre is used.
    """
    ineq, ineq_rhs = poly.inequality_matrices()
    eq, eq_rhs = poly.equality_matrices()
    if ineq.size == 0:
        return None
    n = poly.n
    # The polytope may be unbounded, which makes the Chebyshev LP unbounded.
    # Derive one observation-scale cap per variable; this only bounds the
    # interior construction, never the identified ranges reported elsewhere.
    caps = np.full(n, max(np.max(np.abs(ineq_rhs)) if ineq_rhs.size else 1.0, 1.0))
    # Lay out [structural inequalities][x <= cap][x >= 0] in that order, so
    # the margin column applies only to the first two blocks.  Non-negativity
    # cannot carry a positive margin when a variable is fixed at zero.
    cap_matrix, cap_rhs = np.eye(n), caps
    nonneg_matrix, nonneg_rhs = -np.eye(n), np.zeros(n)
    margin_count = ineq.shape[0] + n
    margin = np.concatenate([np.ones(margin_count), np.zeros(n)])
    stacked_a = np.vstack([ineq, cap_matrix, nonneg_matrix])
    stacked_b = np.concatenate([ineq_rhs, cap_rhs, nonneg_rhs])
    # Variables: x (n), slack t.  Equality rows: exact; inequality rows: +t.
    eq_block = np.column_stack([eq, np.zeros(eq.shape[0])])
    a = np.column_stack([stacked_a, margin])
    b = stacked_b
    objective = np.zeros(n + 1)
    objective[-1] = -1.0
    point = _solve(objective, a, b, _bounds(n) + [(0.0, caps[0])],
                   eq_matrix=eq_block if eq.size else None,
                   eq_rhs=eq_rhs if eq.size else None)
    if point is None:
        return None
    start = np.maximum(point[:n], 0.0)
    gap = float(np.min(ineq_rhs - ineq @ start))
    if not np.isfinite(gap) or gap <= 1e-7:
        return None
    if eq.size and np.max(np.abs(eq_rhs - eq @ start)) > 1e-7:
        return None
    return start


def center(poly, ref_scale):
    """Analytic centre of the polytope, with an LP centre as fallback.

    The analytic centre minimizes -sum(log(b - A.x)) over the inequality
    region while equality constraints remain exact, so on an unbounded or
    slab-shaped polytope it stays in the relative interior instead of
    collapsing onto a vertex.  It is a deterministic reference for the
    reported point estimate; the identified intervals from the LP extrema
    remain the honest statement of what the data distinguish.
    """
    fallback = _lp_center(poly, ref_scale)
    # The barrier only covers strictly positive-slack structural inequalities;
    # non-negativity is enforced through the optimizer bounds, since a variable
    # fixed at zero has no finite log barrier and breaks the line search.
    structural = [(row[0], row[1]) for row in poly.rows
                  if not row[2].get('equality') and np.any(row[0] != 0.0)
                  and not _is_nonneg_row(row[0], poly.n)]
    if not structural:
        return fallback
    ineq = np.vstack([item[0] for item in structural])
    ineq_rhs = np.array([item[1] for item in structural], dtype=float)
    ineq, ineq_rhs = poly.inequality_matrices()
    eq, eq_rhs = poly.equality_matrices()
    start = _interior_start(poly)
    if start is None:
        return fallback
    if np.min(ineq_rhs - ineq @ start) <= 1e-7:
        return fallback
    gap = np.min(ineq_rhs - ineq @ start)

    def objective(x):
        slacks = np.maximum(ineq_rhs - ineq @ x, 1e-300)
        value = -np.sum(np.log(slacks))
        gradient = -ineq.T @ (1.0 / slacks)
        return value, gradient

    def hessian(x):
        slacks = np.maximum(ineq_rhs - ineq @ x, 1e-300)
        weights = 1.0 / (slacks ** 2)
        return (ineq * weights).T @ ineq

    try:
        outcome = minimize(objective, start, jac=True, hess=hessian,
                           method='trust-constr',
                           bounds=Bounds(np.zeros(poly.n),
                                         np.full(poly.n, np.inf)),
                           constraints=([LinearConstraint(eq, eq_rhs, eq_rhs)]
                                        if eq.size else []),
                           options={'maxiter': 1000, 'gtol': 1e-10,
                                    'xtol': 1e-12, 'barrier_tol': 1e-12,
                                    'verbose': 0})
    except Exception:
        return fallback
    if not outcome.success or np.any(~np.isfinite(outcome.x)):
        return fallback
    if np.min(ineq_rhs - ineq @ outcome.x) <= 1e-7 * max(gap, 1.0):
        return fallback
    if eq.size and np.max(np.abs(eq_rhs - eq @ outcome.x)) > 1e-6 * max(np.max(np.abs(eq_rhs)), 1.0):
        return fallback
    return np.maximum(outcome.x, 0.0)


def _is_nonneg_row(vector, n):
    """True for rows generated by the x_i >= 0 non-negativity constraints."""
    nonzero = np.flatnonzero(vector)
    return len(nonzero) == 1 and vector[nonzero[0]] == -1.0


def minimum_violation(poly):
    """L1 relaxation: minimum total weighted violation of every row.

    Returns (slack per row, optimum value).  Rows with a slack above the
    numerical tolerance are exactly the constraints that must be relaxed to
    restore feasibility.
    """
    matrix, rhs = poly.matrices()
    m, n = matrix.shape
    scales = np.maximum(np.abs(rhs), 1.0)
    # Variables: x (n), then one non-negative slack per row.
    a = np.zeros((2 * m, n + m))
    b = np.zeros(2 * m)
    for i in range(m):
        # matrix[i].x - u_i <= rhs[i]
        a[2 * i, :n] = matrix[i]
        a[2 * i, n + i] = -1.0
        b[2 * i] = rhs[i]
        # -matrix[i].x - u_i <= -rhs[i]
        a[2 * i + 1, :n] = -matrix[i]
        a[2 * i + 1, n + i] = -1.0
        b[2 * i + 1] = -rhs[i]
    objective = np.zeros(n + m)
    objective[n:] = 1.0 / scales
    point = _solve(objective, a, b, _bounds(n) + [(0.0, None)] * m)
    if point is None:
        raise ValueError('relaxation failed')
    slacks = point[n:]
    return slacks, float(np.dot(objective[n:], slacks)), scales


def _active_paths(case, closed_edges=()):
    matrix = incidence(case, closed_edges)
    by_id = unique(case['paths'], 'id')
    return [by_id[identifier] for identifier in matrix['paths']]


def _edge_vector(paths, edge):
    return [path['edges'].count(edge) for path in paths]


def _od_vector(paths, od):
    return [1.0 if path['od'] == od else 0.0 for path in paths]


def _validate_closure(case, closure):
    known_edges = {edge for path in case['paths'] for edge in path['edges']}
    edges = closure.get('edges', [])
    if not isinstance(edges, list) or any(not isinstance(edge, str) for edge in edges):
        raise ValueError('closure edges must be a list of edge ids')
    unknown = sorted(set(edges) - known_edges)
    if unknown:
        raise ValueError('closure references unknown edges: ' + ','.join(unknown))
    band = closure.get('reroute_band')
    if band is not None:
        if not isinstance(band, list) or len(band) != 2:
            raise ValueError('reroute_band must be [lower, upper]')
        lower, upper = finite_number(band[0]), finite_number(band[1])
        if not 0.0 <= lower <= upper <= 1.0:
            raise ValueError('reroute_band must satisfy 0 <= lower <= upper <= 1')
    return list(edges), band


def _build_hard_polytope(paths, case, diagnostics, holdout_ids):
    """Counter intervals and business bounds; holdouts are excluded."""
    poly = Polytope(len(paths))
    for counter in case['counters']:
        identifier = counter['id']
        if identifier in holdout_ids:
            continue
        interval = box_interval(counter)
        if interval is None:
            diagnostics.append({
                'code': 'OFFLINE_COUNTER', 'severity': 'warning',
                'message': 'counter ' + identifier + ' on ' + counter['edge']
                           + ' is offline: it contributes no constraint and a missing '
                             'reading must not be treated as zero flow'})
            continue
        lower, upper = interval
        vector = _edge_vector(paths, counter['edge'])
        if not any(vector):
            continue
        sense = '==' if lower == upper else '<='
        poly.add(vector, upper, sense, {'kind': 'counter', 'id': identifier})
        if lower != upper:
            poly.add(vector, lower, '>=', {'kind': 'counter', 'id': identifier})
        if counter['measure'] == 'trucks':
            load_low, load_high = counter['boxes_per_truck']
            diagnostics.append({
                'code': 'TRUCK_INTERVAL', 'severity': 'info',
                'message': 'counter ' + identifier + ' counts trucks only; box flow is the '
                           + str(lower) + '-' + str(upper) + ' interval implied by '
                           + str(load_low) + '-' + str(load_high) + ' boxes per truck, not a point count'})
        availability = counter.get('availability')
        if availability is not None and availability != 1.0:
            diagnostics.append({
                'code': 'AVAILABILITY_SCALING', 'severity': 'info',
                'message': 'counter ' + identifier + ' observed ' + str(counter['observed'])
                           + ' at availability ' + str(availability)
                           + '; the scaled reading assumes the unobserved period looks like the observed one'})
    for bound in case.get('known_bounds', []):
        vector = np.zeros(len(paths))
        for od, coefficient in bound['coefficients'].items():
            vector += coefficient * np.array(_od_vector(paths, od))
        if not np.any(vector):
            continue
        finite_number(bound['lower'])
        finite_number(bound['upper'])
        if bound['lower'] > bound['upper']:
            raise ValueError('known bound ' + bound['id'] + ' has lower above upper')
        poly.add(vector, bound['upper'], '<=', {'kind': 'bound', 'id': bound['id']})
        poly.add(vector, bound['lower'], '>=', {'kind': 'bound', 'id': bound['id']})
    return poly


def _add_sample_layer(poly, paths, case):
    sample_rows = sample_intervals(case['samples'], {path['id'] for path in paths})
    index = {path['id']: i for i, path in enumerate(paths)}
    for row in sample_rows:
        vector = np.zeros(len(paths))
        vector[index[row['path']]] = 1.0
        poly.add(vector, row['upper'], '<=', {'kind': 'sample', 'id': row['path']})
        poly.add(vector, row['lower'], '>=', {'kind': 'sample', 'id': row['path']})
    return sample_rows


def _relax_inplace(poly, diagnostics, layer_name, infeasible_code, relaxed_code):
    slacks, optimum, _scales = minimum_violation(poly)
    widened = Polytope(poly.n)
    relaxed_ids = []
    for (vector, rhs, meta), slack in zip(poly.rows, slacks):
        if slack > RELAX_TOL * max(1.0, abs(rhs)):
            relaxed_ids.append(meta['id'])
        widened.add(vector, rhs + slack, '<=', meta)
    diagnostics.append({
        'code': infeasible_code, 'severity': 'error',
        'message': layer_name + ' evidence is contradictory; no flow can satisfy every '
                   'constraint, so a minimum weighted violation fit is reported'})
    diagnostics.append({
        'code': relaxed_code, 'severity': 'error',
        'message': 'constraints relaxed to restore feasibility: '
                   + ','.join(sorted(set(relaxed_ids))) + ' (minimum weighted violation '
                   + str(round(optimum, ROUND)) + ')'})
    return widened, sorted(set(relaxed_ids))


def _od_sources(paths, case, od, with_samples):
    paths_of_od = {path['id'] for path in paths if path['od'] == od}
    edges_of_od = {edge for path in paths if path['od'] == od for edge in path['edges']}
    sources = []
    for counter in case['counters']:
        if counter['holdout']:
            continue
        if counter['edge'] in edges_of_od and box_interval(counter) is not None:
            sources.append(counter['id'])
    for bound in case.get('known_bounds', []):
        if od in bound['coefficients']:
            sources.append(bound['id'])
    if with_samples:
        for row in case['samples']:
            if row['path'] in paths_of_od:
                sources.append('sample:' + row['path'])
    return sources


def _interval_dict(lower, upper):
    return {'lower': _clean(lower), 'upper': _clean(upper),
            'width': _clean(max(upper - lower, 0.0))}


def _fitted_records(case, paths, point, hard_poly, holdout_ids, relaxed_ids):
    records = []
    for counter in case['counters']:
        if counter['id'] in holdout_ids:
            continue
        interval = box_interval(counter)
        vector = np.array(_edge_vector(paths, counter['edge']))
        predicted_point = float(np.dot(vector, point))
        predicted_low, predicted_high = value_range(hard_poly, vector)
        record = {
            'counter': counter['id'], 'edge': counter['edge'],
            'measure': counter['measure'],
            'availability': counter['availability'],
            'observed': counter['observed'],
            'status': 'offline' if interval is None else 'observed',
            'box_interval': None if interval is None else [_clean(interval[0]), _clean(interval[1])],
            'point_prediction': _clean(predicted_point),
            'predicted_interval': [_clean(predicted_low), _clean(predicted_high)],
            'relaxed': counter['id'] in relaxed_ids,
            'interpretation': ('offline counter: no constraint, prediction is the identified '
                               'range from the other evidence') if interval is None else
                              ('truck reading converted through the boxes-per-truck interval'
                               if counter['measure'] == 'trucks' else
                               'availability-scaled box reading treated as a point constraint')}
        records.append(record)
    return records


def _validation_records(case, paths, point, hard_poly, diagnostics, relaxed):
    records = []
    for counter in case['counters']:
        if not counter['holdout']:
            continue
        interval = box_interval(counter)
        vector = np.array(_edge_vector(paths, counter['edge']))
        predicted_point = float(np.dot(vector, point))
        predicted_low, predicted_high = value_range(hard_poly, vector)
        covered = None
        residual = None
        if interval is not None:
            covered = interval[0] <= predicted_high + TOL and predicted_low - TOL <= interval[1]
            residual = interval[0] - predicted_point
            if not covered:
                diagnostics.append({
                    'code': 'HOLDOUT_NOT_COVERED', 'severity': 'warning',
                    'message': 'holdout counter ' + counter['id'] + ' observed box interval '
                               + str([_clean(interval[0]), _clean(interval[1])])
                               + ' is outside the predicted range; the fitted structure fails this check'})
        records.append({
            'counter': counter['id'], 'edge': counter['edge'],
            'observed_box_interval': None if interval is None else [_clean(interval[0]), _clean(interval[1])],
            'point_prediction': _clean(predicted_point),
            'predicted_interval': [_clean(predicted_low), _clean(predicted_high)],
            'residual_at_point': None if residual is None else _clean(residual),
            'covered': covered,
            'used_in_fitting': False,
            'interpretation': 'held-out evidence, never used for fitting or relaxation choices'
                              + (' on a relaxed fit' if relaxed else '')})
    return records


def _scenario_polytope(surviving, od_limits):
    poly = Polytope(len(surviving))
    for od, (lower, upper) in od_limits.items():
        vector = np.array(_od_vector(surviving, od))
        if np.any(vector):
            poly.add(vector, upper, '<=', {'kind': 'scenario_od', 'id': od})
            poly.add(vector, lower, '>=', {'kind': 'scenario_od', 'id': od})
    return poly


def _edge_compare(edge, surviving, baseline_intervals, scenario_poly):
    vector = np.array(_edge_vector(surviving, edge))
    if not np.any(vector):
        return None
    low, high = value_range(scenario_poly, vector)
    base_low, base_high = baseline_intervals[edge]
    change_low, change_high = low - base_high, high - base_low
    if change_high <= -1e-6:
        direction = 'decreases'
    elif change_low >= 1e-6:
        direction = 'increases'
    else:
        direction = 'overlaps'
    return {'edge': edge,
            'baseline_interval': [_clean(base_low), _clean(base_high)],
            'scenario_interval': _interval_dict(low, high),
            'change_interval': [_clean(change_low), _clean(change_high)],
            'direction': direction}


def _run_scenarios(case, paths, closure, od_outputs, baseline_poly, point, diagnostics, relaxed):
    closed_edges, reroute_band = _validate_closure(case, closure)
    surviving = _active_paths(case, closed_edges)
    surviving_ids = {path['id'] for path in surviving}
    all_edges = sorted({edge for path in paths for edge in path['edges']})
    baseline_intervals = {
        edge: value_range(baseline_poly, np.array(_edge_vector(paths, edge)))
        for edge in all_edges}
    surviving_ods = sorted({path['od'] for path in surviving})
    unserved = sorted({path['od'] for path in paths
                       if path['od'] not in surviving_ods})

    # Variant 1: demand unchanged.  OD totals stay inside the historically
    # identified interval; routing is free across surviving paths.
    unchanged_limits = {
        row['od']: (row['identified']['lower'], row['identified']['upper'])
        for row in od_outputs if row['od'] in surviving_ods}
    unchanged_poly = _scenario_polytope(surviving, unchanged_limits)
    unchanged_od = []
    for row in od_outputs:
        od = row['od']
        if od in unserved:
            served = (0.0, 0.0)
        else:
            served = value_range(unchanged_poly, np.array(_od_vector(surviving, od)))
        unchanged_od.append({
            'od': od, 'baseline_point': row['point'],
            'baseline_identified_interval': [row['identified']['lower'], row['identified']['upper']],
            'served_interval': _interval_dict(*served),
            'unserved': od in unserved})
    unchanged_edges = [record for record in
                       (_edge_compare(edge, surviving, baseline_intervals, unchanged_poly)
                        for edge in all_edges if edge not in closed_edges)
                       if record is not None]

    # Variant 2: reroute fraction uncertainty around the historical point
    # estimate.  This introduces driver behaviour, not new observations.
    reroute_limits = {}
    if reroute_band is not None:
        r_low, r_high = reroute_band
        for row in od_outputs:
            od = row['od']
            if od in unserved:
                continue
            demand = row['point']
            reroute_limits[od] = (r_low * demand, r_high * demand)
        reroute_poly = _scenario_polytope(surviving, reroute_limits)
        reroute_od = []
        for row in od_outputs:
            od = row['od']
            if od in unserved:
                served = (0.0, 0.0)
            else:
                served = value_range(reroute_poly, np.array(_od_vector(surviving, od)))
            reroute_od.append({
                'od': od, 'baseline_point': row['point'],
                'reroute_band': reroute_band,
                'served_interval': _interval_dict(*served),
                'unserved': od in unserved})
        reroute_edges = [record for record in
                         (_edge_compare(edge, surviving, baseline_intervals, reroute_poly)
                          for edge in all_edges if edge not in closed_edges)
                         if record is not None]
    else:
        reroute_poly = None
        reroute_od, reroute_edges = [], []

    for od in unserved:
        diagnostics.append({
            'code': 'UNSERVED_OD', 'severity': 'warning',
            'message': 'with edges ' + ','.join(closed_edges) + ' closed, OD pair ' + od
                       + ' has no surviving path; its demand cannot be served regardless of reroute share'})

    # Findings that hold under both scenario variants.
    robust = []
    for edge in closed_edges:
        base = baseline_intervals.get(edge)
        robust.append({'kind': 'closed_edge', 'edge': edge, 'scenario_flow': 0.0,
                       'baseline_interval': None if base is None
                       else [_clean(base[0]), _clean(base[1])]})
    if reroute_poly is not None:
        reroute_by_edge = {row['edge']: row for row in reroute_edges}
        for row in unchanged_edges:
            other = reroute_by_edge.get(row['edge'])
            if other is not None and other['direction'] == row['direction'] \
                    and row['direction'] in ('decreases', 'increases'):
                robust.append({'kind': 'edge_direction', 'edge': row['edge'],
                               'direction': row['direction'],
                               'note': 'same direction under demand-unchanged and reroute-band projections'})
        for od in unserved:
            robust.append({'kind': 'unserved_od', 'od': od})

    diagnostics.append({
        'code': 'SCENARIO_SEPARATION', 'severity': 'info',
        'message': 'closure scenarios are forward projections of the historical point '
                   'estimate and identified ranges; no counter is re-fitted inside a scenario'
                   + (' (scenarios inherit a relaxed fit)' if relaxed else '')})

    return {
        'closed_edges': closed_edges,
        'surviving_paths': sorted(surviving_ids),
        'demand_unchanged': {
            'assumptions': ['od totals remain at the historically identified interval',
                            'flows may redistribute freely across surviving paths'],
            'od': unchanged_od, 'edges': unchanged_edges},
        'reroute_band': None if reroute_band is None else {
            'band': reroute_band,
            'assumptions': ['od totals equal the historical point estimate times the reroute fraction',
                            'reroute fraction is an unobserved driver choice, not a measurement'],
            'od': reroute_od, 'edges': reroute_edges},
        'robust_findings': robust}


def _allocation_spread(poly, paths, od_names, grid=4):
    """Pairwise conditional-width spread over the path-flow polytope.

    Fixing one path flow at interior quantiles and measuring the conditional
    width of every other path captures routing allocation uncertainty,
    including how the same OD splits across parallel paths.  Marginal OD
    interval widths cannot see this; a point-identified path polytope
    returns zero.
    """
    n = poly.n
    total = 0.0
    for i in range(n):
        unit_i = np.zeros(n)
        unit_i[i] = 1.0
        low_i, high_i = value_range(poly, unit_i)
        if high_i - low_i <= 1e-9:
            continue
        anchors = np.linspace(low_i, high_i, grid + 2)[1:-1]
        for j in range(i + 1, n):
            unit_j = np.zeros(n)
            unit_j[j] = 1.0
            for anchor_value in anchors:
                low, high = value_range_with_fix(poly, unit_i,
                                                 float(anchor_value), unit_j)
                total += max(high - low, 0.0)
    return total


def value_range_with_fix(poly, fixed_vector, fixed_value, objective_vector):
    """Range of a linear quantity while another OD total is held fixed."""
    probe = poly.clone()
    probe.add(np.asarray(fixed_vector, dtype=float), fixed_value, '==',
              {'kind': 'spread_fix'})
    if not is_feasible(probe):
        return 0.0, 0.0
    return value_range(probe, np.asarray(objective_vector, dtype=float))


def extreme_point(poly, vector, maximum=False):
    """Like extreme(), but returns the optimizer rather than its objective."""
    matrix, rhs = poly.matrices()
    objective = -np.asarray(vector, dtype=float) if maximum else np.asarray(vector, dtype=float)
    point = _solve(objective, matrix, rhs, _bounds(poly.n))
    if point is None:
        raise ValueError('polytope is infeasible')
    return point


def _counterfactual_probes(case, paths, hard_poly, point, od_outputs, diagnostics):
    """Rank edges where a new box point counter would shrink OD uncertainty."""
    od_names = [row['od'] for row in od_outputs]
    base_widths = {row['od']: row['identified']['width'] for row in od_outputs}
    baseline_total = sum(base_widths.values())
    # Joint spread captures routing allocation uncertainty across parallel
    # paths, which marginal OD interval widths cannot see.  The baseline is
    # computed once and reused for every counterfactual probe.
    base_spread = _allocation_spread(hard_poly, paths, od_names)
    present = {}
    for counter in case['counters']:
        present.setdefault(counter['edge'], []).append(counter)
    candidates = []
    holdout_edges = {counter['edge'] for counter in case['counters'] if counter['holdout']}
    for edge in sorted({edge for path in paths for edge in path['edges']}):
        devices = present.get(edge, [])
        # Holdout edges already carry a device: never propose fitting on them.
        if edge in holdout_edges:
            continue
        # A precise box reading already exists when a non-holdout box counter covers it.
        if any(not counter['holdout'] and counter['measure'] == 'boxes'
               and box_interval(counter) is not None for counter in devices):
            continue
        vector = np.array(_edge_vector(paths, edge))
        target = float(np.dot(vector, point))
        probe = hard_poly.clone()
        probe.add(vector, target, '==', {'kind': 'counterfactual', 'id': edge})
        per_od = {}
        for row in od_outputs:
            od_vector = np.array(_od_vector(paths, row['od']))
            low, high = value_range(probe, od_vector)
            per_od[row['od']] = _clean(base_widths[row['od']] - max(high - low, 0.0))
        probe_spread = _allocation_spread(probe, paths, od_names)
        joint_reduction = _clean(max(base_spread - probe_spread, 0.0))
        total_reduction = _clean(baseline_total - sum(per_od.values()))
        existing = devices[0] if devices else None
        candidates.append({
            'edge': edge,
            'existing_device': None if existing is None
            else {'counter': existing['id'], 'measure': existing['measure'],
                  'status': 'offline' if box_interval(existing) is None else 'observed'},
            'od_width_reduction': per_od,
            'total_od_width_reduction': total_reduction,
            'joint_allocation_spread_reduction': joint_reduction,
            'probe_reading': _clean(target)})
    candidates.sort(key=lambda row: (-row['total_od_width_reduction'], row['edge']))
    for rank, row in enumerate(candidates, start=1):
        row['rank'] = rank
    diagnostics.append({
        'code': 'VOI_SYNTHETIC_PROBE', 'severity': 'info',
        'message': 'counter placement value uses a counterfactual perfect box reading at the '
                   'current centre estimate; it ranks uncertainty reduction, it is not a measurement. '
                   'od_width_reduction measures marginal OD intervals; joint_allocation_spread_reduction '
                   'measures path-level routing allocation, including how an OD splits across parallel paths'})
    return candidates, baseline_total


def infer(case, closure=None):
    """Run the full demand inversion and return the consumer payload."""
    unique(case['paths'], 'id')
    unique(case['counters'], 'id')
    paths = _active_paths(case)
    holdout_ids = {counter['id'] for counter in case['counters'] if counter['holdout']}
    diagnostics = []

    hard_poly = _build_hard_polytope(paths, case, diagnostics, holdout_ids)
    ref_scale = float(np.median([abs(rhs) for _, rhs, _ in hard_poly.rows if abs(rhs) > 0] or [1.0]))
    relaxed_ids = []
    relaxed = False
    if not is_feasible(hard_poly):
        hard_poly, relaxed_ids = _relax_inplace(
            hard_poly, diagnostics, 'hard counter/bound',
            'INFEASIBLE_EVIDENCE', 'RELAXED_CONSTRAINTS')
        relaxed = True

    assumption_poly = hard_poly.clone()
    sample_rows = _add_sample_layer(assumption_poly, paths, case)
    relaxed_sample_ids = []
    if not is_feasible(assumption_poly):
        assumption_poly, relaxed_sample_ids = _relax_inplace(
            assumption_poly, diagnostics, 'sample-band',
            'INFEASIBLE_SAMPLE_LAYER', 'RELAXED_SAMPLES')

    point = center(hard_poly, ref_scale)
    assumption_point = center(assumption_poly, ref_scale)

    od_names = sorted({path['od'] for path in paths})
    od_outputs = []
    for od in od_names:
        vector = np.array(_od_vector(paths, od))
        identified = value_range(hard_poly, vector)
        assumed = value_range(assumption_poly, vector)
        od_outputs.append({
            'od': od,
            'paths': sorted(path['id'] for path in paths if path['od'] == od),
            'point': _clean(float(np.dot(vector, point))),
            'identified': {**_interval_dict(*identified),
                           'sources': _od_sources(paths, case, od, with_samples=False)},
            'assumption': {**_interval_dict(*assumed),
                           'center': _clean(float(np.dot(vector, assumption_point))),
                           'sources': _od_sources(paths, case, od, with_samples=True)},
            'identified_by_data': (identified[1] - identified[0]) <= 1e-6,
            'relaxed': relaxed})

    for row in sample_rows:
        diagnostics.append({
            'code': 'SAMPLE_SELECTION_BIAS', 'severity': 'info',
            'message': 'sample on path ' + row['path'] + ' is an inclusion-band expectation, not a '
                       'random draw; large direct-ship merchants are over-represented, so its band '
                       'only enters the assumption layer'})
    diagnostics.append({
        'code': 'SAMPLE_FLUCTUATION_IGNORED', 'severity': 'info',
        'message': 'sample bands describe sampling-probability uncertainty only; finite-sample '
                   'fluctuation of observed boxes is not modelled'})
    for row in od_outputs:
        if not row['identified_by_data']:
            diagnostics.append({
                'code': 'POINT_NOT_IDENTIFIED', 'severity': 'warning',
                'message': 'OD flow ' + row['od'] + ' is not point-identified by the hard evidence; '
                           'the point value is a max-margin centre and the identified interval must be reported'})
    diagnostics.append({
        'code': 'HOLDOUT_EXCLUDED', 'severity': 'info',
        'message': 'holdout counters (' + ','.join(sorted(holdout_ids)) + ') never enter fitting, '
                   'relaxation or counter placement probes'})

    fitted = _fitted_records(case, paths, point, hard_poly, holdout_ids, relaxed_ids)
    validation = _validation_records(case, paths, point, hard_poly, diagnostics, relaxed)

    closure = case['closure'] if closure is None else closure
    if closure is None:
        scenarios = None
    else:
        scenarios = _run_scenarios(case, paths, closure, od_outputs, hard_poly, point,
                                   diagnostics, relaxed)

    probes, baseline_total = _counterfactual_probes(case, paths, hard_poly, point,
                                                    od_outputs, diagnostics)

    return {
        'snapshot': case['_snapshot'],
        'day': case['day'],
        'relaxed_fit': relaxed,
        'relaxed_constraints': sorted(set(relaxed_ids + relaxed_sample_ids)),
        'od': od_outputs,
        'fitted': fitted,
        'validation': validation,
        'scenarios': scenarios,
        'information_value': {'baseline_total_od_width': _clean(baseline_total),
                              'candidates': probes},
        'diagnostics': diagnostics}
