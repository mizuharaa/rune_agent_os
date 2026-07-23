# Wisp — Product Spec v1

A local AI that lives on your desktop, sees what you see, and gets your work
done. Windows-first. Formerly Rune; Sentient OS (macOS) is the inspiration,
not the ceiling.

## One-line story

> Your computer already has everything — your files, your email, your
> calendar, your browser, your accounts. Wisp is the intelligence layer that
> finally uses them for you, locally.

## Decisions (locked 2026-07-23)

| Decision | Choice |
|---|---|
| Name | **Wisp** |
| Shell | **Electron** (tray, always-on-top mini bar, installer via electron-builder) |
| Engine | Existing **Python stdlib backend** (`dashboard/serve.py`, 127.0.0.1:8817) as sidecar |
| LLM | **Hybrid**: local via Ollama for text work; user's own cloud key (Claude/GPT) for vision + computer-use. Model selector in UI, start/stop controls as in Rune. |
| Platform | Windows 11 first; macOS later |
| Dev tools | Existing Rune agent console moves under a **Developer** section/subtab |

Not Swift: Swift is macOS-native. The Windows equivalent of Sentient's stack
is Python engine + Electron shell + Windows UI Automation + browser CDP.

## Architecture

```text
┌─ Electron shell (app/) ─────────────────────────────┐
│  tray icon · main window · mini bar (alwaysOnTop)   │
│  global hotkey Ctrl+Shift+Space                     │
└──────────────┬──────────────────────────────────────┘
               │ http 127.0.0.1:8817 (same-origin)
┌──────────────▼──────────────────────────────────────┐
│  Python engine (dashboard/serve.py + friends)       │
│  missions · memory/recall · hermes · guard · skills │
│  calendar (MS Graph) · briefing · orchestrator      │
│  NEW: llm router (ollama ⇄ cloud) · action runner   │
└───────┬───────────────┬───────────────┬─────────────┘
        │               │               │
   Ollama (local)   Cloud API      Windows actions
   text: drafts,    vision +       UIA click/type,
   proofread,       computer-use   browser via CDP,
   file Q&A         multi-pass     file ops, shell
```

Multi-pass revision (draft → critique → revise) already exists in
`dashboard/orchestrator.py` (worker/critic rounds) — reuse it for the
LLM router rather than building a new loop.

## v1 pillars (all four selected)

1. **Mini bar + overlay** — the identity. Tray app, close-to-tray,
   `Ctrl+Shift+Space` toggles between the full dashboard and a floating
   prompt bar. Scaffolded in `app/`.
2. **Browser control** — drive the user's real Edge/Chrome over CDP
   (`--remote-debugging-port`): read the page, select elements, fill carts,
   click. Hybrid model: cloud vision plans, engine executes. Every
   irreversible step (checkout, send, delete) gates on a confirm in the
   mini bar — this is the trust story, not a limitation.
3. **Life dashboard** — calendar (exists) + email + writing surface
   (proofread / draft / rewrite) in one place. Gmail/Outlook via Graph +
   Gmail API using the user's own account.
4. **Developer subtab** — current mission tray / agent console / spawn
   tooling relocated under a Developer section with start/stop + model
   selection preserved.

## Feature map (beyond Sentient OS)

Near-term, high leverage:
- **Inbox triage**: overnight pass over email → morning digest folded into
  the existing daily briefing (reuse briefing pipeline).
- **File concierge**: "find the invoice from March", auto-organize Downloads,
  summarize any document from the mini bar. Local index + local model —
  the privacy story earns its keep here.
- **Clipboard intelligence**: copy anything → mini bar offers rewrite,
  reply-to, translate, extract-table.
- **Meeting prep**: 15 min before each calendar event, a card with the
  attendees, the last emails exchanged, and open threads.
- **Form filler**: profile vault (local, encrypted) + browser control =
  fills any signup/checkout/government form.
- **Watchers**: standing instructions — "when the visa appointment page
  changes, tell me"; "when this price drops, add to cart and ask me."

Later, differentiating:
- **Routine recorder**: user does a task once while Wisp watches (screen +
  DOM), Wisp replays it on demand — automation without prompt engineering.
- **Cross-app memory**: hermes memory already exists; extend it to remember
  everything Wisp saw/did so "what was that restaurant Sam mentioned?" works.
- **Overnight machine**: Sentient's "3 AM machine" equivalent — scheduled
  missions run while idle: triage, file organization, briefing prep.
- **Skill store**: `skills/` registry already exists — third-party skills
  are the ecosystem play.

## Startup story

- **Wedge**: "Copilot lives in a chat box. Wisp lives on your computer."
  Microsoft's assistant can't click your PC, read your files without
  uploading them, or act in your logged-in browser sessions. Wisp can,
  because it's local.
- **Privacy as product**: local model for anything touching raw personal
  data; cloud only for planning/vision, with redaction. "Raw data never
  leaves" is the headline, hybrid is the fine print.
- **Trust as UX**: every action previewed and confirmable from the mini
  bar; a full audit log (missions tray already does this). Competitors
  hide the agent; Wisp shows its hands.
- **Monetization**: free local tier → paid tier for cloud-powered
  computer-use, watchers, and overnight runs. Skills marketplace later.

## Roadmap

1. **Now** — repo moved + renamed, Electron shell scaffolded (done).
2. **Backend revamp** — `engine/llm.py` router (Ollama + cloud, model
   registry, multi-pass via orchestrator); `engine/actions.py` (UIA +
   CDP browser control, confirm gates); email integration.
3. **Features** — triage, file concierge, clipboard, meeting prep.
4. **UI/UX revamp** — full glass redesign (separate pass, spec to come:
   resizable glass panels, scroll-triggered hue shifts per feature,
   Phantom-style hover motion, no status dots / emoji slop).
5. **Ship** — electron-builder NSIS installer, auto-update, onboarding
   (pick models, connect accounts), landing page.

## Consent + safety model

- Engine stays bound to 127.0.0.1.
- Purchases, sends, deletes, and credential use always confirm-gate.
- Account credentials in Windows Credential Manager (DPAPI), never in repo
  state files.
- Browser control uses the user's own logged-in profile — Wisp never
  stores passwords for sites, it acts where you're already signed in.
