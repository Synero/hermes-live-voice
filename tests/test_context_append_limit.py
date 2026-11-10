"""Wire contract for delegation.context.append on the Codex Live (v3) data channel.

The server rejects any append whose text exceeds 500 tokens with
"Context append text must not exceed 500 tokens." and the Codex core then closes the
whole live session with outcome "failed". Codex's own client splits every append into
<=500-byte pieces (codex-rs methods_frameless_bidi.rs, CONTEXT_APPEND_MAX_BYTES); a token
is at least one byte, so 500 bytes can never exceed 500 tokens.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_long_results_reach_the_voice_whole_in_frames_within_the_limit():
    source = (REPO / 'desktop/plugin.js').read_text()
    delegation = source[source.index('const _voiceNote ='):source.index('// ── Barge-in')]
    script = r'''
const assert = require('node:assert/strict');
const EN = true;
const tr = (es, en) => EN ? en : es;
let _delegating = null, _delegBusy = false, _delegQueue = null, _delegSid = '';
let handoffMode = 'client';
const transcript = [], pushTranscript = () => {}, KEY_CHAT = 'chat';
const sleep = async () => {};
const handlers = {};
// A long spoken answer with multi-byte characters (accents, curly quotes, emoji).
const paragraph = 'Here’s the fuller version — café, niño, “quoted” text and 🙂 included. ';
const longText = paragraph.repeat(60).trim();
const interim = 'Found the cause. ' + paragraph.repeat(20).trim();
const host = {state: {focusedSessionId: 'session'}, onEvent: (name, cb) => {
  handlers[name] = cb; return () => { delete handlers[name]; };
}, request: async () => {
  handlers['message.interim']({text: interim});
  handlers['message.complete']({text: longText});
  return {admission_id: 'test', status: 'started', execution_generation: 7};
}};
const frames = [];
const dc = {send: data => frames.push(JSON.parse(data))};
const ctx = {storage: {get: () => '1'}};
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
DELEGATION
(async () => {
  _runDelegation(ctx, dc, 'item_1', 'give me more detail');
  await flush();
  const appends = frames.filter(f => f.type === 'delegation.context.append');
  for (const f of appends) {
    assert.equal(f.delegation_item_id, 'item_1');
    assert.equal(f.content.length, 1);
    const bytes = Buffer.byteLength(f.content[0].text, 'utf8');
    assert.ok(bytes <= 500, 'append of ' + bytes + ' bytes exceeds the 500-token wire limit');
  }
  const joined = ch => appends.filter(f => f.channel === ch).map(f => f.content[0].text).join('');
  assert.equal(joined('commentary'), interim + '\n\nNot done yet.');
  assert.equal(joined('speakable'), 'Task complete. Result: ' + longText);
})().catch(e => { console.error(e); process.exitCode = 1; });
'''.replace('DELEGATION', delegation)
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
