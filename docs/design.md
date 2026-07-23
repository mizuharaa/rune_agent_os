# Rune Operator Design System

Status: locked for the agentic_OS operations revamp

## Product frame

Rune is an operator workbench for launching, monitoring, intervening in, and recovering agent work. It is not a marketing dashboard. Every surface should answer four questions quickly:

1. What is running?
2. What needs me?
3. What changed?
4. What can I safely do next?

The audience is a technical operator. Copy is concise, concrete, and stateful. Prefer “Stop mission”, “Retrying in 4s”, and “Permission required” over conversational filler.

## Visual direction

- Genre: modern minimal
- App family: workbench
- Navigation: N3 compact side rail with a filled active indicator
- Theme: warm paper neutrals with royal-plum operational accents
- Density: compact but breathable; information hierarchy comes from contrast and spacing, not decorative cards
- Enrichment: preserve Rune's existing grid, plum wordmark treatment, and
  functional music-player character; do not introduce unrelated decoration
- Border radius: restrained; controls may be softly rounded, structural regions remain squared
- Shadows: reserved for overlays and the fixed mission tray
- Icons: use the existing icon system; no emoji as interface chrome

## Macrostructure

Desktop uses a persistent rail, a compact top utility bar, and one primary work surface. The global mission tray is a fixed, bounded layer that can collapse without moving page content.

Mobile collapses the rail into the existing navigation trigger. The mission tray becomes a bottom sheet with a visible close control and Escape support when a hardware keyboard is present.

Primary routes:

- Overview: attention queue, active work, recent outcomes, automation radar
- Agent console: mission history and intervention controls
- Calendar: Month, Week, Day, and Agenda views with date navigation
- Daily briefing: ranked work with explicit evidence and next actions
- Skills: installed capabilities and learning candidates

Shared across routes: rail, utility bar, typography, status grammar, focus treatment, mission tray, empty/error/loading states.

Allowed differences:

- Calendar may use denser grid rules and compact date typography.
- Agent console may use monospaced output and higher-density timelines.
- Daily briefing may use a long-document reading measure.

## Color roles

All authored interface colors come from tokens.css. Royal plum identifies selection and primary action; it does not fill every container. Warm neutrals separate work surfaces. Semantic colors are reserved for actual state.

- ink / ink-2: primary and secondary readable text
- muted: supporting copy that still meets WCAG AA
- paper / paper-2 / paper-3: page, raised region, and selected/quiet region
- rule / rule-strong: structural separation
- accent / accent-hover / accent-soft: primary action and selection
- success: completed or verified
- warning: retrying, degraded, or time-sensitive
- danger: failed, stopped, or destructive action
- info: neutral system progress
- focus: keyboard focus ring

Never encode mission state by color alone. Every status includes text and, where useful, an icon or shape.

## Typography

- Display: Segoe UI Variable Display, Segoe UI, system sans-serif
- Text: Segoe UI Variable Text, Segoe UI, system sans-serif
- Technical: Cascadia Mono, Consolas, monospace

Page titles use the display face at a compact scale. Body and controls use the text face. IDs, timestamps, logs, and machine states use the technical face. Avoid all-caps except short metadata labels.

## Spacing and sizing

The base rhythm is 4px. Common gaps are 4, 8, 12, 16, 24, and 32px. Interactive targets are at least 40px, and 44px on touch-first layouts. Reading content should not exceed 76ch. Dense operational grids may exceed that measure when columns need it.

## Interaction grammar

- Primary actions use a solid accent treatment.
- Secondary actions use a quiet paper treatment with a strong rule.
- Destructive actions use danger text/border until confirmation.
- Each overlay has a visible close control and closes on Escape.
- A running mission always exposes Stop and Open.
- A recoverable failed mission exposes Retry or Continue.
- Completed or stopped missions expose Dismiss.
- “Clear completed” never affects running work.
- Permission-required work is never auto-approved.

## Motion

Preserve Rune's signature motion while keeping operations readable:

- 120ms for hover/focus feedback
- 180ms for tray or panel entry/exit
- 240ms for view transitions that preserve spatial context
- retain the restrained wordmark sheen and the vinyl/equalizer motion that
  communicates playback
- retain route and card transitions where they reinforce continuity

Use the standard and emphasized easing tokens. Do not add looping “working”
animation to mission state or let decorative motion compete with controls.
Respect prefers-reduced-motion by removing transforms and nonessential
transitions, including signature ambient effects.

## Responsive behavior

- At 1100px and above: rail plus full work surface; calendar shows complete Month/Week grids.
- From 720px to 1099px: compact rail; secondary calendar labels reduce before columns collapse.
- Below 720px: navigation drawer, single-column content, horizontal calendar scroller where necessary, mission bottom sheet.
- Never hide stop, permission, or failure state behind a hover-only affordance.

## Accessibility contract

- WCAG AA contrast for text; 3:1 minimum for meaningful component boundaries and focus indicators
- Keyboard access for every action
- Logical focus restoration when overlays close
- aria-live for mission status changes, not raw streaming logs
- Explicit labels for icon-only buttons
- Visible focus ring on both light and accent surfaces

## Token exports

tokens.css is the canonical runtime export.

### Tailwind v4 theme

~~~css
@theme {
  --color-rune-paper: oklch(0.975 0.007 330);
  --color-rune-paper-2: oklch(0.949 0.011 330);
  --color-rune-paper-3: oklch(0.915 0.016 330);
  --color-rune-ink: oklch(0.185 0.022 330);
  --color-rune-muted: oklch(0.455 0.025 330);
  --color-rune-rule: oklch(0.815 0.021 330);
  --color-rune-accent: oklch(0.355 0.115 330);
  --color-rune-danger: oklch(0.49 0.17 25);
  --font-sans: "Segoe UI Variable Text", "Segoe UI", sans-serif;
  --font-display: "Segoe UI Variable Display", "Segoe UI", sans-serif;
  --font-mono: "Cascadia Mono", "Consolas", monospace;
}
~~~

### DTCG

~~~json
{
  "color": {
    "paper": {"$type": "color", "$value": {"colorSpace": "oklch", "components": [0.975, 0.007, 330]}},
    "ink": {"$type": "color", "$value": {"colorSpace": "oklch", "components": [0.185, 0.022, 330]}},
    "muted": {"$type": "color", "$value": {"colorSpace": "oklch", "components": [0.455, 0.025, 330]}},
    "accent": {"$type": "color", "$value": {"colorSpace": "oklch", "components": [0.355, 0.115, 330]}},
    "danger": {"$type": "color", "$value": {"colorSpace": "oklch", "components": [0.49, 0.17, 25]}}
  },
  "space": {
    "1": {"$type": "dimension", "$value": {"value": 4, "unit": "px"}},
    "2": {"$type": "dimension", "$value": {"value": 8, "unit": "px"}},
    "4": {"$type": "dimension", "$value": {"value": 16, "unit": "px"}},
    "6": {"$type": "dimension", "$value": {"value": 24, "unit": "px"}}
  },
  "duration": {
    "fast": {"$type": "duration", "$value": {"value": 120, "unit": "ms"}},
    "normal": {"$type": "duration", "$value": {"value": 180, "unit": "ms"}}
  }
}
~~~

### shadcn variables

~~~css
:root {
  --background: 0.975 0.007 330;
  --foreground: 0.185 0.022 330;
  --card: 0.99 0.004 330;
  --card-foreground: 0.185 0.022 330;
  --primary: 0.355 0.115 330;
  --primary-foreground: 0.98 0.006 330;
  --secondary: 0.949 0.011 330;
  --secondary-foreground: 0.29 0.028 330;
  --muted: 0.949 0.011 330;
  --muted-foreground: 0.455 0.025 330;
  --border: 0.815 0.021 330;
  --input: 0.815 0.021 330;
  --ring: 0.56 0.17 330;
  --destructive: 0.49 0.17 25;
  --destructive-foreground: 0.98 0.006 25;
  --radius: 0.5rem;
}
~~~

## Component contracts

Mission tray:

- fixed and bounded, maximum 42vh desktop / 62vh mobile
- grouped mission row rather than one permanent bubble per message
- internal scrolling with newest active work surfaced
- count badge reports active and attention-needed work honestly
- close collapses presentation; Stop changes process state

Calendar:

- Month is the default overview
- Week and Day expose time placement
- Agenda supports scanning and links to source events
- Prev, Today, and Next preserve the selected view
- event chips show time and title; overflow uses an explicit more count

Automation radar:

- suggestions are derived from observed repeated workflows and current skill coverage
- each suggestion shows evidence, confidence, and a review-first next action
- no candidate auto-installs or auto-executes

Recovery:

- retry transient failures with bounded backoff
- safe repair attempts are visible in mission history
- destructive, credential, billing, external-message, and policy decisions pause for permission
- verification results become feedback for later runs
