import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from project_memory import capture, deliver, intent as intent_layer, hook  # noqa: E402
from project_memory.model import Intent, MemoryError, Observation  # noqa: E402
from project_memory.store import Store  # noqa: E402


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    run = subprocess.run(['git', '-C', str(root), *args], capture_output=True, env=env)
    if run.returncode:
        raise RuntimeError(run.stderr.decode('utf-8', errors='replace'))
    return run.stdout.decode('utf-8', errors='replace').strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pm test ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        git(self.root, 'init', '-b', 'main')
        for key, value in (('user.name', 'Test'), ('user.email', 'test@example.invalid'),
                           ('core.autocrlf', 'false')):
            git(self.root, 'config', key, value)

    def commit(self, name, message):
        (self.root / name).write_text(message + '\n', encoding='utf-8')
        git(self.root, 'add', name)
        git(self.root, 'commit', '-m', message)
        return git(self.root, 'rev-parse', 'HEAD')

    def event(self, name='PostToolUse', **extra):
        return {'hook_event_name': name, 'tool_name': 'Bash', 'cwd': str(self.root), **extra}


class StoreTests(Base):
    def test_init_and_open(self):
        with self.assertRaisesRegex(MemoryError, 'not initialized'):
            Store.open(self.root)
        store = Store.init(self.root)
        self.assertTrue((self.root / '.project-memory').is_dir())
        self.assertEqual(Store.open(self.root).root, store.root)
        self.assertEqual(Store.init(self.root).root, store.root)   # idempotent

    def test_observation_and_intent_roundtrip(self):
        store = Store.init(self.root)
        store.add_observation(Observation(kind='commit', cwd=str(self.root), source='test',
                                          commit='a' * 40, files_changed=['x.py'], message='hello'))
        rows = store.observations()
        self.assertEqual([o.message for o in rows], ['hello'])
        self.assertEqual(rows[0].files_changed, ('x.py',))

    def test_new_intents_must_be_llm_candidates(self):
        store = Store.init(self.root)
        with self.assertRaisesRegex(MemoryError, 'undecided candidates'):
            store.add_intent(Intent(text='t', why='w', origin='user'))

    def test_decide_transitions_and_is_one_shot(self):
        store = Store.init(self.root)
        candidate = intent_layer.propose(store, '收敛到 X', '因为 Y', ['b' * 40])
        self.assertEqual((candidate.status, candidate.origin), ('candidate', 'llm'))
        adopted = intent_layer.decide(store, candidate.id, 'adopted')
        self.assertEqual((adopted.status, adopted.origin), ('adopted', 'user'))
        self.assertIsNotNone(adopted.decided_at)
        self.assertTrue(intent_layer.is_commitment(store.intents()[0]))
        with self.assertRaisesRegex(MemoryError, 'already'):
            intent_layer.decide(store, candidate.id, 'dropped')

    def test_strict_json_rejects_duplicate_keys(self):
        store = Store.init(self.root)
        (store.root / 'observations.jsonl').write_text('{"kind":"commit","kind":"commit"}\n', encoding='utf-8')
        with self.assertRaisesRegex(MemoryError, 'duplicate JSON key'):
            store.observations()


class CaptureTests(Base):
    def test_unborn_repository_is_quiet(self):
        store = Store.init(self.root)
        self.assertEqual(capture.observe(store, self.event()), [])      # no HEAD: no crash, no record

    def test_first_commit_on_a_fresh_repository_is_recorded(self):
        """An unborn repo must baseline at 'unborn', not at the first commit --
        otherwise that commit is silently swallowed."""
        store = Store.init(self.root)
        self.assertEqual(capture.observe(store, self.event()), [])
        self.assertIsNotNone(store.observer())          # baseline recorded while still unborn
        sha = self.commit('a.txt', '第一条')
        rows = capture.observe(store, self.event())
        self.assertEqual([o.commit for o in rows], [sha])
        self.assertEqual(rows[0].message, '第一条')

    def test_first_sighting_baselines_then_records_new_commits(self):
        self.commit('a.txt', 'first')
        store = Store.init(self.root)
        self.assertEqual(capture.observe(store, self.event()), [])      # baseline only
        self.assertIsNotNone(store.observer())
        sha = self.commit('b.txt', 'second')
        rows = capture.observe(store, self.event())
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].kind, rows[0].commit, rows[0].message), ('commit', sha, 'second'))
        self.assertEqual(rows[0].files_changed, ('b.txt',))
        store.add_observation(rows[0])
        self.assertEqual(capture.observe(store, self.event()), [])      # idempotent

    def test_checkout_does_not_produce_a_commit_observation(self):
        self.commit('a.txt', 'first')
        store = Store.init(self.root)
        capture.observe(store, self.event())
        git(self.root, 'checkout', '-b', 'other')
        self.assertEqual(capture.observe(store, self.event()), [])

    def test_non_bash_and_session_events(self):
        store = Store.init(self.root)
        self.assertEqual(capture.observe(store, self.event(tool_name='Read')), [])
        started = capture.observe(store, self.event(name='SessionStart'))
        self.assertEqual([o.kind for o in started], ['session_start'])


class DeliverTests(Base):
    def test_render_marks_candidates_and_unknown(self):
        store = Store.init(self.root)
        sha = self.commit('a.txt', 'first')
        store.add_observation(Observation(kind='commit', cwd=str(self.root), source='t',
                                          commit=sha, message='first'))
        intent_layer.propose(store, '做 X', '因为 Y', [sha])
        text = deliver.render(store)
        self.assertIn('[候选]', text)
        self.assertIn('未知', text)
        self.assertIn('不得当作已定方向', text)

    def test_budget_is_explicit_and_bounded(self):
        store = Store.init(self.root)
        for index in range(50):
            store.add_observation(Observation(kind='commit', cwd=str(self.root), source='t',
                                              commit=f'{index:040d}', message='x' * 100))
        text = deliver.render(store, budget=400)
        self.assertLessEqual(len(text), 500)
        self.assertIn('超出预算', text)

    def test_prompt_triggering(self):
        store = Store.init(self.root)
        self.assertIsNone(deliver.for_prompt(store, 'please refactor the parser'))
        self.assertIn('project-memory', deliver.for_prompt(store, '这个项目最近的状态是什么'))


class HookTests(Base):
    def test_silent_when_uninitialized(self):
        self.commit('a.txt', 'first')
        self.assertIsNone(hook.handle(self.event()))          # never creates the directory
        self.assertFalse((self.root / '.project-memory').exists())

    def test_session_start_injects_and_posttooluse_captures(self):
        self.commit('a.txt', 'first')
        store = Store.init(self.root)
        capture.observe(store, self.event())                  # baseline
        self.commit('b.txt', 'second')
        self.assertIsNone(hook.handle(self.event()))          # capture: no stdout payload
        self.assertEqual(len(store.observations()), 1)
        payload = hook.handle(self.event(name='SessionStart'))
        self.assertIn('hookSpecificOutput', payload)
        self.assertIn('project-memory', payload['hookSpecificOutput']['additionalContext'])
        self.assertEqual([o.kind for o in store.observations()], ['commit', 'session_start'])

    def test_non_git_directory_is_ignored(self):
        outside = Path(self.temp.name) / 'plain'
        outside.mkdir()
        self.assertIsNone(hook.handle({'hook_event_name': 'SessionStart', 'cwd': str(outside)}))

    def test_stdout_is_ascii_safe(self):
        """Hook output crosses a subprocess pipe; a Windows console codepage would
        otherwise mangle non-ASCII before Claude Code ever sees it."""
        self.commit('a.txt', 'first')
        store = Store.init(self.root)
        capture.observe(store, self.event())
        self.commit('b.txt', '加入笔记')
        for observation in capture.observe(store, self.event()):
            store.add_observation(observation)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root),
                   PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
        run = subprocess.run([sys.executable, '-B', '-m', 'project_memory.hook'],
                             input=b'{"hook_event_name":"SessionStart"}',
                             capture_output=True, env=env)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(all(byte < 128 for byte in run.stdout), run.stdout[:300])
        text = json.loads(run.stdout.decode('utf-8'))['hookSpecificOutput']['additionalContext']
        self.assertIn('加入笔记', text)


class ServerTests(Base):
    def test_tool_surface(self):
        import asyncio
        from project_memory.server import create_server
        tools = asyncio.run(create_server().list_tools())
        names = {t.name for t in tools}
        self.assertEqual(names, {'recall', 'timeline', 'intents', 'propose_intent'})
        annotations = {t.name: t.annotations for t in tools}
        self.assertTrue(annotations['timeline'].readOnlyHint)
        self.assertFalse(annotations['propose_intent'].readOnlyHint)


if __name__ == '__main__':
    unittest.main()
