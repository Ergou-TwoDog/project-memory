"""Single hook entry: L1 capture and L4 delivery.

Silent by design. If the project has no memory directory, or Git cannot answer,
this says nothing and creates nothing -- a hook must never surprise the user
with a new directory on the first stray Bash call.
"""
import json
import sys

from .capture import observe, project_dir
from .deliver import render, for_prompt
from .store import Store, MemoryError, dumps

MAX_INPUT = 65536
DELIVERING = ('SessionStart', 'UserPromptSubmit')


def _payload(name, text):
    if not text:
        return None
    return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': text}}


def handle(event):
    root = project_dir(event)
    if not root:
        return None
    try:
        store = Store.open(root)
    except MemoryError:
        return None
    name = event.get('hook_event_name')
    if name not in DELIVERING:
        for observation in observe(store, event):
            store.add_observation(observation)
        return None
    if name == 'SessionStart':
        for observation in observe(store, event):      # a session is an observation too
            store.add_observation(observation)
        return _payload(name, render(store))
    return _payload(name, for_prompt(store, event.get('prompt', '')))


def main():
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        return 0
    try:
        event = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError):
        return 0
    try:
        result = handle(event)
    except (MemoryError, OSError) as exc:
        print(f'project-memory: {exc}', file=sys.stderr)
        return 0
    if result:
        print(dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
