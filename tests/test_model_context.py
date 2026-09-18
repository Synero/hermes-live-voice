"""Exercise model context without providers, a microphone, or an installed plugin."""
import ast
import asyncio
import importlib.util
import subprocess
from types import SimpleNamespace

import pytest

from test_plugin_api import PLUGIN_API, REPO, VENDOR, load_unit


@pytest.mark.parametrize('localized', [False, True])
@pytest.mark.parametrize('language', ['en', 'es'])
def test_tool_adapter_signature_compatibility(localized, language):
    ns = load_unit(PLUGIN_API, 'run_tool')
    calls = []

    def legacy(name, arguments):
        calls.append((name, arguments))
        return 'Original tool output'

    def localized_tool(name, arguments, language='es'):
        calls.append((name, arguments, language))
        return 'Original tool output'

    ns['_voice_gate_route'] = _no_voice_gate
    ns['talk_tools'] = SimpleNamespace(
        execute_talk_tool=localized_tool if localized else legacy,
        TalkToolError=ValueError,
        **({'SUPPORTS_LANGUAGE': True} if localized else {}),
    )

    class Request:
        async def json(self):
            return {'name': 'example', 'arguments': {}, 'language': language}

    assert asyncio.run(ns['run_tool'](Request()))['output'] == 'Original tool output'
    assert calls == [('example', {}, language) if localized else ('example', {})]


def constants():
    tree = ast.parse(PLUGIN_API.read_text())
    names = {'_LANGUAGE_DIRECTIVE_DEFAULT', '_AGENT_INSTR', '_AGENT_INSTR_SKIP', '_VOICE_POLICY'}
    result = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in names}
    result['_LANGUAGE_DIRECTIVE_BUNDLE'] = '\n\n'.join(v.strip() for v in result['_LANGUAGE_DIRECTIVE_DEFAULT'].values())
    policy = load_unit(PLUGIN_API, '_voice_policy')
    policy.update(result)
    result['_voice_policy'] = policy['_voice_policy']
    return result


def vendor_module(name):
    spec = importlib.util.spec_from_file_location(name, VENDOR / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _no_voice_gate(*_args, **_kwargs):
    """Gate apagado: la ruta /tool sigue con su dispatch normal."""
    return None


@pytest.mark.parametrize('language', ['es', 'en'])
def test_language_directive_default_file_and_user_override(tmp_path, language):
    ns = load_unit(PLUGIN_API, '_language_directive')
    ns.update(constants(), _DIRECTIVE_PATH=REPO / 'language_directive.txt')
    expected = ns['_language_directive'](language)
    assert expected == ns['_LANGUAGE_DIRECTIVE_DEFAULT'][language]
    assert ('natural Chilean Spanish' if language == 'en' else 'español natural de Chile') in expected
    assert ('if they speak English, reply in English' if language == 'en' else 'si te habla en inglés, en inglés') in expected
    ns['_DIRECTIVE_PATH'] = tmp_path / 'directive.txt'
    assert ns['_language_directive'](language) == expected
    ns['_DIRECTIVE_PATH'].write_text('')
    assert ns['_language_directive'](language) == expected
    custom = '  Mi preferencia personal: español.\n'
    ns['_DIRECTIVE_PATH'].write_text(custom)
    assert ns['_language_directive'](language) == '\n\n' + custom
    for legacy_default in ns['_LANGUAGE_DIRECTIVE_DEFAULT'].values():
        ns['_DIRECTIVE_PATH'].write_text(legacy_default.strip() + '\n')
        assert ns['_language_directive'](language) == expected


@pytest.mark.parametrize('profile', [None, 'example-bot'])
@pytest.mark.parametrize('allow_chat', [False, True])
@pytest.mark.parametrize('language', [None, 'es', 'en'])
def test_constructed_voice_prompts(profile, allow_chat, language):
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
        _DIRECTIVE_PATH=REPO / 'language_directive.txt',
        _bot_display_name=lambda _: 'Example Bot',
        talk_auth=SimpleNamespace(resolve_auth=lambda: SimpleNamespace(token='stub')),
        talk_config=SimpleNamespace(talk_model=lambda: 'stub'),
        talk_wire=SimpleNamespace(mint_ephemeral_session=lambda **kw: captured.update(kw)),
        jev_gate=SimpleNamespace(env_key=lambda: ""),
    )
    directive = load_unit(PLUGIN_API, '_language_directive')
    directive.update(ns)
    ns['_language_directive'] = directive['_language_directive']
    ns['_send_to_chat_tool'].__globals__['_SEND_TO_CHAT_DESC'] = 'Send work to the chat.'
    ns['_mint_for'](profile, 'marin', allow_chat, language)
    prompt = captured['instructions']
    assert (('VOICE + CHAT:' if language == 'en' else 'VOZ + CHAT:') in prompt) == allow_chat
    if allow_chat:
        assert ('complete request phrased naturally in their language' if language == 'en' else 'petición completa y natural en su idioma') in prompt
    assert ('LANGUAGE AND VOICE:' if language == 'en' else 'IDIOMA Y VOZ:') in prompt
    assert 'Do NOT delegate greetings' in prompt
    assert all(text in prompt for text in sections.values())
    persona = load_unit(PLUGIN_API, '_codexlive_persona')
    persona.update(ns)
    prompt = persona['_codexlive_persona'](profile, language)
    assert ('VOICE: You are speaking' if language == 'en' else 'VOZ: hablas') in prompt
    assert ('IDENTITY: You are ' if language == 'en' else 'IDENTIDAD: eres ') + ('Example Bot' if profile else 'Luna') in prompt
    assert all(text in prompt for text in sections.values())


@pytest.mark.parametrize('mode', ['client', 'server'])
@pytest.mark.parametrize('language', [None, 'es', 'en'])
def test_codex_start_sends_localized_delegation_instructions(mode, language):
    ns = load_unit(PLUGIN_API, '_codexlive_start')
    ns.update(constants())
    ns.update(_CL={'notifs': []}, _cl_ensure=lambda: None,
              _cl_thread_ensure=lambda lang: 'thread', _codexlive_persona=lambda _, lang: 'persona',
              _talk_settings=lambda: {'delegation': mode})
    captured = {}

    def request(method, params, **kwargs):
        if method == 'thread/realtime/start':
            captured.update(params)
            ns['_CL']['notifs'].append({'method': 'thread/realtime/sdp', 'params': {'sdp': 'stub'}})
        return {}

    ns['_cl_request'] = request
    ns['_codexlive_start'](None, 'cove', 'stub', language)
    instruction = captured['realtimeStartInstructions']
    assert instruction.startswith('You are connected to a live voice session' if language == 'en' else 'Estás conectado a una sesión de voz en vivo')
    if mode == 'client':
        assert '<realtime_delegation>' in instruction
        assert ('do NOT read files: reply with only the word: skip' if language == 'en' else 'NO leas archivos: responde únicamente la palabra: skip') in instruction
    else:
        assert ('carry it out with your tools' if language == 'en' else 'actúala con tus herramientas') in instruction


@pytest.mark.parametrize('language', [None, 'es', 'en'])
def test_tool_timeout_and_dynamic_output(language):
    ns = load_unit(PLUGIN_API, 'run_tool')
    output = 'Resultado original en español.'

    async def wait_for(*args, **kwargs):
        if output is None:
            raise asyncio.TimeoutError
        return output

    ns['asyncio'] = SimpleNamespace(wait_for=wait_for, to_thread=lambda *args: None,
                                    TimeoutError=asyncio.TimeoutError)
    ns['talk_tools'] = SimpleNamespace(execute_talk_tool=lambda *args: None, TalkToolError=ValueError)
    ns['_voice_gate_route'] = _no_voice_gate

    class Request:
        async def json(self):
            return {'name': 'example', 'arguments': {}, 'language': language}

    assert asyncio.run(ns['run_tool'](Request()))['output'] == output
    output = None
    assert asyncio.run(ns['run_tool'](Request()))['output'].startswith('The tool example is still running;' if language == 'en' else 'La herramienta example sigue corriendo;')
    tools = vendor_module('talk_tools')
    with pytest.raises(tools.TalkToolError, match="the voice tool 'example' is not available" if language == "en" else "la tool de voz 'example' no está disponible"):
        tools.execute_talk_tool('example', {}, language)


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
  assert.equal(await delegateToChat(''), tr('Petición vacía.', 'Empty request.'));
  _delegating = {text: 'revisa', at: now};
  assert.match(await delegateToChat('revisa'), (EN ? /^That request is already/ : /^Esa petición ya está/));
  _delegating = null;
  host.state.focusedSessionId = '';
  assert.match(await delegateToChat('revisa'), (EN ? /^No conversation is open/ : /^No hay una conversación/));
  host.state.focusedSessionId = 'session';
  assert.equal(await delegateToChat('revisa mi archivo'), completion);
  assert.match(submitted, (EN ? /^\[voice\] Reply briefly and naturally/ : /^\[voz\] Responde breve y natural/));
  assert.ok(submitted.endsWith('\n\nrevisa mi archivo'));
  completion = new Error('fallo original');
  assert.equal(await delegateToChat('revisa'), tr('No se pudo enviar al chat: fallo original', 'Could not send to the chat: fallo original'));
  completion = 'agent-error';
  assert.equal(await delegateToChat('revisa'), tr('Error del agente: fallo original', 'Agent error: fallo original'));
  completion = '';
  assert.equal(await delegateToChat('revisa'), tr('El agente terminó sin texto visible.', 'The agent finished without visible text.'));
  completion = null;
  assert.match(await delegateToChat('revisa'), (EN ? /^The agent is still working/ : /^El agente sigue trabajando/));
  completion = 'Resultado en español.';
  await runVoiceTool(ctx, 'call', 'send_to_chat', {request: 'revisa'}, dc);
  assert.match(messages[0].item.content[0].text, (EN ? /^\[The real agent is ALREADY/ : /^\[El agente real YA/));
  assert.equal(messages[2].item.output, completion);
  await runVoiceTool({rest: async () => { throw new Error('fallo original'); }}, 'call', 'probe', {}, dc);
  assert.equal(messages.at(-2).item.output, tr('La herramienta probe falló: fallo original', 'The tool probe failed: fallo original'));
  await runVoiceTool({rest: async (path, options) => { assert.equal(options.body.language, EN ? 'en' : 'es'); return {output: completion}; }}, 'call', 'probe', {}, dc);
  assert.equal(messages.at(-2).item.output, completion);
  const msg = text => ({item: {id: 'item', content: [{text}]}});
  handleDelegation(ctx, msg('hola, ¿me escuchas?'), dc);
  await flush();
  assert.match(lastText(), (EN ? /^The user was just chatting/ : /^El usuario solo estaba conversando/));
  handleDelegation({storage: {get: () => '0'}}, msg('revisa mi archivo'), dc);
  await flush();
  assert.match(lastText(), (EN ? /^I cannot run that/ : /^No puedo ejecutar tareas/));
  _delegBusy = true;
  handleDelegation(ctx, msg('revisa mi archivo'), dc);
  await flush();
  assert.match(lastText(), (EN ? /^A task is already in progress/ : /^Ya hay una tarea en curso/));
  _delegQueue.at = now - 600001;
  _delegBusy = false;
  _runDelegation(ctx, dc, 'current', 'revisa');
  await flush();
  assert.match(lastText(), (EN ? /^The queued task went stale/ : /^La tarea en cola quedó obsoleta/));
  assert.ok(messages.some(m => m.content?.[0]?.text === tr('Resultado de la tarea (respóndele al usuario con esto, breve y natural): ', 'Task result (answer the user with this, briefly and naturally): ') + completion));
  assert.equal(_plainForVoice('Texto español. ```codigo```'), tr('Texto español.  (bloque de código omitido)', 'Texto español.  (code block omitted)'));
  const realDelegate = delegateToChat;
  delegateToChat = async () => '';
  _runDelegation(ctx, dc, 'empty', 'revisa');
  await flush();
  assert.equal(lastText(), tr('Resultado de la tarea (respóndele al usuario con esto, breve y natural): La tarea no pudo completarse en el chat.', 'Task result (answer the user with this, briefly and naturally): The task could not be completed in the chat.'));
  delegateToChat = realDelegate;
  // Preview prompt construction and both outbound formats; no media APIs run.
  for (const eng of ['codex', 'realtime']) {
    const voice = 'cove';
    PREVIEW
    const message = messages.at(eng === 'codex' ? -1 : -2);
    const text = (message.content || message.item.content)[0].text;
    assert.match(text, (EN ? /^Say out loud exactly this sentence, adding nothing else:/ : /^Di en voz alta exactamente esta frase,/));
    assert.ok(text.includes(EN ? 'Hi, this is voice cove' : 'Hola, esta es la voz cove'));
    assert.ok(!text.includes('Nacho'));
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
'''.replace('EN_VALUE', str(english).lower()).replace('DELEGATION', delegation).replace('PREVIEW', preview)
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('route', ['create_session', 'codexlive_session'])
def test_session_routes_keep_request_language_isolated(route):
    import threading
    ns = load_unit(PLUGIN_API, route)
    calls = []
    def mint(profile, voice, allow_chat, language):
        calls.append(language)
        return SimpleNamespace(to_wire=lambda: {'language': language}), SimpleNamespace(source='stub')
    def start(profile, voice, offer, language):
        calls.append(language)
        return {'language': language}
    ns.update(_MINT_LOCK=threading.Lock(), _CL_LOCK=threading.Lock(),
              _resolve_voice=lambda value: 'marin', _mint_for=mint, _codexlive_start=start)
    class Request:
        def __init__(self, language):
            self.language = language
        async def json(self):
            return {'offer': 'stub', **({'language': self.language} if self.language else {})}
    async def run():
        return await asyncio.gather(*(ns[route](Request(lang)) for lang in ['en', 'es', None]))
    results = asyncio.run(run())
    assert [r['language'] for r in results] == ['en', 'es', None]
    assert sorted(str(v) for v in calls) == ['None', 'en', 'es']


def test_codex_thread_reuse_includes_language():
    from pathlib import Path
    ns = load_unit(PLUGIN_API, '_cl_thread_ensure')
    calls = []
    def request(method, body, **kwargs):
        calls.append(body)
        return {'thread': {'id': str(len(calls))}}
    ns.update(_CL={}, _agent_model=lambda: None, Path=Path, _cl_request=request)
    ensure = ns['_cl_thread_ensure']
    assert ensure() == ensure('es') == '1'
    assert ensure('en') == ensure('en') == '2'
    assert ensure('es') == '3'


@pytest.mark.parametrize('english', [False, True])
def test_desktop_session_request_bodies(english):
    import re
    source = (REPO / 'desktop/plugin.js').read_text()
    # Evaluate every production session request body, including preview's Codex path.
    bodies = re.findall(r"ctx.rest\('/(?:codexlive/)?session',\s*\{\s*method: 'POST',\s*body: (\{[^\n]+?\}),", source)
    assert len(bodies) == 3
    script = 'const assert = require("node:assert/strict"); const EN = ' + str(english).lower() + ';'
    script += 'const profile = null, voice = "cove", offer = {sdp: "stub"}, allowChat = true;'
    for body in bodies:
        script += f'assert.equal(({body}).language, EN ? "en" : "es");'
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('language', [None, 'es', 'en'])
def test_tool_route_localizes_unavailable_error(language):
    ns = load_unit(PLUGIN_API, 'run_tool')
    class HTTPException(Exception):
        def __init__(self, status_code, detail):
            self.detail = detail
    ns.update(talk_tools=vendor_module('talk_tools'), HTTPException=HTTPException, _voice_gate_route=_no_voice_gate)
    class Request:
        async def json(self):
            return {'name': 'probe', 'language': language}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ns['run_tool'](Request()))
    assert ('the voice tool' if language == 'en' else 'la tool de voz') in exc.value.detail
