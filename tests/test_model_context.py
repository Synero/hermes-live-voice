"""Exercise model context without providers, a microphone, or an installed plugin."""
import ast
import asyncio
import importlib.util
import subprocess
from types import SimpleNamespace

import pytest

from test_plugin_api import PLUGIN_API, REPO, VENDOR, load_unit


def constants():
    tree = ast.parse(PLUGIN_API.read_text())
    names = {'_LANGUAGE_DIRECTIVE_DEFAULT', '_AGENT_INSTR', '_AGENT_INSTR_SKIP', '_VOICE_POLICY'}
    return {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in names}


def vendor_module(name):
    spec = importlib.util.spec_from_file_location(name, VENDOR / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_language_directive_default_file_and_user_override(tmp_path):
    ns = load_unit(PLUGIN_API, '_language_directive')
    ns.update(constants(), _DIRECTIVE_PATH=REPO / 'language_directive.txt')
    expected = ns['_language_directive']()
    assert expected == ns['_LANGUAGE_DIRECTIVE_DEFAULT']
    assert 'natural Chilean Spanish' in expected
    assert 'if they speak English, reply in English' in expected
    ns['_DIRECTIVE_PATH'] = tmp_path / 'directive.txt'
    assert ns['_language_directive']() == expected
    ns['_DIRECTIVE_PATH'].write_text('')
    assert ns['_language_directive']() == expected
    ns['_DIRECTIVE_PATH'].write_text('Mi preferencia personal: español.')
    assert ns['_language_directive']() == '\n\nMi preferencia personal: español.'


@pytest.mark.parametrize('profile', [None, 'example-bot'])
@pytest.mark.parametrize('allow_chat', [False, True])
def test_constructed_voice_prompts(profile, allow_chat):
    sections = {'PERSONA': 'Soy un bot de prueba.', 'MEMORY': 'El usuario vive en Chile.'}
    ns = load_unit(PLUGIN_API, '_mint_for')
    ns.update(constants())
    captured = {}
    ns.update(
        talk_tools=vendor_module('talk_tools'),
        _send_to_chat_tool=load_unit(PLUGIN_API, '_send_to_chat_tool')['_send_to_chat_tool'],
        _bot_identity_sections=lambda _: sections,
        talk_host=SimpleNamespace(host=lambda: SimpleNamespace(identity_sections=lambda: sections)),
        talk_identity=vendor_module('talk_identity'),
        talk_capabilities=SimpleNamespace(instruction_section=lambda: None),
        _language_directive=lambda: constants()['_LANGUAGE_DIRECTIVE_DEFAULT'],
        _bot_display_name=lambda _: 'Example Bot',
        talk_auth=SimpleNamespace(resolve_auth=lambda: SimpleNamespace(token='stub')),
        talk_config=SimpleNamespace(talk_model=lambda: 'stub'),
        talk_wire=SimpleNamespace(mint_ephemeral_session=lambda **kw: captured.update(kw)),
    )
    ns['_send_to_chat_tool'].__globals__['_SEND_TO_CHAT_DESC'] = 'Send work to the chat.'
    ns['_mint_for'](profile, 'marin', allow_chat)
    prompt = captured['instructions']
    assert ('VOICE + CHAT:' in prompt) == allow_chat
    if allow_chat:
        assert 'complete request phrased naturally in their language' in prompt
    assert 'LANGUAGE AND VOICE:' in prompt
    assert 'Do NOT delegate greetings' in prompt
    assert all(text in prompt for text in sections.values())
    assert 'VOZ' not in prompt and 'IDENTIDAD' not in prompt
    persona = load_unit(PLUGIN_API, '_codexlive_persona')
    persona.update(ns)
    prompt = persona['_codexlive_persona'](profile)
    assert 'VOICE: You are speaking' in prompt
    assert f'IDENTITY: You are {"Example Bot" if profile else "Luna"}' in prompt
    assert all(text in prompt for text in sections.values())


@pytest.mark.parametrize('mode', ['client', 'server'])
def test_codex_start_sends_english_delegation_instructions(mode):
    ns = load_unit(PLUGIN_API, '_codexlive_start')
    ns.update(constants())
    ns.update(_CL={'notifs': []}, _cl_ensure=lambda: None,
              _cl_thread_ensure=lambda: 'thread', _codexlive_persona=lambda _: 'persona',
              _talk_settings=lambda: {'delegation': mode})
    captured = {}

    def request(method, params, **kwargs):
        if method == 'thread/realtime/start':
            captured.update(params)
            ns['_CL']['notifs'].append({'method': 'thread/realtime/sdp', 'params': {'sdp': 'stub'}})
        return {}

    ns['_cl_request'] = request
    ns['_codexlive_start'](None, 'cove', 'stub')
    instruction = captured['realtimeStartInstructions']
    assert instruction.startswith('You are connected to a live voice session')
    if mode == 'client':
        assert '<realtime_delegation>' in instruction
        assert 'do NOT read files: reply with only the word: skip' in instruction
    else:
        assert 'carry it out with your tools' in instruction


def test_tool_timeout_and_dynamic_output():
    ns = load_unit(PLUGIN_API, 'run_tool')
    output = 'Resultado original en español.'

    async def wait_for(*args, **kwargs):
        if output is None:
            raise asyncio.TimeoutError
        return output

    ns['asyncio'] = SimpleNamespace(wait_for=wait_for, to_thread=lambda *args: None,
                                    TimeoutError=asyncio.TimeoutError)
    ns['talk_tools'] = SimpleNamespace(execute_talk_tool=lambda *args: None, TalkToolError=ValueError)

    class Request:
        async def json(self):
            return {'name': 'example', 'arguments': {}}

    assert asyncio.run(ns['run_tool'](Request()))['output'] == output
    output = None
    assert asyncio.run(ns['run_tool'](Request()))['output'].startswith('The tool example is still running;')
    tools = vendor_module('talk_tools')
    with pytest.raises(tools.TalkToolError, match="the voice tool 'example' is not available"):
        tools.execute_talk_tool('example', {})


@pytest.mark.parametrize('english', [False, True])
def test_desktop_model_messages(english):
    source = (REPO / 'desktop/plugin.js').read_text()
    # Evaluate the real delegation functions with only host/time/transport boundaries stubbed.
    delegation = source[source.index('const _voiceNote ='):source.index('// ── Barge-in')]
    preview = source[source.index('  const phrase ='):source.index('  const url = await new Promise', source.index('  const phrase ='))]
    script = r'''
const assert = require('node:assert/strict');
const EN = EN_VALUE;
const tr = (es, en) => EN ? en : es;
let _delegating = null, _delegBusy = false, _delegQueue = null;
let handoffMode = 'client';
const transcript = [], pushTranscript = () => {}, KEY_CHAT = 'chat';
let now = 1000000;
Date.now = () => now;
const sleep = async () => { now += 300000; };
const handlers = {};
let completion = 'Resultado en español.', submitted;
const host = {state: {focusedSessionId: 'session'}, onEvent: (name, cb) => {
  handlers[name] = cb; return () => { delete handlers[name]; };
}, request: async (method, body) => {
  submitted = body.text;
  if (completion instanceof Error) throw completion;
  if (completion === 'agent-error') handlers.error({message: 'fallo original'});
  else if (completion !== null) handlers['message.complete']({text: completion});
}};
const messages = [];
const dc = {send: data => messages.push(JSON.parse(data))};
const ctx = {storage: {get: () => '1'}};
const lastText = () => messages.at(-1).content[0].text;
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
DELEGATION
(async () => {
  assert.equal(await delegateToChat(''), 'Empty request.');
  _delegating = {text: 'revisa', at: now};
  assert.match(await delegateToChat('revisa'), /^That request is already/);
  _delegating = null;
  host.state.focusedSessionId = '';
  assert.match(await delegateToChat('revisa'), /^No conversation is open/);
  host.state.focusedSessionId = 'session';
  assert.equal(await delegateToChat('revisa mi archivo'), completion);
  assert.match(submitted, /^\[voice\] Reply briefly and naturally/);
  assert.ok(submitted.endsWith('\n\nrevisa mi archivo'));
  completion = new Error('fallo original');
  assert.equal(await delegateToChat('revisa'), 'Could not send to the chat: fallo original');
  completion = 'agent-error';
  assert.equal(await delegateToChat('revisa'), 'Agent error: fallo original');
  completion = '';
  assert.equal(await delegateToChat('revisa'), 'The agent finished without visible text.');
  completion = null;
  assert.match(await delegateToChat('revisa'), /^The agent is still working/);
  completion = 'Resultado en español.';
  await runVoiceTool(ctx, 'call', 'send_to_chat', {request: 'revisa'}, dc);
  assert.match(messages[0].item.content[0].text, /^\[The real agent is ALREADY/);
  assert.equal(messages[2].item.output, completion);
  await runVoiceTool({rest: async () => { throw new Error('fallo original'); }}, 'call', 'probe', {}, dc);
  assert.equal(messages.at(-2).item.output, 'The tool probe failed: fallo original');
  await runVoiceTool({rest: async () => ({output: completion})}, 'call', 'probe', {}, dc);
  assert.equal(messages.at(-2).item.output, completion);
  const msg = text => ({item: {id: 'item', content: [{text}]}});
  handleDelegation(ctx, msg('hola, ¿me escuchas?'), dc);
  assert.match(lastText(), /^The user was just chatting/);
  handleDelegation({storage: {get: () => '0'}}, msg('revisa mi archivo'), dc);
  assert.match(lastText(), /^I cannot run that/);
  _delegBusy = true;
  handleDelegation(ctx, msg('revisa mi archivo'), dc);
  assert.match(lastText(), /^A task is already in progress/);
  _delegQueue.at = now - 600001;
  _delegBusy = false;
  _runDelegation(ctx, dc, 'current', 'revisa');
  await flush();
  assert.match(lastText(), /^The queued task went stale/);
  assert.ok(messages.some(m => m.content?.[0]?.text === 'Task result (answer the user with this, briefly and naturally): ' + completion));
  assert.equal(_plainForVoice('Texto español. ```codigo```'), 'Texto español.  (code block omitted)');
  const realDelegate = delegateToChat;
  delegateToChat = async () => '';
  _runDelegation(ctx, dc, 'empty', 'revisa');
  await flush();
  assert.equal(lastText(), 'Task result (answer the user with this, briefly and naturally): The task could not be completed in the chat.');
  delegateToChat = realDelegate;
  // Preview prompt construction and both outbound formats; no media APIs run.
  for (const eng of ['codex', 'realtime']) {
    const voice = 'cove';
    PREVIEW
    const message = messages.at(eng === 'codex' ? -1 : -2);
    const text = (message.content || message.item.content)[0].text;
    assert.match(text, /^Say out loud exactly this sentence, adding nothing else:/);
    assert.ok(text.includes(EN ? 'Hi, this is voice cove' : 'Hola, esta es la voz cove'));
    assert.ok(!text.includes('Nacho'));
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
'''.replace('EN_VALUE', str(english).lower()).replace('DELEGATION', delegation).replace('PREVIEW', preview)
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
