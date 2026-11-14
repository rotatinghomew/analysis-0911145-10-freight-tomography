def sample_intervals(samples, path_ids):
    result = []
    for row in samples:
        if row['path'] not in path_ids:
            raise ValueError('sample outside candidate paths')
        lower, upper = row['inclusion_band']
        if not 0 < lower <= upper <= 1:
            raise ValueError('invalid inclusion band')
        result.append({'path': row['path'], 'lower': row['observed_boxes'] / upper,
                       'upper': row['observed_boxes'] / lower,
                       'interpretation': 'assumption-dependent expectation band'})
    return result
