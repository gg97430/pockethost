"""A finalized migration remains valid when new databases are created on the active host."""
import re

ID = re.compile(r'[a-z0-9]{15}\Z')


def reviewed_inventory(reviewed, current, *, allow_additions=False):
    for values in [reviewed, current]:
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not ID.fullmatch(v) for v in values):
            raise ValueError('Invalid recovery database inventory')
        if len(values) != len(set(values)):
            raise ValueError('Duplicate recovery database identifier')
    expected, actual = set(reviewed), set(current)
    if not expected.issubset(actual):
        raise ValueError('A reviewed migrated database is missing from the active controller')
    if not allow_additions and expected != actual:
        raise ValueError('The migration assembly inventory has changed')
    return sorted(actual)
