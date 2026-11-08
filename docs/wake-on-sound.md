# Wake on sound

In the microphone settings, enable **Wake on sound** to monitor the selected microphone locally while no call is active. It is OFF by default. Only `wakeOnSound.v1` set to `1` enables it. Old experimental timer or speech settings do not enable microphone access.

The composer shows **Local listening** while the local microphone is active. Three consecutive 100 ms samples above RMS 0.045 start the ordinary Live Voice call with the current engine, voice, microphone and output. The local monitor releases its resources before that call starts.

This is a sound-level detector, not speech recognition or a wake-word model. Background noise can start a billable live call. Triggering audio, including a spoken phrase, is not buffered or transcribed into the new call. Wait for the connection before giving your request. There is no promise of retaining an earlier provider conversation.

Calls never end automatically because of silence or task progress. Manual hangup returns to local listening only when Wake on sound is enabled. Disable the switch to stop local monitoring immediately, including a microphone request that resolves later. Disabling wake does not end or mute an already active call. Hang up that call separately.

A failed connection or denied microphone request does not retry automatically. Toggle the switch off and on, or start a call manually, to try again. Completed chat tasks never reopen a call. Their results remain in chat after hangup; ordinary live delegation responses still use the existing protocol.

Last composer unmount and plugin unload release monitoring, active calls, tracks, audio contexts, timers and local chat observers. This requires the desktop SDK's `ctx.onDispose` lifecycle API.

## Offline verification

```sh
node --test tests/wake-on-sound.test.cjs
python -m pytest -q
node --check desktop/plugin.js
bash tools/plugin-load-test.sh
git diff --check
```

The Node harness evaluates the full renderer module with fake media, clocks and backend requests. It does not open a microphone or contact a provider. It verifies resource ownership, cancellation, one-call exclusion, hangup versus failure, delegation and disposal. Real browser microphone permissions, background sampling, layout and audible call behavior still need manual verification.
