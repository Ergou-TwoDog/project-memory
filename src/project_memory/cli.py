"""Operator commands. Adopting an intention is deliberately not an MCP tool.

The model proposes; only this CLI turns a candidate into a commitment. That is
the one place a human is required, because intention has no ground truth to
check against.
"""
import argparse
import os

from .store import Store, MemoryError, dumps
from . import intent as intent_layer
from .deliver import render


def main(argv=None):
    parser = argparse.ArgumentParser(prog='project-memory', description=__doc__)
    parser.add_argument('--project', default=os.environ.get('CLAUDE_PROJECT_DIR'),
                        help='project root (defaults to $CLAUDE_PROJECT_DIR)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='create .project-memory once (required before hooks do anything)')
    sub.add_parser('status', help='show the digest a session would receive')
    adopt = sub.add_parser('adopt', help='adopt a candidate intention (human decision)')
    adopt.add_argument('intent_id')
    drop = sub.add_parser('drop', help='drop a candidate intention (human decision)')
    drop.add_argument('intent_id')
    args = parser.parse_args(argv)
    if not args.project:
        parser.exit(2, 'a project path is required (--project or CLAUDE_PROJECT_DIR)\n')
    try:
        if args.command == 'init':
            store = Store.init(args.project)
            print(dumps({'initialized': str(store.root)}))
            return 0
        store = Store.open(args.project)
        if args.command == 'status':
            print(render(store))
        else:
            status = 'adopted' if args.command == 'adopt' else 'dropped'
            print(dumps(intent_layer.decide(store, args.intent_id, status).to_dict()))
        return 0
    except (MemoryError, OSError) as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
