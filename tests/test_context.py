"""上下文压缩回归：模拟摘要/API，所有归档均写入临时目录。"""
import copy
import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from mini_Ncode.context import ContextCompactor, block_type, json_value
from mini_Ncode import runner, subagent
from mini_Ncode.hooks import HookManager
from mini_Ncode.providers import LocalToolProvider
from mini_Ncode.registry import ToolRegistry
from mini_Ncode.skills import Skillloader


def text(value):
    return NS(type='text', text=value)


def call(name='check', id='call-1'):
    return NS(type='tool_use', name=name, id=id, input={})


def pair(id='call-1', output='result'):
    return [
        {'role': 'assistant', 'content': [call(id=id)]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': id, 'content': output}]},
    ]


def assert_pairs(test, messages):
    pending = set()
    for message in messages:
        content = message.get('content')
        blocks = content if isinstance(content, list) else []
        if pending:
            test.assertEqual(message['role'], 'user')
            ids = {b['tool_use_id'] for b in blocks if isinstance(b, dict) and b.get('type') == 'tool_result'}
            test.assertEqual(ids, pending)
            pending = set()
        else:
            test.assertFalse(any(block_type(b) == 'tool_result' for b in blocks))
        pending = {json_value(b)['id'] for b in blocks if block_type(b) == 'tool_use'}
    test.assertFalse(pending)


class FakeClient:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []
        self.messages = NS(create=self.create)

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return NS(content=value)


class ContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def compactor(self, client=None):
        return ContextCompactor(client, 'test', self.root / 'transcripts', self.root / 'outputs')

    def test_small_context_needs_no_api_or_files(self):
        c = self.compactor()
        messages = [{'role': 'user', 'content': 'hello'}]
        self.assertEqual(c.prepare(messages, 'hello'), messages)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_large_output_is_saved_and_original_history_is_unchanged(self):
        c = self.compactor()
        output = '完整输出' * 10000
        history = [{'role': 'user', 'content': 'read'}, *pair(output=output)]
        result = c.prepare(history, 'read')
        preview = result[-1]['content'][0]['content']
        self.assertLess(len(preview), 3000)
        self.assertEqual(c.persisted_output_path(preview).read_text(encoding='utf-8'), output)
        self.assertEqual(history[-1]['content'][0]['content'], output)
        assert_pairs(self, result)

    def test_reused_ids_do_not_overwrite_archives(self):
        c = self.compactor()
        first = c.save_output('../../same', 'first')
        second = c.save_output('../../same', 'second')
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_relative_to(c.tool_results_dir))
        self.assertEqual(first.read_text(encoding='utf-8'), 'first')

    def test_medium_outputs_also_obey_batch_budget(self):
        c = self.compactor()
        c.TOOL_RESULT_BATCH_CHAR_LIMIT = 5000
        results = [{'type': 'tool_result', 'tool_use_id': str(i), 'content': 'x' * 4000} for i in range(3)]
        c.tool_result_budget([{'role': 'user', 'content': results}])
        self.assertLessEqual(sum(len(b['content']) for b in results), 5000)
        self.assertEqual(len(list(c.tool_results_dir.glob('*.txt'))), 3)

    def test_micro_keeps_recent_and_unseen_results(self):
        c = self.compactor()
        history = [{'role': 'user', 'content': 'start'}]
        for i in range(6):
            history.extend(pair(str(i), str(i) * 4000))
        c.micro_compact(history, 0)
        for i in range(2):
            self.assertTrue(history[2 + i * 2]['content'][0]['content'].startswith('[Earlier'))
        for i in range(2, 6):
            self.assertEqual(history[2 + i * 2]['content'][0]['content'], str(i) * 4000)
        assert_pairs(self, history)

    def test_snip_keeps_pairs_and_archives_structured_blocks(self):
        c = self.compactor()
        history = [{'role': 'user', 'content': 'start'}]
        for i in range(36):
            history.extend(pair(str(i)))
        result = c.snip_compact(history, 'Do not delete files')
        self.assertLess(len(result), len(history))
        self.assertIn('Do not delete files', result[3]['content'])
        assert_pairs(self, result)
        records = [json.loads(line) for line in next(c.transcript_dir.glob('*.jsonl')).read_text(encoding='utf-8').splitlines()]
        self.assertEqual(records[1]['content'][0]['type'], 'tool_use')
        self.assertEqual(records[1]['content'][0]['id'], '0')
        self.assertEqual(len(records), len(history))

    def test_auto_summary_retains_current_request_and_latest_batch(self):
        client = FakeClient([text('Earlier work completed; do not delete files.')])
        c = self.compactor(client)
        c.CONTEXT_CHAR_LIMIT = 10000
        history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': 'old' * 1000} for i in range(8)]
        history.extend([{'role': 'user', 'content': 'Keep current constraints'}, *pair(output='NEW RESULT')])
        before = copy.deepcopy(history)
        result = c.prepare(history, 'Keep current constraints')
        self.assertIn('[Compacted]', result[0]['content'])
        self.assertIn('Keep current constraints', result[0]['content'])
        self.assertEqual(result[-1]['content'][0]['content'], 'NEW RESULT')
        self.assertLess(c.estimate_chars(result), c.CONTEXT_CHAR_LIMIT)
        self.assertEqual(history, before)
        assert_pairs(self, result)

    def test_summary_failure_and_empty_summary_do_not_replace_history(self):
        for response in [RuntimeError('summary unavailable'), []]:
            with self.subTest(response=response):
                c = self.compactor(FakeClient(response))
                c.CONTEXT_CHAR_LIMIT = 2000
                history = [{'role': 'user', 'content': 'old' * 2000}, *pair()]
                before = copy.deepcopy(history)
                with self.assertLogs('mini_Ncode.context', level='WARNING'):
                    result = c.prepare(history, 'continue')
                self.assertEqual(result, before)
                self.assertEqual(history, before)

    def test_disk_failure_retains_original_history(self):
        c = self.compactor()
        c.tool_results_dir.write_text('not a directory')
        history = [{'role': 'user', 'content': 'read'}, *pair(output='x' * 40000)]
        with self.assertLogs('mini_Ncode.context', level='WARNING'):
            self.assertIs(c.prepare(history, 'read'), history)
        self.assertEqual(len(history[-1]['content'][0]['content']), 40000)

    def test_manual_compaction_refuses_unanswered_call(self):
        c = self.compactor()
        with self.assertRaisesRegex(ValueError, '工具结果'):
            c.compact_history([{'role': 'assistant', 'content': [call()]}], 'continue')

    def test_overflow_recovers_once_and_keeps_tool_pair(self):
        client = FakeClient(RuntimeError('prompt_too_long'), [text('Short summary')], [text('Done')])
        c = self.compactor(client)
        history = [{'role': 'user', 'content': 'old' * 2000}, *pair()]
        response = c.create_response(history, 'continue', max_tokens=100)
        self.assertEqual(response.content[0].text, 'Done')
        self.assertEqual(len(client.calls), 3)
        assert_pairs(self, history)

    def test_second_overflow_and_unrelated_errors_are_not_retried(self):
        for responses, count in [
            ([RuntimeError('maximum context length'), [text('summary')], RuntimeError('too many tokens')], 3),
            ([RuntimeError('invalid api key')], 1),
        ]:
            with self.subTest(count=count):
                client = FakeClient(*responses)
                c = self.compactor(client)
                with self.assertRaises(RuntimeError):
                    c.create_response([{'role': 'user', 'content': 'old' * 2000}, *pair()], 'continue', max_tokens=100)
                self.assertEqual(len(client.calls), count)

    def test_recovery_summary_failure_returns_original_api_error(self):
        original = RuntimeError('prompt_too_long')
        c = self.compactor(FakeClient(original, RuntimeError('summary failed')))
        history = [{'role': 'user', 'content': 'old' * 2000}, *pair()]
        before = copy.deepcopy(history)
        with self.assertLogs('mini_Ncode.context', level='WARNING'), self.assertRaises(RuntimeError) as caught:
            c.create_response(history, 'continue', max_tokens=100)
        self.assertIs(caught.exception, original)
        self.assertEqual(history, before)


class ContextIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(runner, 'load_memories', return_value=''))
        self.extract = self.stack.enter_context(patch.object(runner, 'extract_memories', return_value=0))
        self.stack.enter_context(patch.object(runner, 'read_memory_index', return_value=''))
        self.provider = LocalToolProvider()
        self.provider.tool(name='compact')(lambda: 'Compaction requested')
        self.registry = ToolRegistry()
        self.registry.add_provider(self.provider)
        self.hooks = HookManager()

    async def run_loop(self, client, messages):
        await self.registry.refresh()
        c = ContextCompactor(client, 'test', self.root / 'transcripts', self.root / 'outputs')
        await runner.agent_loop(messages, client=client, model='test', tool_registry=self.registry,
                                skills=Skillloader(self.root / 'skills'), hooks=self.hooks,
                                compactor=c, active_request='continue')

    async def test_manual_compact_waits_for_entire_batch(self):
        handler = Mock(return_value='file written')
        self.provider.tool(name='write')(handler)
        client = FakeClient([call('compact', 'c'), call('write', 'w')], [text('summary')], [text('Done')])
        history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': 'history' * 500} for i in range(6)]
        history.append({'role': 'user', 'content': 'continue'})
        await self.run_loop(client, history)
        handler.assert_called_once()
        self.assertEqual(len(client.calls), 3)
        self.assertIn('[Compacted]', history[0]['content'])
        assert_pairs(self, history)
        records = [json.loads(line) for line in next((self.root / 'transcripts').glob('*.jsonl')).read_text(encoding='utf-8').splitlines()]
        self.assertEqual({b['tool_use_id'] for b in records[-1]['content']}, {'c', 'w'})
        memory_history = self.extract.call_args.args[0]
        self.assertFalse(any(ContextCompactor.is_generated_message(m) for m in memory_history))
        self.assertTrue(any(m['content'] == 'continue' for m in memory_history))

    async def test_blocked_compact_does_not_summarize(self):
        self.hooks.register('PreToolUse', lambda block: 'blocked')
        client = FakeClient([call('compact')], [text('Done')])
        messages = [{'role': 'user', 'content': 'continue'}]
        await self.run_loop(client, messages)
        self.assertEqual(len(client.calls), 2)
        self.assertFalse((self.root / 'transcripts').exists())

    async def test_manual_summary_failure_returns_tool_error_and_continues(self):
        client = FakeClient([call('compact')], RuntimeError('no summary'), [text('Done')])
        messages = [{'role': 'user', 'content': 'history' * 1000}, {'role': 'assistant', 'content': [text('old reply')]}, {'role': 'user', 'content': 'continue'}]
        with self.assertLogs('mini_Ncode.runner', level='WARNING'):
            await self.run_loop(client, messages)
        self.assertTrue(messages[-2]['content'][0]['is_error'])
        self.assertIn('original history retained', messages[-2]['content'][0]['content'])
        self.assertEqual(messages[0]['content'], 'history' * 1000)
        assert_pairs(self, messages)

    async def test_subagent_also_persists_large_results(self):
        provider = LocalToolProvider()
        provider.tool(name='read_file')(lambda: 'x' * 40000)
        client = FakeClient([call('read_file')], [text('summary')])
        c = ContextCompactor(client, 'test', self.root / 'transcripts', self.root / 'outputs')
        with patch.object(subagent, 'ContextCompactor', return_value=c), redirect_stdout(io.StringIO()):
            result = subagent.run_subagent('read', client=client, model='test', local_provider=provider)
        self.assertEqual(result, 'summary')
        preview = client.calls[1]['messages'][-1]['content'][0]['content']
        self.assertTrue(preview.startswith('<persisted-output>'))
        self.assertEqual(len(c.persisted_output_path(preview).read_text()), 40000)


if __name__ == '__main__':
    unittest.main()
