// talk-desktop — Live Voice v21. Motor por defecto: Codex Live-1 (gpt-live-1, suscripción) con
// fallback/alternativa Hermes (gpt-realtime-2.1). Sin ventana flotante: botón + popover del botón.
// v21: follow-window del bot, delegación endurecida, test de micrófono, transcripción en burbujas iMessage.
// Config: gear junto al mic → Popover NATIVO del desktop (portal propio, sin clipping) con Selects nativos.
// Onda: barras reactivas al espectro WebAudio real + fallback a levels de WebRTC getStats — nunca random.
// En vivo: círculo navy, aro azul suave; hover → rojo (colgar). Cargando: anillo azul girando.
// Transcript en vivo (canal oai-events), tools de voz (relay /tool), uso Codex en la config.
// Sonidos chime al conectar/colgar. Widget flotante + pane con el mismo formulario.
// IMPORTS PERMITIDOS: 'react', 'react/jsx-runtime', '@hermes/plugin-sdk' (nada más).
import { jsx, jsxs } from 'react/jsx-runtime'
import { useState, useEffect, useRef } from 'react'
import * as sdk from '@hermes/plugin-sdk'

const {
  Popover, PopoverContent, PopoverTrigger,
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
  host, useValue, Switch, Button
} = sdk

const KEY_VOICE = 'voice.v3'
const KEY_PROFILE = 'lastProfile.v3'
const KEY_MIC = 'mic.v3'
const KEY_OUT = 'out.v3'
const KEY_CHAT = 'chatwork.v1'
const KEY_ENGINE = 'engine.v1'
const VOICES = ['marin', 'cedar', 'alloy', 'echo', 'shimmer', 'verse', 'sage', 'ballad']
const V3_VOICES = ['cove', 'juniper', 'maple', 'spruce', 'ember', 'vale', 'breeze', 'arbor', 'sol']
const effVoiceFor = (engine, stored) => {
  const v = String(stored || '')
  if (engine === 'codex') return V3_VOICES.indexOf(v) >= 0 ? v : 'cove'
  return VOICES.indexOf(v) >= 0 ? v : 'marin'
}
const SENTINEL_DEFAULT = '__default'
const SENTINEL_HOST = '__host'
const EN = String((typeof navigator !== 'undefined' && navigator.language) || 'es').toLowerCase().indexOf('en') === 0
const tr = (es, en) => (EN ? en : es)

// Allowance de VOZ de Codex en desktop: ventana rolling de 5 horas, por plan.
// Fuente: learn.chatgpt.com/docs/pricing (Plus ~15-30 min; Pro 5x ~1-2.5 h; Pro 20x ilimitado).
const VOICE_CAP_MIN = plan => {
  const p = String(plan || '').toLowerCase()
  if (p.indexOf('plus') >= 0) return [15, 30]
  if (p.indexOf('business') >= 0 || p.indexOf('enterprise') >= 0 || p.indexOf('edu') >= 0) return [45, 45]
  if (p.indexOf('pro') >= 0) return [60, 150]
  return null
}
const SB = 'var(--popover-surface, #ffffff)'

const bus = {
  live: false, stage: 'idle', micLevel: 0, remoteLevel: 0, widget: false, err: '', muted: false,
  bands: [0, 0, 0, 0, 0, 0], remBands: [0, 0, 0, 0, 0, 0], transcriptRev: 0, spkUser: false, spkBot: false, botName: '',
  emit() {
    try {
      window.dispatchEvent(new CustomEvent('talk-desktop:state', { detail: {
        live: this.live, stage: this.stage, micLevel: this.micLevel, remoteLevel: this.remoteLevel, muted: this.muted,
        widget: this.widget, err: this.err, bands: this.bands, remBands: this.remBands,
        transcriptRev: this.transcriptRev, spkUser: this.spkUser, spkBot: this.spkBot
      } }))
    } catch {}
  },
  set(patch) { Object.assign(this, patch); this.emit() }
}

// ── transcripción de la sesión (compartida por widget y pane) ────────────────

let transcript = []
let liveAudioMs = 0
let handoffMode = 'client'
let _delegBusy = false
let _delegQueue = null
let _lastAppendedId = ''
let _bumpTimer = null
function _bumpTranscript() {
  if (_bumpTimer) return
  _bumpTimer = setTimeout(() => { _bumpTimer = null; bus.set({ transcriptRev: (bus.transcriptRev || 0) + 1 }) }, 110)
}
function _lastOpen(role) {
  for (let i = transcript.length - 1; i >= Math.max(0, transcript.length - 6); i--) {
    if (transcript[i].role === role && !transcript[i].done) return transcript[i]
  }
  return null
}
function _recentOpen(role, now) {
  for (let i = transcript.length - 1; i >= Math.max(0, transcript.length - 6); i--) {
    const it = transcript[i]
    if (it.role === role) {
      if (it.done) return null
      return (now - (it.at || 0) < GAP_MS) ? it : null
    }
  }
  return null
}
// El lane v3 rota turnos y manda fragmentos (turn.done parciales con 1-2 palabras):
// NUNCA encoger ni cerrar un bubble por un turn.done (eso producía texto duplicado
// y "editándose"). Un bubble CONTINÚA mientras los deltas del mismo rol lleguen con
// menos de GAP_MS de separación (inmune a ráfagas); si no, se abre uno nuevo.
// IDLE_MS solo controla el brillo (done=false pinta al 72%).
const GAP_MS = 3500
const IDLE_MS = 8000
function _push(role, text, done) {
  transcript.push({ role: role, text: text, done: !!done, at: Date.now() })
  if (transcript.length > 240) transcript = transcript.slice(-240)
  _bumpTranscript()
}
function pushTranscript(role, text) {
  const raw = String(text == null ? '' : text)
  const now = Date.now()
  const open = role === 'bot' ? _recentOpen('bot', now) : null
  if (open) { open.text += raw; open.at = now; _bumpTranscript(); return }
  const t = raw.trim()
  if (!t) return
  _push(role, t, role !== 'bot')
}
function pushUserDelta(text) {
  const raw = String(text == null ? '' : text)
  if (!raw) return
  const now = Date.now()
  const openU = _recentOpen('user', now)
  if (openU) { openU.text += raw; openU.at = now; _bumpTranscript(); return }
  _push('user', raw, false)
}

function finalizeUser(finalText) {
  const t = String(finalText == null ? '' : finalText).trim()
  if (!t) return
  const open = _recentOpen('user', Date.now()) || _lastOpen('user')
  if (open) {
    if (t.length > String(open.text || '').length) { open.text = t; open.at = Date.now(); _bumpTranscript() }
    return
  }
  _push('user', t, true)
}

function endBotTranscript(finalText) {
  const t = String(finalText == null ? '' : finalText).trim()
  if (!t) return
  const open = _recentOpen('bot', Date.now()) || _lastOpen('bot')
  if (open) {
    if (t.length > String(open.text || '').length) { open.text = t; open.at = Date.now(); _bumpTranscript() }
    return
  }
  _push('bot', t, true)
}
function clearTranscript() { transcript = []; _bumpTranscript() }

const sleep = ms => new Promise(r => setTimeout(r, ms))
let _delegating = null

// Voz → Chat: envía la petición al chat ENFOCADO (el agente real responde ahí,
// streaming visible en la ventana) y devuelve el texto de la respuesta para leerlo.
const _voiceNote = () => (EN
  ? '[voice] Reply briefly and naturally in the user’s language so it can be READ ALOUD (2-4 sentences, no markdown or lists). The text is dictated and may contain errors, use the latest intent. Do not claim something is done before it actually is.\n\n'
  : '[voz] Responde breve y natural en el idioma del usuario para LEER EN VOZ ALTA (2-4 frases, sin markdown ni listas). El texto es dictado y puede tener errores; usa la última intención. No afirmes que algo quedó hecho antes de hacerlo.\n\n')

async function delegateToChat(text) {
  const req = String(text || '').trim()
  if (!req) return tr('Petición vacía.', 'Empty request.')
  if (_delegating && _delegating.text === req && Date.now() - _delegating.at < 30000) return tr('Esa petición ya está en curso en el chat; espera su respuesta.', 'That request is already in progress in the chat; wait for its response.')
  const atomGet = v => { try { return v && typeof v.get === 'function' ? v.get() : v } catch { return null } }
  const sid = String(atomGet(host.state.focusedSessionId) || atomGet(host.state.activeSessionId) || '')
  if (!sid) return tr('No hay una conversación abierta en la ventana. Dile al usuario que abra el chat donde quieres trabajar y que repita la petición.', 'No conversation is open in the window. Tell the user to open the chat where they want to work and repeat the request.')
  _delegating = { text: req, at: Date.now() }
  pushTranscript('tool', 'trabajando en el chat…')
  let acc = ''
  let settled = false
  let finalText = ''
  let errText = ''
  const unsubs = []
  try {
    try {
      if (host && typeof host.onEvent === 'function') {
        const evSid = ev => String((ev && (ev.session_id != null ? ev.session_id : (ev.sessionId != null ? ev.sessionId : ((ev.payload || {}).session_id || (ev.payload || {}).sessionId)))) || '')
        const same = ev => { const es = evSid(ev); return !es || es === sid }
        const evText = ev => String((ev && (ev.text != null ? ev.text : (ev.display != null ? ev.display : ((ev.payload || {}).text != null ? (ev.payload || {}).text : ((ev.payload || {}).display || ''))))) || '')
        unsubs.push(host.onEvent('message.delta', ev => {
          if (!same(ev)) return
          const piece = ev && (ev.delta != null ? ev.delta : ev.text)
          if (typeof piece === 'string') acc += piece
        }))
        unsubs.push(host.onEvent('message.complete', ev => {
          if (settled || !same(ev)) return
          settled = true
          finalText = evText(ev)
        }))
        unsubs.push(host.onEvent('error', ev => {
          if (settled || !same(ev)) return
          settled = true
          errText = tr('Error del agente: ', 'Agent error: ') + String((ev && (ev.message || ev.error || (ev.payload || {}).message)) || tr('desconocido', 'unknown'))
        }))
      }
      await host.request('prompt.submit', { session_id: sid, text: _voiceNote() + req })
    } catch (e) {
      return tr('No se pudo enviar al chat: ', 'Could not send to the chat: ') + String((e && e.message) || e || 'error')
    }
    const t0 = Date.now()
    while (!settled && Date.now() - t0 < 240000) await sleep(300)
    if (!settled) return tr('El agente sigue trabajando en el chat; avísale al usuario que la respuesta aparecerá ahí en un momento.', 'The agent is still working in the chat; tell the user the response will appear there shortly.')
    const out = (errText || finalText || acc).trim()
    return out ? out.slice(0, 4000) : tr('El agente terminó sin texto visible.', 'The agent finished without visible text.')
  } finally {
    _delegating = null
    for (const u of unsubs) { try { u() } catch {} }
  }
}

async function runVoiceTool(ctx, callId, name, args, dc) {
  pushTranscript('tool', '· ' + name + ' ' + JSON.stringify(args || {}).slice(0, 100))
  let output = ''
  if (name === 'send_to_chat') {
    // ack hablado: confirmación breve mientras el chat trabaja
    try {
      dc.send(JSON.stringify({ type: 'conversation.item.create', item: { type: 'message', role: 'system', content: [{ type: 'input_text', text: tr('[El agente real YA está trabajando en el chat del usuario con esta petición. Ahora dile al usuario UNA sola frase muy breve (máximo 8 palabras) en SU idioma confirmando que lo estás gestionando; no des resultados aún.]', '[The real agent is ALREADY working on this request in the user’s chat. Now say ONE very short sentence (at most 8 words) in THEIR language confirming that you are handling it; do not give any results yet.]') }] } }))
      dc.send(JSON.stringify({ type: 'response.create' }))
    } catch {}
    output = await delegateToChat(String((args && (args.request || args.text)) || ''))
  } else {
    try {
      const r = await ctx.rest('/tool', { method: 'POST', body: { language: EN ? 'en' : 'es', name: name, arguments: args || {} }, timeoutMs: 125000 })
      output = (r && r.output) || ''
    } catch (e) {
      output = tr('La herramienta ', 'The tool ') + name + tr(' falló: ', ' failed: ') + String((e && e.message) || e).slice(0, 160)
    }
  }
  pushTranscript('tool', '→ ' + String(output).slice(0, 400))
  try {
    dc.send(JSON.stringify({ type: 'conversation.item.create', item: { type: 'function_call_output', call_id: callId, output: String(output) } }))
    dc.send(JSON.stringify({ type: 'response.create' }))
  } catch {}
}

// Delegación nativa del lane v3: el modelo de voz delega una tarea al cliente.
// Con "Trabajar en el chat" la ejecutamos en la ventana enfocada y devolvemos el
// resultado por delegation.context.append (el modelo lo lee en voz alta).
function _isJunkDelegation(t) {
  const s = String(t || '').trim()
  if (!s) return true
  if (s.length > 90) return false
  const toks = s.toLowerCase().replace(/[¿?¡!.,;:…()"'«»]/g, ' ').split(/\s+/).filter(Boolean)
  if (!toks.length) return true
  const fill = new Set(['hola', 'hello', 'hey', 'dale', 'ya', 'bueno', 'bien', 'si', 'sí', 'no', 'nope', 'aló', 'alo', 'holi', 'eh', 'emm', 'mmm', 'ah', 'ahá', 'ajá', 'aja', 'ok', 'okay', 'listo', 'perfecto', 'gracias', 'thanks', 'eso', 'mismo', 'y', 'qué', 'que', 'pasó', 'paso', 'final', 'me', 'escuchas', 'estás', 'estas', 'ahí', 'ahi', 'verá', 'vera', 'oye', 'pues', 'onda', 'po'])
  const n = toks.filter(w => fill.has(w)).length
  return n / toks.length >= 0.8
}

function _plainForVoice(t) {
  let s = String(t || '')
  s = s.replace(/```[\s\S]*?```/g, ' ' + tr('(bloque de código omitido)', '(code block omitted)') + ' ')
  s = s.replace(/`([^`]*)`/g, '$1')
  s = s.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/__([^_]+)__/g, '$1')
  s = s.replace(/^#{1,6}\s+/gm, '')
  s = s.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
  s = s.replace(/\n{3,}/g, '\n\n')
  return s.trim()
}

function _delegRespond(dc, itemId, text) {
  try {
    dc.send(JSON.stringify({ type: 'delegation.context.append', delegation_item_id: itemId,
      content: [{ type: 'input_text', text: String(text || '').slice(0, 3500) }] }))
  } catch {}
}

function handleDelegation(ctx, msg, dc) {
  const item = (msg && msg.item) || {}
  const itemId = String(item.id || '')
  let req = ''
  try {
    if (Array.isArray(item.content)) req = item.content.map(c => (c && c.text) || '').join('')
  } catch {}
  req = String(req || '').trim()
  if (!req) {
    for (let i = transcript.length - 1; i >= 0; i--) {
      if (transcript[i].role === 'user' && transcript[i].text) { req = String(transcript[i].text).trim(); break }
    }
  }
  pushTranscript('tool', tr('delegación: ', 'delegation: ') + (req || '…').slice(0, 180))
  if (handoffMode === 'server') {
    pushTranscript('sys', tr('El agente del servidor la está resolviendo; te leeré el resultado cuando esté.', 'The server agent is on it; I will read the result when ready.'))
    return
  }
  if (_isJunkDelegation(req)) {
    pushTranscript('tool', tr('charla (no es tarea) — la voz responde sola', 'small talk (not a task) — voice answers itself'))
    _delegRespond(dc, itemId, tr('El usuario solo estaba conversando, no pidiendo trabajo. Responde tú breve y natural; si te estaba preguntando por algo en curso, dile que sigues en eso. No hay nada que ejecutar.', 'The user was just chatting, not asking for work. Reply briefly and naturally; if they were asking about something in progress, tell them you are still on it. Nothing to run.'))
    return
  }
  const chatWork = (ctx.storage.get(KEY_CHAT) || '1') !== '0'
  if (!chatWork) {
    pushTranscript('sys', tr('Tareas desactivadas: activa "Trabajar en el chat".', 'Tasks are off: enable "Work in the chat".'))
    _delegRespond(dc, itemId, tr('No puedo ejecutar tareas: "Trabajar en el chat" está desactivado. Pídele al usuario que lo active en la configuración del micrófono.', 'I cannot run that: "Work in the chat" is disabled. Ask the user to enable it in the mic settings.'))
    return
  }
  if (_delegBusy) {
    _delegQueue = { req, itemId, at: Date.now() }
    pushTranscript('tool', tr('en cola (tarea en curso): ', 'queued (task in progress): ') + req.slice(0, 100))
    _delegRespond(dc, itemId, tr('Ya hay una tarea en curso. Dile al usuario que sigues trabajando en eso; si lo que dijo es una corrección, se verá reflejada en el resultado, y si es algo nuevo, espera a que termine lo actual.', 'A task is already in progress. Tell the user you are still on it; if what they said is a correction it will be reflected in the result, if it is something new, wait for the current one to finish.'))
    return
  }
  _runDelegation(ctx, dc, itemId, req)
}

function _runDelegation(ctx, dc, itemId, req) {
  _delegBusy = true
  ;(async () => {
    let out = ''
    try { out = await delegateToChat(req) } catch (e) { out = '' }
    out = String(out || '').trim()
    if (!out) out = tr('La tarea no pudo completarse en el chat.', 'The task could not be completed in the chat.')
    const forVoice = _plainForVoice(out)
    pushTranscript('tool', tr('resultado: ', 'result: ') + forVoice.slice(0, 160))
    _delegRespond(dc, itemId, tr('Resultado de la tarea (respóndele al usuario con esto, breve y natural): ', 'Task result (answer the user with this, briefly and naturally): ') + forVoice)
  })().catch(() => {}).then(() => {
    _delegBusy = false
    const q = _delegQueue
    _delegQueue = null
    if (q && Date.now() - q.at < 600000) {
      pushTranscript('tool', tr('retomando en cola: ', 'resuming queued: ') + q.req.slice(0, 100))
      _runDelegation(ctx, dc, q.itemId, q.req)
    } else if (q) {
      pushTranscript('sys', tr('La tarea en cola quedó obsoleta y no se ejecutó.', 'Queued task went stale and was not run.'))
      _delegRespond(dc, q.itemId, tr('La tarea en cola quedó obsoleta y no se ejecutó. Dile al usuario que si todavía la quiere, la repita y la ejecutas al tiro.', 'The queued task went stale and was not run. Tell the user that if they still want it, to say it again and you will run it right away.'))
    }
  })
}

// ── Barge-in (estado de módulo: lo escriben los eventos y lo lee el medidor) ──
let currentBotTurnId = ''
let _lastBargeAt = 0
let _botSpeakingSince = 0
let _liveRefs = null  // { pc, audioEl, ctx } — set por startLive

function doBargeIn() {
  const now = Date.now()
  if (now - _lastBargeAt < 1200) return
  if (bus.muted) return  // con el mic silenciado no hay barge-in que hacer
  _lastBargeAt = now
  const refs = _liveRefs
  const hadTurn = !!currentBotTurnId
  try {
    if (refs && refs.pc) refs.pc.getSenders().forEach(s => { if (s.track && s.track.kind === 'audio') s.track.enabled = true })
  } catch {}
  try { if (refs && refs.audioEl) { refs.audioEl.pause(); refs.audioEl.currentTime = 0; refs.audioEl.play().catch(() => {}) } } catch {}
  bus.set({ spkBot: false, remoteLevel: 0, remBands: null })
  pushTranscript('sys', tr('interrumpido — te escucho', 'interrupted — I am listening'))
  if (!refs || !hadTurn) return
  const tid = currentBotTurnId
  currentBotTurnId = ''
  ;(async () => {
    try { await refs.ctx.rest('/codexlive/interrupt', { method: 'POST', body: { turnId: tid }, timeoutMs: 5000 }) } catch {}
  })()
}

function maybeBarge() {
  if (bus.muted) { _botSpeakingSince = 0; return }
  const now = Date.now()
  if (bus.spkBot) {
    if (!_botSpeakingSince) _botSpeakingSince = now
    if (now - _botSpeakingSince > 600 && (bus.micLevel || 0) > 0.11) doBargeIn()
  } else {
    _botSpeakingSince = 0
  }
}

function handleRealtimeEvent(msg, dc, ctx) {
  const t = msg && msg.type
  if (!t) return
  // ── Codex Live-1: protocolo v3/frameless ──
  if (t === 'session.started' || t === 'session.updated') return
  if (t === 'input_transcript.added') { const tx = (msg.item || {}).text; if (tx) pushUserDelta(tx); return }
  if (t === 'output_transcript.added') { const tx = (msg.item || {}).text; if (tx) pushTranscript('bot', tx); return }
  if (t === 'turn.done') {
    const tu = msg.turn || {}
    if (tu.role === 'user') finalizeUser(tu.transcript || '')
    else if (tu.role === 'assistant') endBotTranscript(tu.transcript || '')
    return
  }
  if (t === 'turn.created') {
    const tu = msg.turn || {}
    if (tu.role === 'assistant' && tu.id) currentBotTurnId = String(tu.id)
    return
  }
  if (t === 'turn.delta') return
  if (t === 'session.usage.updated') { const au = msg.usage && msg.usage.audio_duration_ms; if (au) { try { liveAudioMs = Math.max(liveAudioMs || 0, Number(au) || 0) } catch {} } return }
  if (t === 'delegation.created') { handleDelegation(ctx, msg, dc); return }
  if (t === 'delegation.context.appended') {
    if (handoffMode === 'server') {
      const did = String(msg.delegation_item_id || '')
      if (did && did !== _lastAppendedId) { _lastAppendedId = did; pushTranscript('tool', tr('respuesta del agente en camino a la voz…', 'agent response streaming to the voice…')) }
    }
    return
  }
  // ── Hermes realtime (realtime-2.1) ──
  if (t === 'input_audio_buffer.speech_started') bus.set({ spkUser: true })
  else if (t === 'input_audio_buffer.speech_stopped') bus.set({ spkUser: false })
  else if (t === 'output_audio_buffer.started') bus.set({ spkBot: true })
  else if (t === 'output_audio_buffer.stopped') bus.set({ spkBot: false })
  else if (t === 'conversation.item.input_audio_transcription.delta') pushUserDelta(msg.delta || '')
  else if (t === 'conversation.item.input_audio_transcription.completed') finalizeUser(msg.transcript || '')
  else if (t === 'response.output_audio_transcript.delta') pushTranscript('bot', msg.delta || '')
  else if (t === 'response.output_audio_transcript.done') endBotTranscript(msg.transcript || '')
  else if (t === 'response.function_call_arguments.done') {
    let args = {}
    try { args = JSON.parse(msg.arguments || '{}') } catch {}
    try { runVoiceTool(ctx, msg.call_id, msg.name || 'tool', args, dc).catch(() => {}) } catch {}
  } else if (t === 'error') {
    const m = msg.error && (msg.error.message || msg.error.code)
    if (m) pushTranscript('sys', 'error: ' + String(m).slice(0, 160))
  }
}

// ── loop de animación: budgeted (SDK) con fallback rAF ───────────────────────

function makeLoop(draw, fps) {
  // rAF como clock principal + heartbeat por intervalo: si el renderer pausa rAF
  // (ventana ocluida/throttling), la animación igual late (menos fps).
  let raf = 0
  let last = -Infinity
  let lastBeat = Date.now()
  const step = t => {
    lastBeat = Date.now()
    if (t - last >= 1000 / fps) { last = t; try { draw(t) } catch {} }
    raf = requestAnimationFrame(step)
  }
  raf = requestAnimationFrame(step)
  const iv = setInterval(() => {
    if (Date.now() - lastBeat > 400) { try { draw(performance.now()) } catch {} }
  }, 120)
  return {
    dispose: () => { try { cancelAnimationFrame(raf) } catch {}; try { clearInterval(iv) } catch {} },
    wake() {}, isDormant: () => false
  }
}

function disposeLoop(loop) { try { loop && loop.dispose && loop.dispose() } catch {} }

// ── sonidos de micro-interacción (WebAudio, sin assets) ──────────────────────

function playChime(kind) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)()
    const now = ctx.currentTime
    const notes = kind === 'connect' ? [523.25, 659.25, 783.99] : [659.25, 523.25, 392.0]
    notes.forEach((f, i) => {
      const o = ctx.createOscillator()
      const g = ctx.createGain()
      o.type = 'sine'
      o.frequency.value = f
      g.gain.setValueAtTime(0, now + i * 0.08)
      g.gain.linearRampToValueAtTime(0.12, now + i * 0.08 + 0.02)
      g.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.08 + 0.28)
      o.connect(g); g.connect(ctx.destination)
      o.start(now + i * 0.08)
      o.stop(now + i * 0.08 + 0.3)
    })
    setTimeout(() => { try { ctx.close() } catch {} }, 900)
  } catch {}
}

// ── sesión realtime ──────────────────────────────────────────────────────────

async function mintSession(ctx, profile, voice, allowChat) {
  const sess = await ctx.rest('/session', {
    method: 'POST',
    body: { language: EN ? 'en' : 'es', profile: profile || null, voice: voice || null, allowChat: allowChat !== false },
    timeoutMs: 45000
  })
  if (!sess || !sess.clientSecret) throw new Error('respuesta sin clientSecret: ' + JSON.stringify(sess).slice(0, 120))
  return sess
}

async function startLive(ctx, { profile, voice, micId, outId, engine, log }) {
  const eng = engine || 'codex'
  clearTranscript()
  liveAudioMs = 0
  bus.set({ err: '', widget: true, stage: 'mint' })
  log('pidiendo sesión…')
  let sess = null
  if (eng !== 'codex') {
    sess = await mintSession(ctx, profile, voice, (ctx.storage.get(KEY_CHAT) || '1') !== '0')
    bus.botName = sess.botName || ''
  }
  log('abriendo micrófono…')
  const audioC = { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
  if (micId) audioC.deviceId = { exact: micId }
  let stream
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: audioC })
  } catch (e) {
    if (micId) {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
        pushTranscript('sys', tr('No pude abrir el micrófono elegido; usé el predeterminado del sistema.', 'Could not open the selected mic; using the system default.'))
      } catch (e2) {
        throw new Error('MIC: ' + ((e2 && e2.name) || e?.name || '') + ' — revisa permisos o prueba otro micrófono en el engranaje')
      }
    } else {
      throw new Error('MIC: ' + (e?.name || '') + ' — revisa permisos o prueba otro micrófono en el engranaje')
    }
  }
  log('mic OK')
  bus.set({ live: true, stage: 'offering', micLevel: 0, remoteLevel: 0 })

  const audioCtx = new AudioContext()
  if (audioCtx.state === 'suspended') { try { await audioCtx.resume() } catch {} }
  const analyserMic = audioCtx.createAnalyser(); analyserMic.fftSize = 512
  // grafo de medición: mantiene el analyser alimentado independientemente del destination
  const micMute = audioCtx.createGain(); micMute.gain.value = 0
  audioCtx.createMediaStreamSource(stream).connect(analyserMic)
  analyserMic.connect(micMute); micMute.connect(audioCtx.destination)
  const remoteRef = { an: null }

  // niveles por WebRTC stats (independiente de WebAudio — fallback sólido en Electron)
  const meter = { sMic: 0, sBot: 0 }
  // umbral de barge-in: solo si el bot lleva >600ms hablando (evita cortar en pausas cortas)
  let botSpeakingSince = 0
  const maybeBarge = () => {
    const now = Date.now()
    if (bus.spkBot) {
      if (!botSpeakingSince) botSpeakingSince = now
      if (now - botSpeakingSince > 600 && (bus.micLevel || 0) > 0.11) doBargeIn()
    } else {
      botSpeakingSince = 0
    }
  }

  const pc = new RTCPeerConnection()
  stream.getTracks().forEach(t => pc.addTrack(t, stream))
  _liveRefs = { pc, audioEl: null, ctx }

  // canal de eventos: transcripción en vivo + llamadas a tools
  const dc = pc.createDataChannel('oai-events')
  dc.onopen = () => log('canal de eventos abierto')
  dc.onmessage = ev => {
    let msg = null
    try { msg = JSON.parse(ev.data) } catch { return }
    handleRealtimeEvent(msg, dc, ctx)
  }

  let audioEl = null
  pc.ontrack = ev => {
    log('audio del bot conectado')
    const remoteStream = ev.streams[0]
    audioEl = new Audio()
    audioEl.srcObject = remoteStream
    audioEl.autoplay = true
    audioEl.play().catch(() => log('click para reproducir audio'))
  if (_liveRefs) _liveRefs.audioEl = audioEl
    if (outId && audioEl.setSinkId) audioEl.setSinkId(outId).catch(e => log('salida: ' + e.message))
    const an = audioCtx.createAnalyser(); an.fftSize = 256
    const src = audioCtx.createMediaStreamSource(remoteStream)
    const mute = audioCtx.createGain(); mute.gain.value = 0
    src.connect(an); an.connect(mute); mute.connect(audioCtx.destination)
    remoteRef.an = an
  }

  let closed = false
  let connectedAt = 0
  // cierra bubbles inactivos (2.6s) para separar intervenciones sin duplicar
  const idleTimer = setInterval(() => {
    const now = Date.now()
    let changed = false
    for (let i = 0; i < transcript.length; i++) {
      const it = transcript[i]
      if (!it.done && now - (it.at || 0) > IDLE_MS) { it.done = true; changed = true }
    }
    if (changed) _bumpTranscript()
  }, 1000)
  const ctl = { close: null }
  // Mute del micrófono: alterna enabled en los tracks de audio del PC (robusto a fallback de device).
  let mutedNow = false
  ctl.toggleMute = () => {
    mutedNow = !mutedNow
    try {
      pc.getSenders().forEach(s => { if (s.track && s.track.kind === 'audio') s.track.enabled = !mutedNow })
    } catch {}
    bus.set({ muted: mutedNow })
    pushTranscript('sys', mutedNow ? tr('Micrófono silenciado', 'Microphone muted') : tr('Micrófono activo', 'Microphone on'))
    return mutedNow
  }

  pc.onconnectionstatechange = () => {
    log('rtc: ' + pc.connectionState)
    bus.set({ stage: pc.connectionState })
    if (pc.connectionState === 'connected') { playChime('connect'); if (!connectedAt) connectedAt = Date.now() }
    if (pc.connectionState === 'failed') {
      try { ctl.close && ctl.close() } catch {}
      bus.set({ err: 'conexión perdida (failed)', widget: true, stage: 'error', live: false, micLevel: 0, remoteLevel: 0 })
    }
  }

  log('negociando…')
  const offer = await pc.createOffer()
  await pc.setLocalDescription(offer)
  let codexSession = null
  if (eng === 'codex') {
    const r2 = await ctx.rest('/codexlive/session', {
      method: 'POST',
      body: { language: EN ? 'en' : 'es', profile: profile || null, voice: voice || 'cove', offer: offer.sdp },
      timeoutMs: 120000
    })
    if (!r2 || !r2.answer) {
      try { stream.getTracks().forEach(t => t.stop()) } catch {}
      throw new Error('codex live: sin SDP de respuesta')
    }
    codexSession = r2
    handoffMode = (r2.handoff === 'server') ? 'server' : 'client'
    if (r2.handoffDegraded) pushTranscript('sys', tr('Aviso: este servidor no soporta clientManagedHandoffs; las tareas de voz pueden ejecutarse en el lane ChatGPT del agente en vez de tu chat.', 'Notice: this server does not support clientManagedHandoffs; voice tasks may run on the agent ChatGPT lane instead of your chat.'))
    bus.botName = r2.botName || bus.botName || ''
    await pc.setRemoteDescription({ type: 'answer', sdp: r2.answer })
  } else {
    const res = await fetch(sess.offerUrl + '?model=' + encodeURIComponent(sess.model), {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + sess.clientSecret, 'Content-Type': 'application/sdp' },
      body: offer.sdp
    })
    if (!res.ok) {
      const t = await res.text().catch(() => '')
      stream.getTracks().forEach(t => t.stop())
      throw new Error('offer ' + res.status + ': ' + t.slice(0, 140))
    }
    await pc.setRemoteDescription({ type: 'answer', sdp: await res.text() })
  }
  log('en vivo')

  // poll de niveles WebRTC cada 250ms (audioLevel outbound=mic / inbound=bot)
  const statsTimer = setInterval(() => {
    try {
      pc.getStats().then(report => {
        try {
          report.forEach(r => {
            // Chrome: outbound-rtp.audioLevel es null (el mic va por WebAudio);
            // inbound-rtp SÍ trae audioLevel (voz del bot).
            const kind = r.kind || r.mediaType
            if (kind !== 'audio') return
            if (r.type === 'inbound-rtp' && typeof r.audioLevel === 'number') meter.sBot = Math.max(r.audioLevel, meter.sBot * 0.8)
          })
        } catch {}
      }).catch(() => {})
    } catch {}
  }, 250)

  const buf = new Uint8Array(analyserMic.frequencyBinCount)
  const bufR = new Uint8Array(256)
  const tdM = new Uint8Array(analyserMic.fftSize)
  const tdR = new Uint8Array(256)
  const NB = 6
  const binOf = f => Math.min(buf.length - 1, Math.round(f / (audioCtx.sampleRate / 2) * buf.length))
  const edges = [100, 250, 500, 900, 1600, 2800, 4500].map(binOf)
  const bandAvg = (arr, a, b) => { let s = 0; const n = Math.max(1, b - a); for (let i = a; i < b; i++) s += arr[i]; return s / n / 255 }
  let lastPush = 0
  let lastEmit = 0
  const tick = () => {
    if (closed) return
    let m = 0, r = 0
    const bands = [0, 0, 0, 0, 0, 0]
    const remBands = [0, 0, 0, 0, 0, 0]
    let rmsM = 0, rmsR = 0
    try {
      analyserMic.getByteFrequencyData(buf)
      for (let i = 0; i < NB; i++) {
        bands[i] = bandAvg(buf, edges[i], edges[i + 1])
        if (bands[i] > m) m = bands[i]
      }
      analyserMic.getByteTimeDomainData(tdM)
      for (let i = 0; i < tdM.length; i++) { const v = (tdM[i] - 128) / 128; rmsM += v * v }
      rmsM = Math.sqrt(rmsM / tdM.length) * 3.2
      if (remoteRef.an) {
        remoteRef.an.getByteFrequencyData(bufR)
        for (let i = 0; i < NB; i++) {
          remBands[i] = bandAvg(bufR, edges[i], edges[i + 1])
          if (remBands[i] > r) r = remBands[i]
        }
        remoteRef.an.getByteTimeDomainData(tdR)
        for (let i = 0; i < tdR.length; i++) { const v = (tdR[i] - 128) / 128; rmsR += v * v }
        rmsR = Math.sqrt(rmsR / tdR.length) * 3.2
      }
    } catch {}
    // combinar fuentes: RMS (time-domain) + espectro + stats del bot
    const micLevel = bus.muted ? 0 : Math.min(1, Math.max(m, rmsM))
    const botLevel = Math.min(1, Math.max(r, rmsR, meter.sBot))
    const now = performance.now()
    if (now - lastPush > 55) {
      lastPush = now
      // escritura directa para las animaciones (rAF lee los campos);
      // emit a React solo ~5fps — evita el re-render storm que laggeaba el drag
      bus.micLevel = micLevel; bus.remoteLevel = botLevel; bus.bands = bands; bus.remBands = remBands
      try { maybeBarge() } catch {}
      if (now - lastEmit > 200) { lastEmit = now; bus.emit() }
    }
  }
  const meterLoop = makeLoop(tick, 24)

  ctl.close = () => {
    if (closed) return
    closed = true
    if (bus.stage === 'connected') playChime('hangup')
    try { clearInterval(statsTimer) } catch {}
    try { clearInterval(idleTimer) } catch {}
    try { for (let i = 0; i < transcript.length; i++) transcript[i].done = true } catch {}
    try { disposeLoop(meterLoop) } catch {}
    try {
      const durMs = connectedAt ? Date.now() - connectedAt : 0
      if (durMs > 4000) {
        ctx.rest('/voice/usage/report', { method: 'POST', body: { durationMs: Math.round(durMs), audioMs: Math.round(liveAudioMs || 0) }, timeoutMs: 8000 }).catch(() => {})
      }
    } catch {}
    try { audioEl && audioEl.pause() } catch {}
    try { stream.getTracks().forEach(t => t.stop()) } catch {}
    try { if (eng === 'codex' && codexSession && codexSession.threadId) ctx.rest('/codexlive/stop', { method: 'POST', body: { threadId: codexSession.threadId }, timeoutMs: 8000 }).catch(() => {}) } catch {}
    try { pc.close() } catch {}
    try { audioCtx.close() } catch {}
    _liveRefs = null
    currentBotTurnId = ''
    _botSpeakingSince = 0
    bus.set({ live: false, stage: 'idle', micLevel: 0, remoteLevel: 0, widget: false, muted: false })
  }
  return {
    close: () => { try { ctl.close && ctl.close() } catch {} },
    toggleMute: () => { try { return ctl.toggleMute() } catch {} return false }
  }
}

function useLiveState() {
  const [s, setS] = useState(() => ({ live: bus.live, stage: bus.stage, micLevel: bus.micLevel, remoteLevel: bus.remoteLevel, widget: bus.widget, err: bus.err, transcriptRev: bus.transcriptRev, spkUser: bus.spkUser, spkBot: bus.spkBot, muted: bus.muted }))
  useEffect(() => {
    const h = e => setS({ ...e.detail })
    window.addEventListener('talk-desktop:state', h)
    return () => window.removeEventListener('talk-desktop:state', h)
  }, [])
  return s
}

// ── onda viva: barras por loop JS, reactivas a voz real ──────────────────────

function LiveBars({ size = 13, count = 5 }) {
  const refs = useRef([])
  useEffect(() => {
    const lvl = new Array(count).fill(0.25)
    const draw = () => {
      const now = performance.now()
      const live = bus.live && bus.stage === 'connected'
      const muted = bus.muted === true
      const botActive = (bus.remoteLevel || 0) > (bus.micLevel || 0)
      const spec = botActive ? bus.remBands : bus.bands
      const level = Math.max(bus.micLevel || 0, bus.remoteLevel || 0)
      let specAlive = false
      if (spec) { for (let i = 0; i < spec.length; i++) { if (spec[i] > 0.02) { specAlive = true; break } } }
      const mid = (count - 1) / 2
      for (let i = 0; i < count; i++) {
        const el = refs.current[i]
        if (!el) continue
        const w = 1 - (Math.abs(i - mid) / (mid + 1)) * 0.35
        let target
        if (!live) {
          target = 0.24 + 0.14 * Math.sin(now / 700 + i * 1.15)
        } else if (muted && !botActive) {
          // mic muteado: respiración calmada; si el bot habla, se ven sus ondas normales
          target = 0.20 + 0.08 * Math.sin(now / 900 + i * 1.1)
        } else if (specAlive && spec) {
          target = Math.max(0.10, Math.pow(Math.min(1, (spec[i] || 0) * 1.9), 0.72) * w)
        } else {
          const v = Math.pow(Math.min(1, level * 1.8), 0.72)
          target = Math.max(0.10, v * (0.55 + 0.45 * w) + 0.06 * Math.sin(now / 380 + i))
          const speakingNow = botActive ? bus.spkBot : bus.spkUser
          if (speakingNow && level < 0.03) {
            // el servidor confirmó habla (eventos) pero el medidor aún no da señal: patrón vivaz
            target = 0.22 + 0.50 * Math.abs(Math.sin(now / (150 + i * 25) + i * 1.7)) * w
          }
        }
        lvl[i] = target > lvl[i] ? lvl[i] + (target - lvl[i]) * 0.55 : lvl[i] + (target - lvl[i]) * 0.15
        const h = Math.max(2, Math.round(lvl[i] * size))
        el.style.height = h + 'px'
        el.style.opacity = live ? String(0.72 + Math.min(0.28, lvl[i] * 0.5)) : '0.5'
        el.style.background = !live ? '#a1a1aa' : (botActive ? '#a78bfa' : (muted ? '#9ca3af' : '#60a5fa'))
        el.style.boxShadow = 'none'
      }
    }
    const loop = makeLoop(draw, 24)
    return () => disposeLoop(loop)
  }, [])
  return jsx('span', {
    'aria-hidden': 'true',
    style: { display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 2, height: size },
    children: Array.from({ length: count }, (_, i) => jsx('span', {
      ref: el => { refs.current[i] = el },
      style: { width: 2.5, height: 2, borderRadius: 2, background: '#a1a1aa', display: 'block' }
    }))
  })
}

function SpinnerRing({ inset = -3 }) {
  const ref = useRef(null)
  useEffect(() => {
    const loop = makeLoop(t => {
      const el = ref.current
      if (el) el.style.transform = 'rotate(' + Math.round(((t / 3.2) % 360)) + 'deg)'
    }, 30)
    return () => disposeLoop(loop)
  }, [])
  return jsx('span', {
    'aria-hidden': 'true',
    style: {
      position: 'absolute', inset, borderRadius: '50%',
      border: '2px solid rgba(59,130,246,0.18)', borderTopColor: '#3b82f6',
      borderRightColor: '#3b82f6', pointerEvents: 'none', display: 'block'
    },
    ref
  })
}

// ── preview de voz: genera una muestra real con la voz elegida ───────────────

let _pvCache = null
let _pvSession = null
let _pvAudio = null

function _pvStopSession() {
  const cur = _pvSession
  if (!cur) return
  _pvSession = null
  try { cur.rec && cur.rec.state !== 'inactive' && cur.rec.stop() } catch {}
  try { cur.timer && clearTimeout(cur.timer) } catch {}
  try { cur.stream && cur.stream.getTracks().forEach(t => t.stop()) } catch {}
  try { cur.pc && cur.pc.close() } catch {}
  try { cur.ctxRef && cur.threadId && cur.ctxRef.rest('/codexlive/stop', { method: 'POST', body: { threadId: cur.threadId }, timeoutMs: 8000 }).catch(() => {}) } catch {}
}

function _pvPlay(url, outId) {
  try {
    if (_pvAudio) { try { _pvAudio.pause() } catch {} }
    _pvAudio = new Audio(url)
    if (outId && _pvAudio.setSinkId) _pvAudio.setSinkId(outId).catch(() => {})
    _pvAudio.play().catch(() => {})
  } catch {}
}

async function _pvGenerate(ctx, { engine, voice, profile, micId, outId }) {
  const eng = engine || 'codex'
  const sc = { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
  if (micId) sc.deviceId = { exact: micId }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: sc })
  const pc = new RTCPeerConnection()
  stream.getTracks().forEach(t => pc.addTrack(t, stream))
  const dc = pc.createDataChannel('oai-events')
  let remoteStream = null
  pc.ontrack = ev => { remoteStream = (ev.streams && ev.streams[0]) || remoteStream }
  const offer = await pc.createOffer()
  await pc.setLocalDescription(offer)
  let threadId = null
  if (eng === 'codex') {
    const r = await ctx.rest('/codexlive/session', { method: 'POST', body: { language: EN ? 'en' : 'es', profile: profile || null, voice, offer: offer.sdp }, timeoutMs: 120000 })
    if (!r || !r.answer) throw new Error(tr('muestra: sin SDP de respuesta', 'sample: no answer SDP'))
    threadId = r.threadId || null
    await pc.setRemoteDescription({ type: 'answer', sdp: r.answer })
  } else {
    const sess = await mintSession(ctx, profile, voice, false)
    const res = await fetch(sess.offerUrl + '?model=' + encodeURIComponent(sess.model), {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + sess.clientSecret, 'Content-Type': 'application/sdp' },
      body: offer.sdp
    })
    if (!res.ok) throw new Error('muestra: offer ' + res.status)
    await pc.setRemoteDescription({ type: 'answer', sdp: await res.text() })
  }
  _pvSession = { key: eng + ':' + voice, stream, pc, rec: null, timer: null, ctxRef: ctx, threadId }
  await new Promise((resolve, reject) => {
    const t0 = Date.now()
    const iv = setInterval(() => {
      if (pc.connectionState === 'connected') { clearInterval(iv); resolve() }
      else if (pc.connectionState === 'failed' || Date.now() - t0 > 25000) { clearInterval(iv); reject(new Error(tr('muestra: no conectó', 'sample: did not connect'))) }
    }, 200)
  })
  await new Promise(r => setTimeout(r, 500))
  const phrase = tr(
    'Di en voz alta exactamente esta frase, sin agregar nada más ni saludar: «Hola, esta es la voz ' + voice + ', así sueno cuando hablamos.»',
    'Say out loud exactly this sentence, adding nothing else: "Hi, this is voice ' + voice + ', this is how I sound when we talk."')
  try {
    if (eng === 'codex') {
      dc.send(JSON.stringify({ type: 'session.context.append', content: [{ type: 'input_text', text: phrase }] }))
    } else {
      dc.send(JSON.stringify({ type: 'conversation.item.create', item: { type: 'message', role: 'user', content: [{ type: 'input_text', text: phrase }] } }))
      dc.send(JSON.stringify({ type: 'response.create' }))
    }
  } catch {}
  const url = await new Promise((resolve, reject) => {
    const chunks = []
    const t0 = Date.now()
    const startRec = () => {
      if (!remoteStream) {
        if (Date.now() - t0 > 12000) { reject(new Error(tr('muestra: sin audio del bot', 'sample: no bot audio'))); return }
        setTimeout(startRec, 250); return
      }
      let mime = ''
      try { if (window.MediaRecorder && MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) mime = 'audio/webm;codecs=opus' } catch {}
      let mr = null
      try { mr = mime ? new MediaRecorder(remoteStream, { mimeType: mime }) : new MediaRecorder(remoteStream) } catch (e) { reject(e); return }
      if (_pvSession) _pvSession.rec = mr
      const stopSoon = () => { try { if (mr.state !== 'inactive') mr.stop() } catch {} }
      mr.ondataavailable = e => { try { if (e.data && e.data.size) chunks.push(e.data) } catch {} }
      mr.onstop = () => {
        try {
          const blob = new Blob(chunks, { type: mr.mimeType || 'audio/webm' })
          if (!blob.size) { reject(new Error(tr('muestra: sin datos', 'sample: no data'))); return }
          resolve(URL.createObjectURL(blob))
        } catch (e) { reject(e) }
      }
      dc.onmessage = ev2 => {
        try {
          const m = JSON.parse(ev2.data)
          const done = (m.type === 'turn.done' && ((m.turn || {}).role === 'assistant')) || m.type === 'response.output_audio_transcript.done'
          if (done) setTimeout(stopSoon, 1300)
        } catch {}
      }
      mr.start()
      const hard = setTimeout(stopSoon, 13000)
      if (_pvSession) _pvSession.timer = hard
    }
    startRec()
  })
  const cur = _pvSession
  if (cur) {
    try { cur.stream.getTracks().forEach(t => t.stop()) } catch {}
    try { pc.close() } catch {}
    try { if (threadId) ctx.rest('/codexlive/stop', { method: 'POST', body: { threadId }, timeoutMs: 8000 }).catch(() => {}) } catch {}
    try { cur.timer && clearTimeout(cur.timer) } catch {}
    _pvSession = null
  }
  _pvCache = { key: eng + ':' + voice, url }
  _pvPlay(url, outId)
  return url
}

// ── formulario de configuración (Popover / widget / pane comparten esto) ────

function ConfigForm({ ctx, dense = false }) {
  const [voice, setVoice] = useState(() => ctx.storage.get(KEY_VOICE) || 'marin')
  const [profile, setProfile] = useState(() => ctx.storage.get(KEY_PROFILE) || '')
  const [micId, setMicId] = useState(() => ctx.storage.get(KEY_MIC) || '')
  const [outId, setOutId] = useState(() => ctx.storage.get(KEY_OUT) || '')
  const [chatWork, setChatWork] = useState(() => (ctx.storage.get(KEY_CHAT) || '1') !== '0')
  const [engine, setEngine] = useState(() => ctx.storage.get(KEY_ENGINE) || 'codex')
  const [micTest, setMicTest] = useState({ on: false, err: '' })
  const [pvVoice, setPvVoice] = useState({ state: 'idle', voice: '' })
  const micBarRef = useRef(null)
  const micTestRef = useRef(null)
  const focusedProf = useValue(sdk.host.state.focusedSessionProfile)
  const autoName = (() => { const f = String(focusedProf || ''); return f && f !== 'default' ? f : '' })()
  const [bots, setBots] = useState([])
  const [devs, setDevs] = useState({ mics: [], outs: [] })
  const [info, setInfo] = useState(null)

  useEffect(() => {
    ctx.rest('/status', { timeoutMs: 20000 })
      .then(r => { setBots(r.bots || []); setInfo(r) })
      .catch(e => setInfo({ error: String(e?.message || e).slice(0, 100) }))
    navigator.mediaDevices?.enumerateDevices?.()
      .then(list => setDevs({ mics: list.filter(d => d.kind === 'audioinput'), outs: list.filter(d => d.kind === 'audiooutput') }))
      .catch(() => {})
  }, [])
  useEffect(() => { ctx.storage.set(KEY_VOICE, voice) }, [voice])
  useEffect(() => { ctx.storage.set(KEY_PROFILE, profile) }, [profile])
  useEffect(() => { ctx.storage.set(KEY_MIC, micId) }, [micId])
  useEffect(() => { ctx.storage.set(KEY_OUT, outId) }, [outId])
  useEffect(() => { ctx.storage.set(KEY_CHAT, chatWork ? '1' : '0') }, [chatWork])
  useEffect(() => { ctx.storage.set(KEY_ENGINE, engine) }, [engine])
  const runPreview = async () => {
    if (bus.live) { try { sdk.host && sdk.host.notifyError && sdk.host.notifyError(tr('Colgá la llamada activa antes de probar voces.', 'Hang up the active call before testing voices.'), 'Live Voice') } catch {} return }
    if (pvVoice.state === 'load') return
    const eng = engine || 'codex'
    const v = effVoiceFor(eng, voice)
    if (_pvCache && _pvCache.key === eng + ':' + v) {
      _pvPlay(_pvCache.url, outId || '')
      setPvVoice({ state: 'play', voice: v })
      setTimeout(() => setPvVoice(s => (s.state === 'play' && s.voice === v) ? { state: 'idle', voice: '' } : s), 1500)
      return
    }
    setPvVoice({ state: 'load', voice: v })
    try {
      await _pvGenerate(ctx, { engine: eng, voice: v, profile, micId: micId || '', outId: outId || '' })
      setPvVoice({ state: 'play', voice: v })
      setTimeout(() => setPvVoice(s => (s.state === 'play' && s.voice === v) ? { state: 'idle', voice: '' } : s), 1800)
    } catch (e) {
      setPvVoice({ state: 'idle', voice: '' })
      try { sdk.host && sdk.host.notifyError && sdk.host.notifyError(String((e && e.message) || e), 'Live Voice') } catch {}
    }
  }
  useEffect(() => () => { try { _pvStopSession() } catch {} }, [])
  // al cambiar de bot/ventana el perfil vuelve a "seguir la ventana"
  useEffect(() => { setProfile('') }, [String(focusedProf || '')])
  useEffect(() => () => { try { stopMicTest() } catch {} }, [])

  const field = { display: 'flex', flexDirection: 'column', gap: 4, marginBottom: dense ? 6 : 8 }
  const label = { fontSize: 10.5, fontWeight: 600, color: 'var(--ui-text-tertiary, #71717a)', textTransform: 'uppercase', letterSpacing: '0.05em' }
  const trigStyle = { width: '100%', minWidth: 0, overflow: 'hidden' }
  const valStyle = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block', flex: '1 1 auto', minWidth: 0 }
  const opt = text => jsx('span', { title: text, style: { display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 232 }, children: text })

  const pick = (next, set) => v => { set(v === SENTINEL_DEFAULT ? '' : v) }

  const stopMicTest = () => {
    const t = micTestRef.current
    if (t) {
      try { t.stream.getTracks().forEach(x => x.stop()) } catch {}
      try { t.ctx.close() } catch {}
      try { cancelAnimationFrame(t.raf) } catch {}
    }
    micTestRef.current = null
    setMicTest({ on: false, err: '' })
  }
  const toggleMicTest = async () => {
    if (micTest.on) { stopMicTest(); return }
    setMicTest({ on: true, err: '' })
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: micId ? { deviceId: { exact: micId } } : true })
      const actx = new AudioContext()
      try { await actx.resume() } catch {}
      const an = actx.createAnalyser(); an.fftSize = 512
      const mute = actx.createGain(); mute.gain.value = 0
      actx.createMediaStreamSource(stream).connect(an); an.connect(mute); mute.connect(actx.destination)
      const buf = new Uint8Array(an.fftSize)
      micTestRef.current = { stream, ctx: actx, raf: 0 }
      const loop = () => {
        const t = micTestRef.current
        if (!t) return
        try {
          an.getByteTimeDomainData(buf)
          let s = 0
          for (let i = 0; i < buf.length; i++) { const d = (buf[i] - 128) / 128; s += d * d }
          const rms = Math.sqrt(s / buf.length)
          if (micBarRef.current) micBarRef.current.style.width = Math.min(100, Math.round(rms * 340)) + '%'
        } catch {}
        t.raf = requestAnimationFrame(loop)
      }
      loop()
      navigator.mediaDevices.enumerateDevices()
        .then(list => setDevs({ mics: list.filter(d => d.kind === 'audioinput'), outs: list.filter(d => d.kind === 'audiooutput') }))
        .catch(() => {})
    } catch (e) {
      setMicTest({ on: false, err: tr('No se pudo abrir el micrófono: ', 'Could not open the microphone: ') + String((e && e.name) || e) })
    }
  }

  return jsxs('div', { style: { display: 'flex', flexDirection: 'column', width: '100%' }, children: [
    jsxs('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }, children: [
      jsx('div', { style: { fontSize: 13, fontWeight: 700, color: 'var(--foreground, #18181b)' }, children: 'Live Voice' }),
      info && jsx('div', {
        style: { fontSize: 10.5, fontWeight: 600, color: info.error ? '#dc2626' : '#3b82f6' },
        children: info.error ? tr('sin host', 'no host') : tr('conectado', 'connected')
      })
    ] }),
    jsxs('div', { style: field, children: [
      jsx('div', { style: label, children: 'Bot' }),
      jsxs(Select, { value: profile === '' ? '__auto' : profile, onValueChange: v => setProfile(v === '__auto' ? '' : v), children: [
        jsx(SelectTrigger, { style: trigStyle, children: jsx(SelectValue, { style: valStyle }) }),
        jsxs(SelectContent, { children: [
          jsx(SelectItem, { value: '__auto', children: tr('Automático (según la ventana)', 'Auto (current window)') }),
          jsx(SelectItem, { value: SENTINEL_HOST, children: 'Luna (host)' }),
          ...bots.map(b => jsx(SelectItem, { value: String(b), children: opt(String(b)) }, 'bot-' + b))
        ] })
      ] })
    ] }),
    jsx('div', { style: { fontSize: 10, color: 'var(--ui-text-tertiary, #a1a1aa)', marginTop: -4, marginBottom: 8 }, children: profile === ''
      ? (autoName
        ? tr('sigue la ventana → ', 'follows window → ') + autoName + ' (Bot)'
        : tr('Automático: usa el bot de la ventana activa (si no, Luna).', 'Auto: uses the current window bot (otherwise Luna).'))
      : tr('Fijo: ', 'Fixed: ') + (profile === '__host' ? 'Luna (host)' : profile + ' (Bot)') }),
    jsxs('div', { style: field, children: [
      jsx('div', { style: label, children: tr('Motor de voz', 'Voice engine') }),
      jsxs(Select, { value: engine, onValueChange: setEngine, children: [
        jsx(SelectTrigger, { style: trigStyle, children: jsx(SelectValue, { style: valStyle }) }),
        jsxs(SelectContent, { children: [
          jsx(SelectItem, { value: 'codex', children: opt('Codex Live-1 (gpt-live-1)') }),
          jsx(SelectItem, { value: 'hermes', children: opt('Hermes (gpt-realtime-2.1)') })
        ] })
      ] }),
      jsx('div', { style: { fontSize: 10, color: 'var(--ui-text-tertiary, #a1a1aa)', marginTop: -2, marginBottom: 2 }, children: engine === 'codex'
        ? tr('Con tu suscripción de Codex — recomendado.', 'On your Codex subscription — recommended.')
        : tr('realtime-2.1 (motor anterior).', 'realtime-2.1 (previous engine).') })
    ] }),
    jsxs('div', { style: field, children: [
      jsx('div', { style: label, children: tr('Voz', 'Voice') }),
      jsxs(Select, { value: effVoiceFor(engine, voice), onValueChange: setVoice, children: [
        jsx(SelectTrigger, { style: trigStyle, children: jsx(SelectValue, { style: valStyle }) }),
        jsx(SelectContent, { children: (engine === 'codex' ? V3_VOICES : VOICES).map(v => jsx(SelectItem, { value: v, children: v }, v)) })
      ] }),
      jsxs('div', { style: { display: 'none', alignItems: 'center', gap: 8, marginTop: 6 }, children: [   // preview desactivado por ahora
        jsx(Button, {
          variant: 'secondary', size: 'sm', onClick: runPreview, style: { flexShrink: 0 },
          children: [
            pvVoice.state === 'load' ? jsx('span', { style: { position: 'relative', width: 11, height: 11, display: 'inline-block', marginRight: 6, verticalAlign: '-1px' }, children: jsx(SpinnerRing, { inset: 0 }) }) : null,
            pvVoice.state === 'load' ? tr('Generando muestra…', 'Generating…') : (pvVoice.state === 'play' ? tr('Reproduciendo…', 'Playing…') : tr('▶ Probar voz', '▶ Test voice'))
          ]
        }),
        jsx('div', { style: { fontSize: 10, color: 'var(--ui-text-tertiary, #a1a1aa)', lineHeight: 1.3 }, children: pvVoice.state === 'load' ? tr('La voz está grabando su muestra…', 'The voice is recording its sample…') : tr('Escuchala antes de elegir (queda en caché).', 'Listen before choosing (cached).') })
      ] })
    ] }),
    jsxs('div', { style: { display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 10 }, children: [
      jsx(Switch, { checked: chatWork, onCheckedChange: v => setChatWork(!!v) }),
      jsxs('div', { style: { fontSize: 11.5, lineHeight: 1.3 }, children: [
        tr('Trabajar en el chat', 'Work in the chat'),
        jsx('div', { style: { fontSize: 10, color: 'var(--ui-text-tertiary, #a1a1aa)' }, children: tr('Las tareas se ejecutan en el chat abierto, con sus tools y su modelo (tu config de Hermes). La voz no ejecuta nada por su cuenta.', 'Tasks run in the open chat with its tools and model (your Hermes config). The voice never runs anything on its own.') }),
      ] })
    ] }),
    jsx('div', { style: { ...label, marginTop: 6, marginBottom: 6, borderTop: '1px solid var(--ui-stroke-secondary, rgba(127,127,127,0.25))', paddingTop: 8 }, children: tr('Audio', 'Audio') }),
    jsxs('div', { style: field, children: [
      jsx('div', { style: label, children: tr('Micrófono', 'Microphone') }),
      jsxs(Select, { value: micId || SENTINEL_DEFAULT, onValueChange: pick(micId, setMicId), children: [
        jsx(SelectTrigger, { style: trigStyle, children: jsx(SelectValue, { style: valStyle }) }),
        jsxs(SelectContent, { children: [
          jsx(SelectItem, { value: SENTINEL_DEFAULT, children: tr('Predeterminado del sistema', 'System default') }),
          ...devs.mics.map((d, i) => jsx(SelectItem, { value: String(d.deviceId), children: opt(d.label || ('Micrófono ' + (i + 1))) }, 'mic-' + i))
        ] })
      ] })
    ] }),
    jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginTop: -4, marginBottom: 10 }, children: [
      jsx(Button, {
        variant: micTest.on ? 'destructive' : 'secondary', size: 'sm',
        onClick: toggleMicTest,
        style: { flexShrink: 0 },
        children: micTest.on ? tr('Detener', 'Stop') : tr('Probar', 'Test')
      }),
      jsx('div', { style: { flex: 1, height: 7, borderRadius: 4, background: 'rgba(127,127,127,0.22)', overflow: 'hidden' }, children:
        jsx('div', { ref: micBarRef, style: { height: '100%', width: '0%', background: micTest.err ? '#dc2626' : '#22c55e' } })
      })
    ] }),
    micTest.on && !micTest.err && jsx('div', { style: { fontSize: 10, color: '#16a34a', marginTop: -6, marginBottom: 8 }, children: tr('Habla y mira la barra: si no se mueve, este micrófono no capta.', 'Speak and watch the bar: if it stays flat, this mic is not capturing.') }),
    micTest.err && jsx('div', { style: { fontSize: 10.5, color: '#dc2626', marginTop: -6, marginBottom: 8, lineHeight: 1.4 }, children: micTest.err }),
    jsxs('div', { style: { ...field, marginBottom: 4 }, children: [
      jsx('div', { style: label, children: tr('Salida', 'Output') }),
      jsxs(Select, { value: outId || SENTINEL_DEFAULT, onValueChange: pick(outId, setOutId), children: [
        jsx(SelectTrigger, { style: trigStyle, children: jsx(SelectValue, { style: valStyle }) }),
        jsxs(SelectContent, { children: [
          jsx(SelectItem, { value: SENTINEL_DEFAULT, children: tr('Predeterminado del sistema', 'System default') }),
          ...devs.outs.map((d, i) => jsx(SelectItem, { value: String(d.deviceId), children: opt(d.label || ('Salida ' + (i + 1))) }, 'out-' + i))
        ] })
      ] })
    ] }),
    devs.mics.length === 0 && jsx('div', { style: { fontSize: 10.5, color: 'var(--ui-text-tertiary, #a1a1aa)', lineHeight: 1.4 }, children: tr('Los dispositivos aparecen tras conceder el micrófono la primera vez.', 'Devices appear after granting the microphone the first time.') }),
    jsx('div', { style: { borderTop: '1px solid var(--ui-stroke-secondary, rgba(127,127,127,0.25))', marginTop: 6, paddingTop: 8 }, children: jsx(CodexSection, { ctx }) })
  ] })
}

// ── íconos ───────────────────────────────────────────────────────────────────

function CodexSection({ ctx }) {
  const [st, setSt] = useState(null)
  const [busy, setBusy] = useState(false)
  const [showInfo, setShowInfo] = useState(false)
  const [confirmOut, setConfirmOut] = useState(false)
  const [localMsg, setLocalMsg] = useState('')
  const [usage, setUsage] = useState(null)
  const [usageBusy, setUsageBusy] = useState(false)
  const usageBusyRef = useRef(false)

  const loadUsage = () => {
    if (usageBusyRef.current) return
    usageBusyRef.current = true
    setUsageBusy(true)
    const codexP = ctx.rest('/codex/usage', { timeoutMs: 30000 }).catch(e => ({ ok: false, error: String(e?.message || e).slice(0, 100) }))
    const voiceP = ctx.rest('/voice/usage', { timeoutMs: 15000 }).catch(() => ({ ok: false }))
    Promise.all([codexP, voiceP]).then(pair => {
      const cx = pair[0] && typeof pair[0] === 'object' ? pair[0] : { ok: false }
      setUsage(Object.assign({}, cx, { voice: pair[1] || null }))
    }).finally(() => { usageBusyRef.current = false; setUsageBusy(false) })
  }
  const load = () => {
    ctx.rest('/codex/status', { timeoutMs: 20000 })
      .then(r => setSt(r))
      .catch(e => setSt({ error: String(e?.message || e).slice(0, 120) }))
  }
  useEffect(() => { load() }, [])
  const loginStatus = st && st.login ? st.login.status : null
  useEffect(() => {
    if (loginStatus !== 'pending') return
    const t = setInterval(load, 2500)
    return () => clearInterval(t)
  }, [loginStatus])

  useEffect(() => { loadUsage() }, [])
  // refresco automático mientras la config está abierta
  useEffect(() => {
    const t = setInterval(() => { try { loadUsage() } catch {} }, 60000)
    return () => clearInterval(t)
  }, [])
  useEffect(() => { if (loginStatus === 'done') loadUsage() }, [loginStatus])

  const refreshSoon = () => { setTimeout(load, 700); setTimeout(load, 3000) }
  const start = async () => {
    if (busy) return
    setBusy(true); setLocalMsg('')
    try { await ctx.rest('/codex/login/start', { method: 'POST' }); refreshSoon() }
    catch (e) { setLocalMsg(String(e?.message || e).slice(0, 140)) }
    finally { setBusy(false) }
  }
  const cancel = async () => {
    try { await ctx.rest('/codex/login/cancel', { method: 'POST' }) } catch {}
    refreshSoon()
  }
  const logout = async () => {
    setBusy(true); setLocalMsg('')
    try { await ctx.rest('/codex/logout', { method: 'POST' }); setConfirmOut(false); refreshSoon() }
    catch (e) { setLocalMsg(String(e?.message || e).slice(0, 140)) }
    finally { setBusy(false) }
  }
  const copy = text => { try { navigator.clipboard?.writeText(text) } catch {} }

  let chip = { text: '…', color: 'var(--ui-text-tertiary, #71717a)' }
  if (st && st.error) chip = { text: tr('sin host', 'no host'), color: '#dc2626' }
  else if (st) {
    const oauth = st.codexOauth
    const lane = st.lane
    if (st.login && st.login.status === 'pending') chip = { text: tr('esperando…', 'waiting…'), color: '#ca8a04' }
    else if (lane === 'codex-oauth' && (oauth === 'valid' || oauth === 'expired')) chip = { text: tr('Activa', 'Active') + (oauth === 'valid' ? '' : tr(' (renueva al usar)', ' (auto-renews)')), color: '#16a34a' }
    else if (lane === 'configured' || lane === 'env') chip = { text: 'API key', color: '#ca8a04' }
    else chip = { text: tr('Sin sesión', 'No session'), color: '#dc2626' }
  }
  const pending = st && st.login && st.login.status === 'pending'
  const loginErr = st && st.login && st.login.status === 'error' ? (st.login.message || '') : ''
  const doneMsg = st && st.login && st.login.status === 'done' ? (st.login.message || tr('Sesión iniciada', 'Signed in')) : ''
  const active = st && st.lane === 'codex-oauth' && (st.codexOauth === 'valid' || st.codexOauth === 'expired')

  const cbtn = (label, onClick, tone) => jsx('button', {
    type: 'button', onClick, disabled: busy,
    style: {
      flex: 1, padding: '6px 0', borderRadius: 8, fontSize: 11.5, fontWeight: 600,
      cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.6 : 1,
      border: '1px solid ' + (tone === 'danger' ? 'rgba(220,38,38,0.45)' : tone === 'muted' ? 'rgba(127,127,127,0.4)' : 'rgba(59,130,246,0.45)'),
      background: tone === 'danger' ? 'rgba(220,38,38,0.08)' : tone === 'muted' ? 'transparent' : 'rgba(59,130,246,0.10)',
      color: tone === 'danger' ? '#dc2626' : tone === 'muted' ? 'var(--ui-text-tertiary, #71717a)' : '#3b82f6'
    },
    children: label
  })

  return jsxs('div', { style: { display: 'flex', flexDirection: 'column' }, children: [
    jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }, children: [
      jsx('div', { style: { flex: 1, fontSize: 10.5, fontWeight: 600, color: 'var(--ui-text-tertiary, #71717a)', textTransform: 'uppercase', letterSpacing: '0.05em' }, children: tr('Sesión', 'Session') }),
      jsx('button', {
        type: 'button', title: tr('Qué es esto', 'What is this'),
        onClick: () => setShowInfo(x => !x),
        style: {
          width: 16, height: 16, borderRadius: '50%', padding: 0, lineHeight: 1,
          border: '1px solid rgba(127,127,127,0.45)', cursor: 'pointer',
          background: showInfo ? '#3b82f6' : 'transparent',
          color: showInfo ? '#ffffff' : 'var(--ui-text-tertiary, #71717a)',
          fontSize: 10, fontWeight: 700
        },
        children: 'i'
      }),
      jsx('div', { style: { fontSize: 11, fontWeight: 700, color: chip.color }, children: chip.text })
    ] }),
    showInfo && jsx('div', {
      style: { fontSize: 11, lineHeight: 1.5, color: 'var(--ui-text-tertiary, #71717a)', background: 'rgba(127,127,127,0.08)', borderRadius: 8, padding: '8px 10px', marginBottom: 8 },
      children: tr('La voz usa la suscripción de ChatGPT vía Codex OAuth (no una API key). Las credenciales viven solo en el servidor: este equipo nunca las ve. "Activa" = lista y se renueva sola. Iniciar sesión: te doy un link y un código; ábrelo en tu navegador e ingrésalo. Cerrar sesión desconecta la cuenta del servidor (voz y Codex CLI del VPS); se guarda un respaldo.', 'Voice uses the ChatGPT subscription via Codex OAuth (not an API key). Credentials live on the server only — this machine never sees them. "Active" = ready and self-renewing. Sign in: you get a link and a code; open it in your browser and enter it. Sign out disconnects the account from the server (voice + VPS Codex CLI); a backup is kept.')
    }),
    pending && jsxs('div', { style: { fontSize: 11.5, marginBottom: 8, lineHeight: 1.7 }, children: [
      jsxs('div', { children: [
        tr('1. Abre: ', '1. Open: '),
        jsx('span', { style: { fontWeight: 600 }, children: 'auth.openai.com/codex/device' }),
        ' ',
        jsx('button', { type: 'button', onClick: () => copy(st.login.url || 'https://auth.openai.com/codex/device'), title: 'Copiar link', style: { border: 'none', background: 'none', color: '#3b82f6', cursor: 'pointer', fontSize: 11, padding: 0 }, children: tr('(copiar)', '(copy)') })
      ] }),
      jsxs('div', { children: [
        tr('2. Código: ', '2. Code: '),
        jsx('span', { style: { fontFamily: 'ui-monospace, monospace', fontWeight: 700, fontSize: 13, letterSpacing: '0.06em' }, children: st.login.code || '…' }),
        ' ',
        jsx('button', { type: 'button', onClick: () => copy(st.login.code || ''), title: 'Copiar código', style: { border: 'none', background: 'none', color: '#3b82f6', cursor: 'pointer', fontSize: 11, padding: 0 }, children: tr('(copiar)', '(copy)') })
      ] }),
      jsx('div', { style: { color: '#ca8a04' }, children: tr('Esperando aprobación… (expira en ~15 min)', 'Waiting for approval… (expires in ~15 min)') })
    ] }),
    doneMsg && jsx('div', { style: { fontSize: 11, color: '#16a34a', marginBottom: 6 }, children: doneMsg }),
    loginErr && jsx('div', { style: { fontSize: 11, color: '#dc2626', marginBottom: 6, whiteSpace: 'pre-wrap' }, children: loginErr }),
    localMsg && jsx('div', { style: { fontSize: 11, color: '#dc2626', marginBottom: 6 }, children: localMsg }),
    (usage && usage.ok) && jsxs('div', { style: { marginBottom: 8 }, children: [
      jsxs('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }, children: [
        jsx('div', { style: { fontSize: 10, fontWeight: 700, color: 'var(--ui-text-tertiary, #71717a)', textTransform: 'uppercase', letterSpacing: '0.05em' }, children: tr('Uso', 'Usage') }),
        jsxs('button', { type: 'button', title: 'Actualizar uso', onClick: loadUsage, style: { display: 'inline-flex', alignItems: 'center', gap: 4, border: 'none', background: 'none', color: usageBusy ? 'var(--ui-text-tertiary, #71717a)' : '#3b82f6', cursor: usageBusy ? 'default' : 'pointer', fontSize: 11, padding: 0 }, children: [
          usageBusy
            ? jsx('span', { style: { position: 'relative', width: 11, height: 11, display: 'inline-block' }, children: jsx(SpinnerRing, { inset: 0 }) })
            : jsx('svg', { width: 11, height: 11, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.2, strokeLinecap: 'round', children: [jsx('path', { d: 'M21 12a9 9 0 1 1-3-6.7' }), jsx('path', { d: 'M21 3v6h-6' })] }),
          tr('actualizar', 'refresh')
        ] })
      ] }),
      (usage.voice && usage.voice.ok) && (function () {
        const v = usage.voice
        const r5 = v.rolling5h || {}
        const r5min = Number((r5.audioMinutes != null && r5.audioMinutes > 0) ? r5.audioMinutes : (r5.minutes || 0))
        const cap = VOICE_CAP_MIN(usage.plan)
        const lo = cap ? cap[0] : 0
        const pct = lo ? Math.max(0, Math.min(100, Math.round(r5min / lo * 100))) : 0
        const color = pct >= 80 ? '#dc2626' : pct >= 50 ? '#ca8a04' : '#16a34a'
        const capTxt = cap ? ('~' + cap[0] + (cap[1] !== cap[0] ? '-' + cap[1] : '') + ' min') : ''
        const used = r5min >= 1 ? (Math.round(r5min * 10) / 10).toString().replace('.', ',') + ' min' : Math.round(r5min * 60) + ' s'
        const r24 = v.rolling24h || {}
        const r24min = Math.round(Number(r24.audioMinutes > 0 ? r24.audioMinutes : (r24.minutes || 0)))
        return jsxs('div', { style: { marginBottom: 8 }, children: [
          jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', fontSize: 10.5, color: 'var(--ui-text-tertiary, #71717a)' }, children: [
            jsx('span', { children: 'Live Voice' + tr(' · 5 h rodantes', ' · rolling 5 h') }),
            jsx('span', { style: { fontWeight: 700, color: cap ? color : 'var(--ui-text-tertiary, #71717a)' }, children: used + (capTxt ? ' / ' + capTxt : '') })
          ] }),
          cap && jsx('div', { style: { height: 4, borderRadius: 2, background: 'rgba(127,127,127,0.18)', marginTop: 2, overflow: 'hidden' }, children:
            jsx('div', { style: { height: 4, width: pct + '%', background: color } }) }),
          jsxs('div', { style: { fontSize: 10, color: 'var(--ui-text-tertiary, #71717a)', marginTop: 2, display: 'flex', justifyContent: 'space-between' }, children: [
            jsx('span', { children: '24 h: ' + r24min + ' min' }),
            jsx('span', { children: ((v.week && v.week.sessions) || 0) + tr(' sesiones · 7 d: ', ' sessions · 7 d: ') + Math.round((v.week && v.week.minutes) || 0) + ' min' })
          ] }),
          jsx('div', { style: { fontSize: 9.5, color: 'var(--ui-text-tertiary, #71717a)', marginTop: 1 }, children: ((v.week && v.week.sessions) || 0) > 0
            ? tr('min de voz activa (medido al colgar) · allowance: voz de Codex del plan', 'active voice min (measured on hangup) · plan allowance: Codex voice')
            : tr('se registra al colgar cada llamada', 'recorded when each call ends') })
        ] })
      })(),
      (usage.windows && Object.keys(usage.windows).length > 0) && jsx('div', { style: { fontSize: 10, fontWeight: 700, color: 'var(--ui-text-tertiary, #71717a)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 3 }, children: 'Codex' + (usage.plan ? ' · ' + usage.plan : '') }),
      ...Object.keys(usage.windows || {}).map(label => {
        const w = usage.windows[label] || {}
        const pct = Math.max(0, Math.min(100, Number(w.usedPercent) || 0))
        const color = pct >= 85 ? '#dc2626' : pct >= 60 ? '#ca8a04' : '#16a34a'
        let reset = ''
        if (w.resetsAt) {
          try {
            const num = Number(w.resetsAt)
            const ms = num > 1e12 ? num : num * 1000
            const dt = new Date(ms)
            reset = tr('resetea ', 'resets ') + dt.toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
          } catch {}
        }
        return jsxs('div', { style: { fontSize: 10.5, color: 'var(--ui-text-tertiary, #71717a)', marginBottom: 5 }, children: [
          jsxs('div', { style: { display: 'flex', justifyContent: 'space-between' }, children: [
            jsx('span', { children: label }),
            jsx('span', { style: { fontWeight: 700, color }, children: pct + tr('% usado', '% used') + ' · ' + (100 - pct) + tr('% libre', '% left') + (pct >= 90 ? tr(' — cerca del límite', ' — near limit') : '') })
          ] }),
          jsx('div', { style: { height: 4, borderRadius: 2, background: 'rgba(127,127,127,0.18)', marginTop: 2, overflow: 'hidden' }, children:
            jsx('div', { style: { height: 4, width: pct + '%', background: color } }) }),
          reset && jsx('div', { style: { marginTop: 1 }, children: reset })
        ] }, 'ux-' + label)
      })
    ] }),
    confirmOut
      ? jsxs('div', { children: [
          jsx('div', { style: { fontSize: 11, color: '#dc2626', lineHeight: 1.5, marginBottom: 6 }, children: tr('¿Cerrar la sesión de ChatGPT del servidor? Afecta la voz y el Codex CLI del VPS. Se guarda un respaldo.', 'Sign out the server ChatGPT session? Affects voice and the VPS Codex CLI. A backup is kept.') }),
          jsxs('div', { style: { display: 'flex', gap: 6 }, children: [
            cbtn(tr('Confirmar cierre', 'Confirm sign-out'), logout, 'danger'),
            cbtn(tr('Cancelar', 'Cancel'), () => setConfirmOut(false), 'muted')
          ] })
        ] })
      : jsxs('div', { style: { display: 'flex', gap: 6 }, children: [
          pending ? cbtn(tr('Reiniciar', 'Restart'), start) : cbtn(active ? tr('Reiniciar sesión', 'Restart sign-in') : tr('Iniciar sesión', 'Sign in'), start),
          pending ? cbtn(tr('Cancelar', 'Cancel'), cancel, 'danger') : (active ? cbtn(tr('Cerrar sesión', 'Sign out'), () => setConfirmOut(true), 'danger') : null)
        ] })
  ] })
}

function HangupIcon({ size = 15, color = 'currentColor' }) {
  return jsx('svg', {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
    stroke: color, strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round',
    style: { transform: 'rotate(133deg)' },
    children: jsx('path', { d: 'M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z' })
  })
}

function MicIcon({ size = 15, color = 'currentColor' }) {
  return jsx('svg', {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
    stroke: color, strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round',
    children: [
      jsx('path', { d: 'M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z' }),
      jsx('path', { d: 'M19 10v2a7 7 0 0 1-14 0v-2' }),
      jsx('line', { x1: 12, y1: 19, x2: 12, y2: 22 })
    ]
  })
}

function MicOffIcon({ size = 15, color = 'currentColor' }) {
  return jsxs('svg', {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
    stroke: color, strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round',
    children: [
      jsx('line', { x1: 2, y1: 2, x2: 22, y2: 22 }),
      jsx('path', { d: 'M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V5a3 3 0 0 0-5.94-.6' }),
      jsx('path', { d: 'M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23' }),
      jsx('line', { x1: 12, y1: 19, x2: 12, y2: 22 })
    ]
  })
}

function GearIcon({ size = 12, color = 'currentColor' }) {
  return jsx('svg', {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
    stroke: color, strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round',
    children: [
      jsx('circle', { cx: 12, cy: 12, r: 3 }),
      jsx('path', { d: 'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z' })
    ]
  })
}

// ── botón del composer ───────────────────────────────────────────────────────

function TrIcon({ size = 13, color = 'currentColor' }) {
  return jsx('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: color, strokeWidth: 2.2, strokeLinecap: 'round', children: [
    jsx('line', { x1: 4, y1: 7, x2: 20, y2: 7 }),
    jsx('line', { x1: 4, y1: 12, x2: 20, y2: 12 }),
    jsx('line', { x1: 4, y1: 17, x2: 13, y2: 17 })
  ] })
}

// Panel de transcripción: popover anclado al botón (transitorio, se cierra solo).
function LivePanel() {
  const s = useLiveState()
  const stop = () => { try { window.__talkLiveHandle?.close() } catch {} }
  const dot = s.err ? '#dc2626' : (s.stage === 'connected' ? '#22c55e' : '#f59e0b')
  const status = s.err ? tr('error', 'error') : (s.spkBot ? tr('hablando', 'speaking') : (s.spkUser ? tr('te escucho', 'listening') : (s.stage === 'connected' ? tr('en vivo', 'live') : s.stage)))
  return jsxs('div', { style: { display: 'flex', flexDirection: 'column' }, children: [
    jsxs('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '10px 14px 6px' }, children: [
      jsxs('div', { style: { display: 'flex', alignItems: 'center', minWidth: 0 }, children: [
        jsx('span', { style: { width: 7, height: 7, borderRadius: '50%', flexShrink: 0, marginRight: 7, background: dot } }),
        jsx('div', { style: { fontSize: 12.5, fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: (bus.botName || 'Bot') + ' (Bot)' })
      ] }),
      jsx('div', { style: { fontSize: 10.5, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', flexShrink: 0, color: s.err ? '#dc2626' : (s.spkBot ? '#7c3aed' : '#3b82f6') }, children: status })
    ] }),
    jsx(TranscriptView, { max: 240 }),
    jsx('div', { style: { padding: '6px 14px 12px' }, children: jsx(Button, {
      variant: 'destructive',
      onClick: stop,
      style: { width: '100%' },
      children: tr('Colgar', 'Hang up')
    }) })
  ] })
}

function ComposerLiveButton({ ctx }) {
  const s = useLiveState()
  const [busy, setBusy] = useState(false)
  const [cfg, setCfg] = useState(false)
  const [micHover, setMicHover] = useState(false)
  const [muteHover, setMuteHover] = useState(false)
  const [gearHover, setGearHover] = useState(false)
  const [trHover, setTrHover] = useState(false)
  const [showTr, setShowTr] = useState(false)
  const [profile, setProfile] = useState(() => ctx.storage.get(KEY_PROFILE) || '')
  const [voice, setVoice] = useState(() => ctx.storage.get(KEY_VOICE) || 'marin')
  const [micId, setMicId] = useState(() => ctx.storage.get(KEY_MIC) || '')
  const [outId, setOutId] = useState(() => ctx.storage.get(KEY_OUT) || '')
  const btnRef = useRef(null)
  const botsRef = useRef(null)
  const focusedProfile = useValue(sdk.host.state.focusedSessionProfile)
  const autoBot = () => {
    const f = String(focusedProfile || '')
    if (!f || f === 'default') return ''
    return f
  }
  // al cambiar de bot/ventana, el perfil vuelve a seguir la ventana
  useEffect(() => {
    setProfile('')
    try { ctx.storage.set(KEY_PROFILE, '') } catch {}
  }, [String(focusedProfile || '')])
  useEffect(() => {
    ctx.rest('/status', { timeoutMs: 20000 })
      .then(r => { botsRef.current = new Set(r.bots || []) })
      .catch(() => {})
  }, [])

  // el form puede editarse desde widget/pane → releer storage
  useEffect(() => {
    const t = setInterval(() => {
      const p = ctx.storage.get(KEY_PROFILE) || ''
      const v = ctx.storage.get(KEY_VOICE) || 'marin'
      const mi = ctx.storage.get(KEY_MIC) || ''
      const o = ctx.storage.get(KEY_OUT) || ''
      setProfile(prev => (prev === p ? prev : p))
      setVoice(prev => (prev === v ? prev : v))
      setMicId(prev => (prev === mi ? prev : mi))
      setOutId(prev => (prev === o ? prev : o))
    }, 700)
    return () => clearInterval(t)
  }, [])

  // click derecho: bonus (la app lo intercepta más arriba; si llega, abre config)
  useEffect(() => {
    const h = e => {
      const el = btnRef.current
      if (!el) return
      const wrap = el.parentElement
      if (!wrap || !wrap.contains(e.target)) return
      e.preventDefault(); e.stopPropagation()
      setCfg(x => !x)
    }
    document.addEventListener('contextmenu', h, true)
    return () => document.removeEventListener('contextmenu', h, true)
  }, [])

  const toggle = async () => {
    if (s.live) { try { window.__talkLiveHandle?.close() } catch {}; return }
    if (busy || bus.live) return
    try { window.__talkLiveHandle?.close() } catch {}
    const effProfile = profile === '__host' ? '' : (profile || autoBot())
    const eng = ctx.storage.get(KEY_ENGINE) || 'codex'
    const vv = effVoiceFor(eng, ctx.storage.get(KEY_VOICE) || '')
    setBusy(true)
    try {
      window.__talkLiveHandle = await startLive(ctx, { profile: effProfile, voice: vv, micId, outId, engine: eng, log: () => {} })
    } catch (e) {
      let m = String(e?.message || e)
      if (m.indexOf('LIVE_SIN_QUOTA') >= 0) {
        m = m.replace('LIVE_SIN_QUOTA: ', '')
        try {
          ctx.storage.set(KEY_ENGINE, 'realtime')
          pushTranscript('sys', tr('Plan semanal sin quota: motor cambiado a gpt-realtime-2.1 — prueba de nuevo.', 'Weekly plan out of quota: engine switched to gpt-realtime-2.1 — try again.'))
        } catch {}
      }
      m = m.slice(0, 240)
      bus.set({ err: m, live: false, stage: 'error', widget: true, micLevel: 0, remoteLevel: 0 })
      try { if (sdk.host && sdk.host.notifyError) sdk.host.notifyError(String(m), 'Live Voice') } catch {}
    } finally { setBusy(false) }
  }

  const live = s.live && s.stage === 'connected'
  useEffect(() => { if (!s.live && showTr) setShowTr(false) }, [s.live, showTr])
  const connecting = busy || (s.live && s.stage !== 'connected' && s.stage !== 'error' && s.stage !== 'failed')
  const cs = 'var(--composer-control-size, 30px)'

  return jsxs('span', {
    'data-context-menu-skip': 'true',
    style: { display: 'inline-flex', alignItems: 'center', gap: 3 },
    children: [
      jsx('button', {
        ref: btnRef,
        type: 'button',
        'data-context-menu-skip': 'true',
        title: live ? 'Colgar Live Voice' : 'Live Voice — click para hablar',
        onClick: toggle,
        onMouseEnter: () => setMicHover(true),
        onMouseLeave: () => setMicHover(false),
        style: {
          width: cs, height: cs, borderRadius: '50%', padding: 0, position: 'relative',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          border: 'none', cursor: 'pointer', overflow: 'visible',
          background: live
            ? (micHover ? '#1e293b' : '#0f172a')
            : (micHover ? 'var(--chrome-action-hover, rgba(0,0,0,0.06))' : 'transparent'),
          color: live ? '#ffffff' : (micHover ? 'var(--foreground, #18181b)' : 'var(--ui-text-tertiary, #71717a)'),
          boxShadow: live
            ? (micHover
              ? '0 0 0 1.5px rgba(239,68,68,0.55), 0 2px 12px rgba(239,68,68,0.28)'
              : '0 0 0 1.5px rgba(96,165,250,0.5), 0 2px 10px rgba(37,99,235,0.22)')
            : (micHover ? '0 0 0 1px var(--border, rgba(0,0,0,0.12))' : 'none'),
          transform: micHover ? 'scale(1.05)' : 'scale(1)',
          transition: 'background 160ms, color 160ms, box-shadow 160ms, transform 160ms'
        },
        children: [
          connecting && jsx(SpinnerRing, {}),
          (live && !connecting)
            ? (micHover ? jsx(HangupIcon, { size: 15 }) : jsx(LiveBars, { size: 10, count: 5 }))
            : jsx(MicIcon, { size: 15 })
        ]
      }),
      live && jsx('button', {
        type: 'button',
        'data-context-menu-skip': 'true',
        title: s.muted ? 'Activar micrófono' : 'Silenciar micrófono',
        onClick: () => { try { window.__talkLiveHandle && window.__talkLiveHandle.toggleMute() } catch {} },
        onMouseEnter: () => setMuteHover(true),
        onMouseLeave: () => setMuteHover(false),
        style: {
          width: 24, height: 24, borderRadius: '50%', padding: 0, border: 'none', cursor: 'pointer',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          background: s.muted ? 'rgba(245,158,11,0.22)' : (muteHover ? 'var(--chrome-action-hover, rgba(0,0,0,0.09))' : 'transparent'),
          color: s.muted ? '#f59e0b' : (muteHover ? 'var(--foreground, #18181b)' : 'var(--ui-text-tertiary, #71717a)'),
          boxShadow: s.muted
            ? '0 0 0 1.5px rgba(245,158,11,0.55)'
            : (muteHover ? '0 0 0 1px var(--border, rgba(0,0,0,0.14))' : 'none'),
          opacity: (s.muted || muteHover) ? 1 : 0.8,
          transform: muteHover ? 'scale(1.1)' : 'scale(1)',
          transition: 'background 140ms, color 140ms, box-shadow 140ms, transform 140ms'
        },
        children: s.muted ? jsx(MicOffIcon, { size: 14 }) : jsx(MicIcon, { size: 14 })
      }),
      live && jsx('span', {
        'data-context-menu-skip': 'true',
        style: { display: 'inline-flex', alignItems: 'center' },
        children: jsx(Popover, {
          open: showTr,
          onOpenChange: setShowTr,
          children: [
            jsx(PopoverTrigger, { asChild: true, children: jsx('button', {
              type: 'button',
              'data-context-menu-skip': 'true',
              title: 'Transcripción en vivo',
              onMouseEnter: () => setTrHover(true),
              onMouseLeave: () => setTrHover(false),
              style: {
                width: 22, height: 22, borderRadius: '50%', padding: 0, border: 'none', cursor: 'pointer',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                background: (showTr || trHover) ? 'var(--chrome-action-hover, rgba(0,0,0,0.08))' : 'transparent',
                color: showTr ? '#3b82f6' : (trHover ? 'var(--foreground, #18181b)' : 'var(--ui-text-tertiary, #71717a)'),
                opacity: (showTr || trHover) ? 1 : 0.85,
                transition: 'background 140ms, color 140ms'
              },
              children: jsx(TrIcon, { size: 13 })
            }) }),
            jsx(PopoverContent, { side: 'top', align: 'end', sideOffset: 8, style: { width: 320 },
              onFocusOutside: e => { try { e.preventDefault() } catch {} },
              children: jsx(LivePanel, {}) })
          ]
        })
      }),
      jsx('span', {
        'data-context-menu-skip': 'true',
        style: { display: 'inline-flex', alignItems: 'center' },
        children: jsx(Popover, {
          open: cfg,
          onOpenChange: setCfg,
          children: [
            jsx(PopoverTrigger, { asChild: true, children: jsx('button', {
              type: 'button',
              'data-context-menu-skip': 'true',
              title: 'Configurar Live Voice (bot, voz, micrófono, salida)',
              onMouseEnter: () => setGearHover(true),
              onMouseLeave: () => setGearHover(false),
              style: {
                width: 22, height: 22, borderRadius: '50%', padding: 0, border: 'none', cursor: 'pointer',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                background: (gearHover || cfg) ? 'var(--chrome-action-hover, rgba(0,0,0,0.08))' : 'transparent',
                color: (gearHover || cfg) ? '#3b82f6' : 'var(--ui-text-tertiary, #71717a)',
                opacity: (gearHover || cfg) ? 1 : 0.6,
                transform: gearHover ? 'scale(1.12)' : 'scale(1)',
                transition: 'opacity 140ms, background 140ms, color 140ms, transform 140ms'
              },
              children: jsx(GearIcon, { size: 12 })
            }) }),
            jsx(PopoverContent, {
              side: 'top', align: 'end', sideOffset: 8,
              style: { width: 300 },
              onFocusOutside: e => { try { e.preventDefault() } catch {} },
              children: jsx(ConfigForm, { ctx })
            })
          ]
        })
      }),
      jsx('span', { style: { display: 'none' } })
    ]
  })
}

// ── transcripción en vivo (estilo codex live) ────────────────────────────────

function renderBubble(m, key) {
  if (m.role === 'sys') return jsx('div', { style: { textAlign: 'center', fontSize: 10.5, color: '#8e8e93', lineHeight: 1.4, padding: '0 8px' }, children: m.text }, 'tr-' + key)
  if (m.role === 'tool') return jsx('div', { style: { display: 'flex', justifyContent: 'center' }, children: jsxs('span', {
    style: { display: 'inline-flex', alignItems: 'flex-start', gap: 5, fontSize: 10, color: '#b0b0b6', background: 'rgba(127,127,127,0.14)', border: '1px solid rgba(127,127,127,0.18)', borderRadius: 10, padding: '3px 9px', maxWidth: '92%', lineHeight: 1.45, textAlign: 'left' }, children: [
      jsx('span', { style: { flexShrink: 0, opacity: 0.9 }, children: '⚙︎' }),
      jsx('span', { style: { wordBreak: 'break-word' }, children: m.text })
    ] }) }, 'tr-' + key)
  const isUser = m.role === 'user'
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', alignItems: isUser ? 'flex-end' : 'flex-start' },
    children: [
      jsx('div', { style: { fontSize: 10, fontWeight: 600, color: '#8e8e93', margin: isUser ? '0 7px 1px 0' : '0 0 1px 7px' }, children: isUser ? tr('Tú', 'You') : ((bus.botName || 'Bot') + ' (Bot)') }),
      jsx('div', {
        style: {
          maxWidth: '84%', padding: '6px 11px', borderRadius: 15, fontSize: 12, lineHeight: 1.35,
          background: isUser ? '#0a84ff' : '#2c2c2e', color: '#ffffff',
          borderBottomRightRadius: isUser ? 5 : 15, borderBottomLeftRadius: isUser ? 15 : 5,
          opacity: m.done === false ? 0.72 : 1, whiteSpace: 'pre-wrap', wordBreak: 'break-word'
        },
        children: m.text
      })
    ]
  }, 'tr-' + key)
}

function useTranscriptRev() {
  const [rev, setRev] = useState(bus.transcriptRev)
  useEffect(() => {
    const h = () => setRev(bus.transcriptRev)
    window.addEventListener('talk-desktop:state', h)
    return () => window.removeEventListener('talk-desktop:state', h)
  }, [])
  return rev
}

function TranscriptView({ expanded, onToggle, max = 66 }) {
  const rev = useTranscriptRev()
  const boxRef = useRef(null)
  const atBottomRef = useRef(true)
  const [more, setMore] = useState(false)
  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    if (atBottomRef.current) { el.scrollTop = el.scrollHeight; if (more) setMore(false) }
    else setMore(true)
  }, [rev, expanded])
  const onScroll = () => {
    const el = boxRef.current
    if (!el) return
    const at = el.scrollHeight - el.scrollTop - el.clientHeight < 26
    atBottomRef.current = at
    if (at && more) setMore(false)
  }
  const scrollDown = () => {
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
    atBottomRef.current = true
    setMore(false)
  }
  const items = transcript.slice(-60)
  return jsxs('div', { style: { padding: '0 12px 8px' }, children: [
    jsxs('div', {
      style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4, cursor: onToggle ? 'pointer' : 'default' },
      onClick: onToggle || undefined, children: [
        jsx('div', { style: { fontSize: 10, fontWeight: 700, letterSpacing: '0.05em', color: '#a1a1aa', textTransform: 'uppercase' }, children: tr('Conversación', 'Conversation') }),
        onToggle && jsx('div', { style: { fontSize: 10, color: '#a1a1aa' }, children: expanded ? tr('ocultar ▴', 'hide ▴') : tr('ver todo ▾', 'show all ▾') })
      ]
    }),
    jsx('div', { style: { position: 'relative' }, children: [
      jsx('div', { ref: boxRef, onScroll, style: { maxHeight: max, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 7, paddingTop: 2, paddingBottom: 4 }, children:
        items.length === 0
          ? jsxs('div', { style: { padding: '22px 10px 18px', textAlign: 'center', color: '#8e8e93' }, children: [
              jsx(TrIcon, { size: 18, color: '#8e8e93' }),
              jsx('div', { style: { fontSize: 10.5, marginTop: 7, lineHeight: 1.4 }, children: tr('La transcripción aparece acá cuando hablen.', 'The transcript shows up here when you start talking.') })
            ] })
          : items.map((m, i) => renderBubble(m, i))
      }),
      more && jsx('button', {
        type: 'button', onClick: scrollDown,
        style: { position: 'absolute', bottom: 8, left: '50%', transform: 'translateX(-50%)', border: 'none', borderRadius: 999, padding: '3px 10px', fontSize: 10, fontWeight: 700, cursor: 'pointer', background: '#3b82f6', color: '#ffffff', boxShadow: '0 2px 8px rgba(0,0,0,0.3)', zIndex: 2 },
        children: '↓ ' + tr('nuevos', 'new')
      })
    ] })
  ] })
}

// ── registro de contribuciones ───────────────────────────────────────────────

export default {
  id: 'talk-desktop',
  name: 'Live Voice',
  register(ctx) {
    ctx.register({
      id: 'composer-live',
      area: 'composer.leading',
      render: () => jsx(ComposerLiveButton, { ctx })
    })
  }
}
