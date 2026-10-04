"""Append-only local store under <project>/.project-memory/.

Observations are append-only JSONL. Intents are small, so status changes rewrite
the file atomically. Nothing here creates the directory implicitly: a project is
"initialized" only when the directory exists, and hooks never create it.
"""
from dataclasses import dataclass
from pathlib import Path
import json
import math
import os

from .model import MemoryError, Intent, Observation, identity

DIRNAME = '.project-memory'
OBSERVATIONS = 'observations.jsonl'
INTENTS = 'intents.jsonl'
OBSERVER = 'observer.json'
MAX_LINE = 262144
MAX_ITERATIONS = 4


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MemoryError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise MemoryError('nonfinite number')
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)
    return value


def loads(data):
    try:
        return _finite(json.loads(data, object_pairs_hook=_pairs,
                                  parse_constant=lambda x: (_ for _ in ()).throw(MemoryError(f'invalid number {x}'))))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise MemoryError(f'invalid JSON: {exc}') from exc


def dumps(value, ensure_ascii=False):
    """Storage keeps non-ASCII readable (files are written as UTF-8 bytes).
    Anything sent to a subprocess pipe or console must use ensure_ascii=True:
    a Windows console codepage would otherwise mangle it."""
    return json.dumps(value, sort_keys=True, ensure_ascii=ensure_ascii,
                      separators=(',', ':'), allow_nan=False)


def root_for(project):
    path = Path(project)
    if not path.is_absolute():
        raise MemoryError('absolute project path required')
    return path / DIRNAME


def _guarded(path):
    """Reject a symlinked memory directory: it must be a real directory we own."""
    if path.is_symlink():
        raise MemoryError('memory directory must not be a symlink')
    return path


def _append(path, value):
    data = (dumps(value) + '\n').encode('utf-8')
    if len(data) > MAX_LINE:
        raise MemoryError('record exceeds line budget')
    with path.open('ab') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _replace(path, value):
    _replace_lines(path, [value])


def _replace_lines(path, values):
    temporary = path.with_name(f'.{path.name}.writing')
    try:
        with temporary.open('wb') as stream:
            for value in values:
                stream.write((dumps(value) + '\n').encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_lines(path):
    if not path.exists():
        return []
    with path.open('rb') as stream:
        raw = stream.read()
    if len(raw) > 8 * 1048576:
        raise MemoryError('memory file exceeds read budget')
    rows = []
    for number, line in enumerate(raw.decode('utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(loads(line))
        except MemoryError as exc:
            raise MemoryError(f'{path.name}:{number}: {exc}') from exc
    return rows


@dataclass(frozen=True)
class Store:
    project: Path

    @classmethod
    def open(cls, project):
        root = root_for(project)
        if not root.exists():
            raise MemoryError('project memory not initialized')
        _guarded(root)
        if not root.is_dir():
            raise MemoryError('memory root must be a directory')
        return cls(project=Path(project))

    @property
    def root(self):
        return root_for(self.project)

    @classmethod
    def init(cls, project):
        root = _guarded(root_for(project))
        if root.exists():
            if not root.is_dir():
                raise MemoryError('memory root must be a directory')
            return cls(project=Path(project))
        root.mkdir()
        return cls(project=Path(project))

    # -- observations -----------------------------------------------------

    def add_observation(self, observation):
        _guarded(self.root)
        _append(self.root / OBSERVATIONS, observation.to_dict())
        return observation

    def observations(self, limit=None):
        rows = [Observation(**row) for row in _read_lines(self.root / OBSERVATIONS)]
        return tuple(rows[-limit:]) if limit else tuple(rows)

    # -- intents ----------------------------------------------------------

    def add_intent(self, intent):
        if intent.status != 'candidate' or intent.origin != 'llm':
            raise MemoryError('new intents must be undecided candidates proposed by the model')
        _guarded(self.root)
        _append(self.root / INTENTS, intent.to_dict())
        return intent

    def intents(self):
        return tuple(Intent(**row) for row in _read_lines(self.root / INTENTS))

    def intent(self, intent_id):
        identity(intent_id)
        for intent in self.intents():
            if intent.id == intent_id:
                return intent
        raise MemoryError('unknown intent')

    def decide(self, intent_id, status):
        """Operator-only transition. A model-proposed candidate becomes adopted."""
        if status not in ('adopted', 'dropped'):
            raise MemoryError('decision must be adopted or dropped')
        from .model import now
        current = self.intent(intent_id)
        if current.status != 'candidate':
            raise MemoryError(f'intent already {current.status}')
        updated = Intent(text=current.text, why=current.why, origin='user', refs=current.refs,
                         status=status, id=current.id, proposed_at=current.proposed_at, decided_at=now())
        rows = [updated if row.id == current.id else row for row in self.intents()]
        _guarded(self.root)
        _replace_lines(self.root / INTENTS, [row.to_dict() for row in rows])
        return updated

    # -- observer marker --------------------------------------------------

    def observer(self):
        path = self.root / OBSERVER
        if not path.exists():
            return None
        return loads(path.read_text(encoding='utf-8'))

    def set_observer(self, value):
        _guarded(self.root)
        _replace(self.root / OBSERVER, value)
