"""不请求模型、不连接 MCP，不修改项目中的记忆文件。"""
import asyncio
import copy
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from mini_Ncode import app, memory, memory_store, runner
from mini_Ncode.config import Settings
from mini_Ncode.hooks import HookManager, create_hooks
from mini_Ncode.providers import LocalToolProvider, MCPToolProvider
from mini_Ncode.registry import ToolRegistry
from mini_Ncode.skills import Skillloader
from mini_Ncode.todo import TodoManager
from mini_Ncode.tools import create_local_provider


def text(value):
    return NS(type="text", text=value)


def call(name, arguments=None, id="tool-1"):
    return NS(type="tool_use", name=name, input=arguments or {}, id=id)


class FakeClient:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []
        self.messages = NS(create=self.create)

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return NS(content=next(self.responses))


class TodoAndMemoryTests(unittest.TestCase):
    def test_todo_invalid_update_keeps_previous_state(self):
        todo = TodoManager()
        todo.update('[{"content":"first","status":"in_progress"}]')
        with self.assertRaises(ValueError):
            todo.update([
                {"content": "a", "status": "in_progress"},
                {"content": "b", "status": "in_progress"},
            ])
        self.assertEqual(todo.items, [{"content": "first", "status": "in_progress"}])

    def test_memory_round_trip_and_path_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(memory_store, WORKDIR=root, MEMORY_DIR=root / '.memory',
                                MEMORY_INDEX=root / '.memory' / 'MEMORY.md'):
                path = memory_store.write_memory_file("Editor", "user", "Editor preference", "Use Vim")
                self.assertIn("Editor", memory_store.read_memory_index())
                records = memory_store.list_memory_files()
                self.assertEqual(records[0]['body'], 'Use Vim')
                self.assertEqual(records[0]['filename'], path.name)
                self.assertIsNone(memory_store.read_memory_file('../outside.md'))

    def test_temporary_memory_is_not_persisted(self):
        candidate = dict(name="Temporary", type="project", scope="persistent",
                         description="this session only", body="Use this directory")
        self.assertFalse(memory_store.should_store_memory(candidate, []))

    def test_memory_selection_uses_injected_client(self):
        records = [dict(name="Editor", description="Preferred editor", filename="editor.md")]
        client = FakeClient([text('[0]')])
        with patch.object(memory, 'list_memory_files', return_value=records):
            selected = memory.select_relevant_memories(
                [{'role': 'user', 'content': 'Which editor?'}], client=client, model='test',
            )
        self.assertEqual(selected, ['editor.md'])
        self.assertEqual(client.calls[0]['model'], 'test')


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_local_tools_registered_and_session_state_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            skills = Skillloader(Path(directory))
            first, second = TodoManager(), TodoManager()
            provider = create_local_provider(client=None, model='test', skills=skills, todo=first)
            other = create_local_provider(client=None, model='test', skills=skills, todo=second)
            registry = ToolRegistry()
            registry.add_provider(provider)
            await registry.refresh()
            self.assertEqual({s['name'] for s in registry.to_llm()}, {
                'bash', 'read_file', 'write_file', 'edit_file', 'glob', 'todo_write', 'load_skill', 'task',
            })
            with redirect_stdout(io.StringIO()):
                result = await registry.invoke('todo_write', {'todos': [{'content': 'Check', 'status': 'pending'}]})
            self.assertTrue(result.ok)
            self.assertIn('Check', result.content)
            self.assertEqual(second.items, [])
            self.assertIsNot(provider, other)

    async def test_file_and_skill_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / '.skills' / 'demo'
            skill_dir.mkdir(parents=True)
            (skill_dir / 'SKILL.md').write_text('---\nname: demo\ndescription: example\n---\nGuide', encoding='utf-8')
            provider = create_local_provider(client=None, model='test', skills=Skillloader(root / '.skills'), todo=TodoManager())
            with patch('mini_Ncode.tools.WORKDIR', root):
                await provider.call_tool('write_file', {'path': 'test.txt', 'content': 'before'})
                await provider.call_tool('edit_file', {'path': 'test.txt', 'old_text': 'before', 'new_text': 'after'})
                result = await provider.call_tool('read_file', {'path': 'test.txt'})
                matches = await provider.call_tool('glob', {'pattern': '*.txt'})
            self.assertEqual(result.content, 'after')
            self.assertIn('test.txt', matches.content)
            loaded = await provider.call_tool('load_skill', {'name': 'demo'})
            self.assertEqual(loaded.content, 'Guide')

    async def test_mcp_name_mapping_and_cleanup(self):
        class MCPClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

        client = MCPClient()
        client.list_tools = AsyncMock(return_value=[NS(name='weather', description='Weather', inputSchema={})])
        client.call_tool = AsyncMock(return_value=NS(isError=False, content=[{'type': 'text', 'text': 'sunny'}]))
        registry = ToolRegistry()
        registry.add_provider(MCPToolProvider(client, server_name='gd'))
        await registry.refresh()
        result = await registry.invoke('gd_weather', {'city': 'Beijing'})
        client.call_tool.assert_awaited_once_with('weather', {'city': 'Beijing'})
        self.assertTrue(result.ok)
        await registry.close()
        self.assertTrue(client.closed)

    async def test_task_uses_separate_context(self):
        client = FakeClient([text('Subtask finished')])
        with tempfile.TemporaryDirectory() as directory:
            provider = create_local_provider(client=client, model='test', skills=Skillloader(Path(directory)), todo=TodoManager())
            with redirect_stdout(io.StringIO()):
                result = await provider.call_tool('task', {'prompt': 'Inspect one file'})
        self.assertEqual(result.content, 'Subtask finished')
        self.assertEqual(client.calls[0]['messages'], [{'role': 'user', 'content': 'Inspect one file'}])
        self.assertNotIn('task', {tool['name'] for tool in client.calls[0]['tools']})


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.skills = Skillloader(Path(directory))
        self.extract = self.stack.enter_context(patch.object(runner, 'extract_memories', return_value=0))
        self.stack.enter_context(patch.object(runner, 'load_memories', return_value=''))
        self.stack.enter_context(patch.object(runner, 'read_memory_index', return_value=''))
        self.hooks = HookManager()
        self.registry = ToolRegistry()
        self.provider = LocalToolProvider()
        self.registry.add_provider(self.provider)
        self.messages = [{'role': 'user', 'content': 'Run task'}]

    async def run_loop(self, client):
        await self.registry.refresh()
        await runner.agent_loop(self.messages, client=client, model='test',
                                tool_registry=self.registry, skills=self.skills, hooks=self.hooks)

    async def test_tool_result_then_final_response(self):
        self.provider.tool(name='add')(lambda a, b: a + b)
        client = FakeClient([call('add', {'a': 2, 'b': 3})], [text('The answer is 5')])
        await self.run_loop(client)
        self.assertEqual([m['role'] for m in self.messages], ['user', 'assistant', 'user', 'assistant'])
        self.assertEqual(self.messages[2]['content'][0]['content'], '5')
        self.assertEqual(self.messages[-1]['content'][0].text, 'The answer is 5')
        self.extract.assert_called_once_with(self.messages, client=client, model='test')

    async def test_blocked_tool_is_not_executed(self):
        handler = Mock(return_value='should not run')
        self.provider.tool(name='write_file')(handler)
        self.hooks.register('PreToolUse', lambda block: 'Permission denied')
        client = FakeClient([call('write_file')], [text('Cancelled')])
        await self.run_loop(client)
        handler.assert_not_called()
        self.assertEqual(self.messages[2]['content'][0]['content'], 'Permission denied')

    async def test_todo_reminder_after_three_tool_rounds(self):
        self.provider.tool(name='check')(lambda: 'ok')
        client = FakeClient([call('check')], [call('check')], [call('check')], [text('Done')])
        await self.run_loop(client)
        self.assertEqual(self.messages[6]['content'][-1], {
            'type': 'text', 'text': '<reminder>Update your todos.</reminder>',
        })

    async def test_todo_update_resets_reminder_counter(self):
        self.provider.tool(name='check')(lambda: 'ok')
        self.provider.tool(name='todo_write')(lambda: 'Updated')
        client = FakeClient([call('check')], [call('check')], [call('todo_write')], [call('check')], [text('Done')])
        await self.run_loop(client)
        for message in self.messages:
            if message['role'] == 'user' and isinstance(message['content'], list):
                self.assertFalse(any(block.get('type') == 'text' for block in message['content']))


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_initializes_and_closes_without_network(self):
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        settings = Settings(model='test', api_key='test', base_url=None, amap_api_key=None)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(app, 'load_settings', return_value=settings))
            stack.enter_context(patch.object(app, 'MEMORY_DIR', Path(directory) / '.memory'))
            stack.enter_context(patch.object(app, 'SKILL_DIR', Path(directory) / '.skills'))
            rebuild = stack.enter_context(patch.object(app, 'rebuild_memory_index'))
            stack.enter_context(patch('anthropic.Anthropic', return_value=client))
            stack.enter_context(patch('builtins.input', return_value='q'))
            stack.enter_context(redirect_stdout(io.StringIO()))
            await app.main()
        rebuild.assert_called_once()
        client.__exit__.assert_called_once()
        client.messages.create.assert_not_called()

    async def test_default_hook_registrations_do_not_accumulate(self):
        first, second = create_hooks(), create_hooks()
        callback = Mock()
        first.register('Stop', callback)
        with redirect_stdout(io.StringIO()):
            second.trigger('Stop', [])
        callback.assert_not_called()


class ImportTests(unittest.TestCase):
    def test_import_without_settings_or_external_clients(self):
        # A fresh process catches accidental import-time initialization.
        env = {k: v for k, v in os.environ.items() if k not in {
            'MODEL', 'MODEL_ID', 'LLM_API_KEY', 'ANTHROPIC_API_KEY', 'AMAP_MAPS_API_KEY',
        }}
        root = Path(__file__).resolve().parents[1]
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "sys.modules['anthropic'] = None; sys.modules['fastmcp'] = None; "
            "sys.modules['dotenv'] = None; "
            "import mini_Ncode; import mini_Ncode.app; "
            "from pathlib import Path; assert list(Path.cwd().iterdir()) == []"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-B', '-c', script, str(root)],
                                    cwd=directory, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
