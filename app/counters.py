def box_interval(counter):
    if counter['observed'] is None or counter['availability'] is None:
        return None
    availability = counter['availability']
    if not 0 < availability <= 1 or counter['observed'] < 0:
        raise ValueError('invalid counter coverage')
    count = counter['observed'] / availability
    if counter['measure'] == 'boxes':
        return count, count
    if counter['measure'] == 'trucks':
        lower, upper = counter['boxes_per_truck']
        if not 0 <= lower <= upper:
            raise ValueError('invalid truck load bounds')
        return count * lower, count * upper
    raise ValueError('unknown counting unit')
