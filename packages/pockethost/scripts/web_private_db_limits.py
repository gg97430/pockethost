"""Strict shared connection policy, supplied only by the trusted controller."""
DEFAULTS = {'total': 512, 'perApp': 128, 'perBinding': 128, 'perDatabase': 128}
MAXIMUM = {'total': 1024, 'perApp': 256, 'perBinding': 256, 'perDatabase': 512}


def limits(value=None):
    if value is None:
        return dict(DEFAULTS)
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError('Invalid connection policy')
    if any(type(value[k]) is not int or not 1 <= value[k] <= maximum for k, maximum in MAXIMUM.items()):
        raise ValueError('Invalid connection ceiling')
    if not value['perBinding'] <= value['perApp'] <= value['total'] or not value['perBinding'] <= value['perDatabase'] <= value['total']:
        raise ValueError('Inconsistent connection ceilings')
    return dict(value)
