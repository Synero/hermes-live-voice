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
    names = {'_LANGUAGE_DIRECTIVE_DEFAULT', '_AGENT_INSTR', '_AGENT_INSTR_SKIP', '_VOICE_POLICY', '_VOICE_POLICY_DE'}
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
@pytest.mark.parametrize('language', [None, 'es', 'en', 'de'])
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
        _host_bot_name=lambda: 'Host Bot',
        talk_auth=SimpleNamespace(resolve_auth=lambda: SimpleNamespace(token='stub', source='env')),
        talk_config=SimpleNamespace(talk_model=lambda: 'stub'),
        talk_wire=SimpleNamespace(mint_ephemeral_session=lambda **kw: captured.update(kw)),
    )
    directive = load_unit(PLUGIN_API, '_language_directive')
    directive.update(ns)
    ns['_language_directive'] = directive['_language_directive']
    ns['_send_to_chat_tool'].__globals__['_SEND_TO_CHAT_DESC'] = 'Send work to the chat.'
    ns['_mint_for'](profile, 'marin', allow_chat, language)
    prompt = captured['instructions']
    assert (({'en': 'VOICE + CHAT:', 'de': 'SPRACHE + CHAT:'}.get(language or 'en', 'VOZ + CHAT:')) in prompt) == allow_chat
    if allow_chat:
        assert ('complete request phrased naturally in their language' if language in ('en', None) else 'vollständigen Anfrage in seiner Sprache' if language == 'de' else 'petición completa y natural en su idioma') in prompt
        if language == 'de':
            assert '\n\nSPRACHE + CHAT:' in prompt
    assert ({'en': 'LANGUAGE AND VOICE:', 'de': 'SPRACHE UND STIMME:'}.get(language or 'en', 'IDIOMA Y VOZ:')) in prompt
    if language == 'de':
        assert 'Grüße, Smalltalk, Bestätigungen und Füllwörter nicht weitergeben' in prompt
    else:
        assert 'Do NOT delegate greetings' in prompt
    assert all(text in prompt for text in sections.values())
    persona = load_unit(PLUGIN_API, '_codexlive_persona')
    persona.update(ns)
    prompt = persona['_codexlive_persona'](profile, language)
    assert ({'en': 'VOICE: You are speaking', 'de': 'STIMME: Du sprichst'}.get(language or 'en', 'VOZ: hablas')) in prompt
    assert ({'en': 'IDENTITY: You are ', 'de': 'IDENTITÄT: Du bist '}.get(language or 'en', 'IDENTIDAD: eres ') + ('Example Bot' if profile else 'Host Bot')) in prompt
    if language == 'de':
        assert '\n\nIDENTITÄT: Du bist ' in prompt
    assert all(text in prompt for text in sections.values())


@pytest.mark.parametrize('mode', ['client', 'server'])
@pytest.mark.parametrize('language', [None, 'es', 'en', 'de'])
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
    assert instruction.startswith({'en': 'You are connected to a live voice session', 'de': 'Du bist mit einer Live-Sprachsitzung'}.get(language or 'en', 'Estás conectado a una sesión de voz en vivo'))
    if mode == 'client':
        assert '<realtime_delegation>' in instruction
        assert ({'en': 'do NOT read files: reply with only the word: skip', 'de': 'verwende keine Tools'}.get(language or 'en', 'NO leas archivos: responde únicamente la palabra: skip')) in instruction
    else:
        assert ({'en': 'carry it out with your tools', 'de': 'erledige sie mit deinen Tools'}.get(language or 'en', 'actúala con tus herramientas')) in instruction


@pytest.mark.parametrize('language', [None, 'es', 'en', 'de'])
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

    class Request:
        async def json(self):
            return {'name': 'example', 'arguments': {}, 'language': language}

    assert asyncio.run(ns['run_tool'](Request()))['output'] == output
    output = None
    assert asyncio.run(ns['run_tool'](Request()))['output'].startswith({'en': 'The tool example is still running;', 'de': 'Das Tool example läuft noch;'}.get(language or 'en', 'La herramienta example sigue corriendo;'))
    tools = vendor_module('talk_tools')
    with pytest.raises(tools.TalkToolError, match={'en': "the voice tool 'example' is not available", 'de': "Das Sprach-Tool 'example' ist in dieser Installation nicht verfügbar"}.get(language or 'en', "la tool de voz 'example' no está disponible")):
        tools.execute_talk_tool('example', {}, language)


@pytest.mark.parametrize('english', [False, True])
def test_desktop_model_messages(english):
    source = (REPO / 'desktop/plugin.js').read_text()
    # Evaluate the real delegation functions with only host/time/transport boundaries stubbed.
    delegation = source[source.index('const _voiceNote ='):source.index('// ── Barge-in')]
    preview = source[source.index('  const phrase ='):source.index('  const url = await new Promise', source.index('  const phrase ='))]
    script = r'''
const assert = require('node:assert/strict');
const LANGUAGE = EN_VALUE ? 'en' : 'es';
const EN = LANGUAGE === 'en';
const DE = {};
const tr = (es, en, de) => LANGUAGE === 'de' ? (de || en) : (EN ? en : es);
let _delegating = null, _delegBusy = false, _delegQueue = null;
let handoffMode = 'client';
const transcript = [], pushTranscript = () => {}, KEY_CHAT = 'chat';
let now = 1000000;
Date.now = () => now;
const sleep = async () => { now += 300000; };
const handlers = {};
let completion = 'Resultado en español.', submitted, submittedSurface, legacyHost = false;
const host = {state: {focusedSessionId: 'session'}, onEvent: (name, cb) => {
  handlers[name] = cb; return () => { delete handlers[name]; };
}, request: async (method, body) => {
  if (legacyHost && 'surface' in body) { const e = new Error('Invalid params: surface: Extra inputs are not permitted'); e.code = 4000; throw e; }
  submitted = body.text; submittedSurface = body.surface;
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
  // Host nuevo: la burbuja queda con lo dicho; la nota de voz la agrega el host solo al modelo.
  assert.equal(submitted, 'revisa mi archivo');
  assert.equal(submittedSurface, 'voice-live');
  // Host viejo (params extra=forbid): cae al prefijo de siempre.
  legacyHost = true;
  assert.equal(await delegateToChat('revisa mi archivo'), completion);
  assert.match(submitted, (EN ? /^\[voice\] Reply briefly and naturally/ : /^\[voz\] Responde breve y natural/));
  assert.ok(submitted.endsWith('\n\nrevisa mi archivo'));
  assert.equal(submittedSurface, undefined);
  legacyHost = false;
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
  assert.match(lastText(), (EN ? /^The user was just chatting/ : /^El usuario solo estaba conversando/));
  handleDelegation({storage: {get: () => '0'}}, msg('revisa mi archivo'), dc);
  assert.match(lastText(), (EN ? /^I cannot run that/ : /^No puedo ejecutar tareas/));
  _delegBusy = true;
  handleDelegation(ctx, msg('revisa mi archivo'), dc);
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
              _resolve_voice=lambda value: 'marin', _mint_for=mint, _codexlive_start=start, _host_bot_name=lambda: 'Hermes')
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
    assert ensure() == ensure('en') == '1'
    assert ensure('es') == '2'
    assert ensure('en') == '3'


def test_german_directive_and_voice_policy_are_localized(tmp_path):
    ns = load_unit(PLUGIN_API, '_language_directive')
    ns.update(constants(), _DIRECTIVE_PATH=tmp_path / 'directive.txt')
    german = ns['_language_directive']('de')
    assert 'SPRACHE UND STIMME:' in german
    policy = load_unit(PLUGIN_API, '_voice_policy')
    policy.update(constants())
    assert 'ich prüfe das' in policy['_voice_policy']('de')
    assert 'Smalltalk, Bestätigungen und Füllwörter nicht weitergeben' in policy['_voice_policy']('de')
    custom = '  Meine Spracheinstellung bleibt exakt.\n'
    ns['_DIRECTIVE_PATH'].write_text(custom)
    assert ns['_language_directive']('de') == '\n\n' + custom


@pytest.mark.parametrize(('language', 'expected'), [
    ('de', 'de'), ('de-DE', 'de'), ('de_AT', 'de'), ('es-ES', 'es'), ('fr-FR', 'en'),
    ('default', 'en'), ('desktop', 'en'), ('esperanto', 'en'), (None, 'en'),
])
def test_codex_threads_normalize_language(language, expected):
    from pathlib import Path
    ns = load_unit(PLUGIN_API, '_cl_thread_ensure')
    calls = []
    def request(method, body, **kwargs):
        calls.append(body)
        return {'thread': {'id': str(len(calls))}}
    ns.update(_CL={}, _agent_model=lambda: None, Path=Path, _cl_request=request)
    ensure = ns['_cl_thread_ensure']
    assert ensure(language) == str(len(calls))
    assert ns['_CL']['thread_language'] == expected


@pytest.mark.parametrize('english', [False, True])
def test_desktop_session_request_bodies(english):
    import re
    source = (REPO / 'desktop/plugin.js').read_text()
    # Evaluate every production session request body, including preview's Codex path.
    bodies = re.findall(r"ctx.rest\('/(?:codexlive/)?session',\s*\{\s*method: 'POST',\s*body: (\{[^\n]+?\}),", source)
    assert len(bodies) == 3
    script = 'const assert = require("node:assert/strict"); const LANGUAGE = ' + repr('en' if english else 'es') + ';'
    script += 'const profile = null, voice = "cove", offer = {sdp: "stub"}, allowChat = true;'
    for body in bodies:
        script += f'assert.equal(({body}).language, LANGUAGE);'
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_codex_schema_backed_voices_are_opt_in_and_keep_existing_defaults():
    """Schema-backed IDs round-trip only for Codex; defaults and stable lists stay unchanged."""
    import re
    source = (REPO / 'desktop/plugin.js').read_text()
    match = re.search(r"const V3_VOICES = .*?\nconst SENTINEL_DEFAULT", source, re.S)
    assert match, 'Codex voice selection definitions not found'
    script = 'const assert = require("node:assert/strict"); const tr = (es, en) => en;\nconst VOICES = ["marin", "cedar", "alloy", "echo", "shimmer", "verse", "sage", "ballad"];\n'
    script += match.group(0).replace('\nconst SENTINEL_DEFAULT', '\n')
    script += '''
const expectedVoiceLanguages = {
  aube: ['Aube', 'Français'], bubbie: ['Bubbie', 'English'],
  haetsal: ['Haetsal', '한국어'], hanul: ['Hanul', '한국어'], himari: ['Himari', '日本語'],
  leher: ['Leher', 'हिन्दी'], miko: ['Miko', 'Deutsch'], neer: ['Neer', 'हिन्दी'],
  rindo: ['Rindo', '日本語'], rio: ['Rio', 'Português (Brasil)'],
  rivage: ['Rivage', 'Français'], sonna: ['Sonna', 'Deutsch'],
  tinta: ['Tinta', 'Español (México)'], viola: ['Viola', 'Português (Brasil)']
};
const expectedExperimental = Object.keys(expectedVoiceLanguages);
assert.deepEqual([...new Set(V3_EXPERIMENTAL_VOICES)].sort(), [...expectedExperimental].sort());
assert.deepEqual(Object.keys(V3_EXPERIMENTAL_LABELS).sort(), [...expectedExperimental].sort());
assert.equal(V3_EXPERIMENTAL_VOICES.length, expectedExperimental.length);
assert.equal(new Set([...V3_VOICES, ...V3_EXPERIMENTAL_VOICES]).size, V3_VOICES.length + V3_EXPERIMENTAL_VOICES.length);
for (const [voice, [name, language]] of Object.entries(expectedVoiceLanguages)) {
  assert.equal(effVoiceFor('codex', voice), voice);
  assert.equal(voiceOptionLabel(voice), name);
  assert.equal(effVoiceFor('hermes', voice), 'marin');
}
assert.equal(effVoiceFor('codex', 'cove'), 'cove');
assert.equal(effVoiceFor('codex', 'unknown'), 'cove');
assert.equal(effVoiceFor('codex', 'arrullo'), 'cove');
assert.equal(effVoiceFor('hermes', 'miko'), 'marin');
assert.equal(voiceOptionLabel('cove'), 'Cove');
assert.deepEqual(V3_VOICES, ['cove', 'juniper', 'maple', 'spruce', 'ember', 'vale', 'breeze', 'arbor', 'sol']);
assert.deepEqual(VOICES, ['marin', 'cedar', 'alloy', 'echo', 'shimmer', 'verse', 'sage', 'ballad']);
'''
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('language', [None, 'es', 'en', 'de'])
def test_tool_route_localizes_unavailable_error(language):
    ns = load_unit(PLUGIN_API, 'run_tool')
    class HTTPException(Exception):
        def __init__(self, status_code, detail):
            self.detail = detail
    ns.update(talk_tools=vendor_module('talk_tools'), HTTPException=HTTPException)
    class Request:
        async def json(self):
            return {'name': 'probe', 'language': language}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ns['run_tool'](Request()))
    assert ({'en': 'the voice tool', 'de': 'Das Sprach-Tool'}.get(language or 'en', 'la tool de voz')) in exc.value.detail
