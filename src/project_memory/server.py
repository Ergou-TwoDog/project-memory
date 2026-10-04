"""MCP tool surface. Read-only except propose_intent, which only writes candidates.

The MCP process is spawned with cwd = the project directory, so the working
directory is the project identity here (the process is not given
CLAUDE_PROJECT_DIR the way hook processes are).
"""
import os
import sys
import traceback

from .store import Store, MemoryError, dumps
from . import intent as intent_layer


def project_root():
    return os.path.abspath(os.environ.get('CLAUDE_PROJECT_DIR') or os.getcwd())


def _reply(payload):
    return dumps(payload, ensure_ascii=True)


def _guard(action):
    try:
        store = Store.open(project_root())
    except MemoryError as exc:
        return _reply({'status': 'uninitialized', 'issue': str(exc),
                       'hint': 'run: python -m project_memory.cli init'})
    try:
        return _reply(action(store))
    except MemoryError as exc:
        return _reply({'status': 'error', 'issue': str(exc)})


def _recall(store, query, limit):
    needle = query.lower()
    limit = max(1, min(int(limit), 100))
    observations = [o.to_dict() for o in store.observations()
                    if needle in (o.message or '').lower() or needle in o.kind
                    or any(needle in f.lower() for f in o.files_changed)]
    intents = [i.to_dict() for i in store.intents()
               if needle in i.text.lower() or needle in i.why.lower()]
    return {'query': query, 'observations': observations[-limit:], 'intents': intents[-limit:],
            'note': 'Candidates with origin=llm are unconfirmed; do not treat them as decided.'}


def _timeline(store, limit):
    limit = max(1, min(int(limit), 100))
    return {'observations': [o.to_dict() for o in store.observations(limit=limit)],
            'note': 'Observations are re-derivable from Git. Raw material for reasoning, not conclusions.'}


def _intents(store, include_candidates):
    rows = store.intents()
    if not include_candidates:
        rows = tuple(i for i in rows if i.status != 'candidate')
    return {'intents': [i.to_dict() for i in rows],
            'note': 'origin=user and status=adopted means a human adopted it. origin=llm is only a candidate.'}


def _propose(store, text, why, refs):
    parsed = tuple(r.strip() for r in refs.split(',') if r.strip()) if refs else ()
    intent = intent_layer.propose(store, text, why, parsed)
    return {'intent': intent.to_dict(), 'formal': False,
            'note': 'Saved as a candidate. A human must adopt it before it is a commitment.'}


def create_server():
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    server = FastMCP('project-memory', instructions=(
        'Project memory. Observations are facts re-derivable from Git; intentions carry an origin. '
        'A candidate (origin=llm) is never a premise for a decision. Memory covers only what was '
        'observed: absence of a record is not proof that nothing happened.'), log_level='ERROR')
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

    @server.tool(annotations=read)
    def recall(query: str, limit: int = 20) -> str:
        """Search recorded observations and intentions by substring; returns raw records with origins."""
        return _guard(lambda s: _recall(s, query, limit))

    @server.tool(annotations=read)
    def timeline(limit: int = 20) -> str:
        """Recent mechanical observations (commits/sessions) as raw material for reasoning."""
        return _guard(lambda s: _timeline(s, limit))

    @server.tool(annotations=read)
    def intents(include_candidates: bool = True) -> str:
        """List intentions. Candidates are unconfirmed; adopted ones were confirmed by a human."""
        return _guard(lambda s: _intents(s, include_candidates))

    @server.tool(annotations=write)
    def propose_intent(text: str, why: str, refs: str = '') -> str:
        """Propose a candidate intention (comma-separated commit refs allowed). Never self-adopting."""
        return _guard(lambda s: _propose(s, text, why, refs))

    return server


def main():
    try:
        create_server().run(transport='stdio')
    except Exception:
        print(f'project-memory startup failed: {traceback.format_exc(limit=1)}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
