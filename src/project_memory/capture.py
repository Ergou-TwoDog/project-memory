"""L1: mechanical capture. Reproducible from Git; no model, no judgement.

Every value here is read from Git or the hook event. If Git cannot answer, the
answer is "nothing observed" — never a guess.
"""
import os
import subprocess

from .model import Observation

REFLOG_LIMIT = 32
GIT_TIMEOUT = 20
COMMIT_PREFIXES = ('commit:', 'commit (initial):', 'commit (amend):')


def project_dir(event, environ=None):
    env = os.environ if environ is None else environ
    raw = env.get('CLAUDE_PROJECT_DIR') or event.get('cwd')
    if type(raw) is not str or not raw:
        return None
    return os.path.abspath(raw)


def _git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    try:
        run = subprocess.run(['git', '-C', str(root), '--no-optional-locks', *args],
                             capture_output=True, env=env, timeout=GIT_TIMEOUT, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if run.returncode:
        return None
    if len(run.stdout) > 8 * 1048576:
        return None
    return run.stdout.decode('utf-8', errors='replace')


def head(root):
    out = _git(root, 'rev-parse', '--verify', 'HEAD')
    return out.strip() if out else None


def reflog(root, limit=REFLOG_LIMIT):
    out = _git(root, 'reflog', f'-{limit}', '--format=%H%x00%gs')
    if out is None:
        return None
    rows = []
    for line in out.splitlines():
        sha, _, subject = line.partition('\x00')
        if sha:
            rows.append((sha, subject))
    return rows


def changed_files(root, commit):
    out = _git(root, 'diff-tree', '--no-commit-id', '--name-only', '-r', '--root', commit)
    if out is None:
        return ()
    return tuple(sorted({line.strip() for line in out.splitlines() if line.strip()}))


def commit_message(root, commit):
    out = _git(root, 'log', '-1', '--format=%s', commit)
    return out.strip() if out else ''


def observe(store, event):
    """Return the observations this event produces. Empty means "nothing observed"."""
    name = event.get('hook_event_name')
    origin = f'hook:{name}'
    if name == 'SessionStart':
        return [Observation(kind='session_start', cwd=str(store.project), source=origin)]
    if name in ('SessionEnd', 'Stop'):
        return [Observation(kind='session_end', cwd=str(store.project), source=origin)]
    if name != 'PostToolUse' or event.get('tool_name') != 'Bash':
        return []
    return _commits(store)


def _commits(store):
    root = store.project
    current = head(root)
    if current is None:
        return []                       # unborn repository: nothing to observe, no error
    entries = reflog(root)
    if not entries:
        return []                       # no reflog yet (fresh repo): stay quiet
    top = entries[0][0]
    marker = store.observer()
    if marker is None or marker.get('reflog') != top or marker.get('head') != current:
        store.set_observer({'head': current, 'reflog': top})
    if marker is None or not marker.get('reflog'):
        return []                       # first sighting: baseline only
    shas = [sha for sha, _ in entries]
    if marker['reflog'] not in shas:
        return []                       # history rewritten: do not guess what is new
    fresh = entries[:shas.index(marker['reflog'])]
    result = []
    for sha, subject in reversed(fresh):        # oldest first
        if not subject.startswith(COMMIT_PREFIXES):
            continue                            # checkout/reset/rebase are not commits
        result.append(Observation(kind='commit', cwd=str(root), source='hook:PostToolUse',
                                  commit=sha, files_changed=changed_files(root, sha),
                                  message=commit_message(root, sha)))
    return result
