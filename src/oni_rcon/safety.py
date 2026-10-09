"""Untrusted server and catalog text must not send commands to an operator's terminal."""
import re

_CONTROL = re.compile('[\x00-\x08\x0b-\x1f\x7f-\x9f]')


def clean(value):
    if isinstance(value, str):
        return _CONTROL.sub('', value)
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {clean(key): clean(item) for key, item in value.items()}
    return value
