# Security Policy

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Use GitHub Private Vulnerability Reporting instead:
**Security tab > Report a vulnerability**
(https://github.com/Synero/hermes-live-voice/security/advisories/new).

A good report includes:

- the affected version or commit,
- the file and line, or the endpoint, involved,
- a minimal, non-destructive reproduction,
- the impact you expect for a typical deployment.

Focused fixes and regression tests are welcome. Please agree on the expected
behavior inside the advisory before opening a public pull request.

## What to expect

- We acknowledge a report within 7 days.
- We triage it, tell you whether we consider it in scope, and agree on a timeline.
- We fix it, credit you in the advisory if you want, and publish after a fix is released.

This is a community project maintained by one person, so timelines are best effort.

## Supported versions

Only the latest release receives security fixes.

## Scope

In scope: the plugin backend (`dashboard/`), the desktop half (`desktop/`), and the way
they handle credentials, delegation and browser control.

Out of scope: vulnerabilities in Hermes itself, Codex, OpenAI services or third-party
dependencies. Please report those upstream.
