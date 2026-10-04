"""Immutable memory records with strict validation.

An Observation is a fact about what happened (re-derivable from Git).
An Intent is a commitment about what should happen (not re-derivable).
The distinction is enforced: an intent always carries its origin.
"""
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from uuid import UUID, uuid4

OBSERVATION_KINDS = ('commit', 'session_start', 'session_end')
INTENT_STATUS = ('candidate', 'adopted', 'dropped')
INTENT_ORIGIN = ('llm', 'user')

MAX_TEXT = 4000
MAX_REFS = 32


class MemoryError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def new_id():
    return str(uuid4())


def text(value, name, *, maximum=MAX_TEXT):
    if type(value) is not str or not value.strip():
        raise MemoryError(f'{name}: nonempty string required')
    if len(value) > maximum:
        raise MemoryError(f'{name}: exceeds {maximum} characters')
    return value


def optional_text(value, name, *, maximum=MAX_TEXT):
    if value is None:
        return None
    return text(value, name, maximum=maximum)


def timestamp(value, name):
    if type(value) is not str:
        raise MemoryError(f'{name}: ISO timestamp required')
    try:
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError
    except (ValueError, TypeError):
        raise MemoryError(f'{name}: timezone-aware ISO timestamp required') from None
    return value


def identity(value, name='id'):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise MemoryError(f'{name}: canonical UUID required') from None
    return value


def _strings(value, name, *, maximum=MAX_REFS):
    if type(value) not in (tuple, list):
        raise MemoryError(f'{name}: list required')
    items = tuple(value)
    if len(items) > maximum:
        raise MemoryError(f'{name}: at most {maximum} entries')
    for item in items:
        text(item, name, maximum=256)
    return items


@dataclass(frozen=True)
class Observation:
    kind: str
    cwd: str
    source: str
    id: str = ''
    at: str = ''
    commit: str | None = None
    files_changed: tuple = ()
    message: str | None = None

    def __post_init__(self):
        if not self.id:
            object.__setattr__(self, 'id', new_id())
        if not self.at:
            object.__setattr__(self, 'at', now())
        identity(self.id)
        if self.kind not in OBSERVATION_KINDS:
            raise MemoryError('unsupported observation kind')
        text(self.cwd, 'cwd')
        text(self.source, 'source', maximum=128)
        timestamp(self.at, 'at')
        if self.commit is not None:
            if type(self.commit) is not str or len(self.commit) not in (40, 64) \
                    or any(c not in '0123456789abcdef' for c in self.commit):
                raise MemoryError('full lowercase commit hash required')
        object.__setattr__(self, 'files_changed', _strings(self.files_changed, 'files_changed', maximum=512))
        optional_text(self.message, 'message')

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class Intent:
    text: str
    why: str
    origin: str
    refs: tuple = ()
    status: str = 'candidate'
    id: str = ''
    proposed_at: str = ''
    decided_at: str | None = None

    def __post_init__(self):
        if not self.id:
            object.__setattr__(self, 'id', new_id())
        if not self.proposed_at:
            object.__setattr__(self, 'proposed_at', now())
        identity(self.id)
        text(self.text, 'text')
        text(self.why, 'why')
        if self.origin not in INTENT_ORIGIN:
            raise MemoryError('unsupported intent origin')
        if self.status not in INTENT_STATUS:
            raise MemoryError('unsupported intent status')
        timestamp(self.proposed_at, 'proposed_at')
        if self.decided_at is not None:
            timestamp(self.decided_at, 'decided_at')
        if self.status == 'candidate' and self.decided_at is not None:
            raise MemoryError('candidate intents are undecided')
        if self.status != 'candidate' and self.decided_at is None:
            raise MemoryError('decided intents require decided_at')
        object.__setattr__(self, 'refs', _strings(self.refs, 'refs'))

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}
