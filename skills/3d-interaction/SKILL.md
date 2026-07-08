# 3d-interaction

3D reactive UI with zero dependencies — CSS transforms + pointer math, no
three.js until a real scene demands it.

- **Trigger:** /3d
- **Earn:** `python skills/engine.py use 3d-interaction` after each real use

## Process

1. **Tilt cards:** parent sets `perspective`; on `pointermove` compute cursor
   offset from card center, map to `rotateX/rotateY` (clamp ±6°) via CSS custom
   properties; reset on `pointerleave` with a 300ms ease-out transition.
2. **Depth:** layered `box-shadow` (tight + wide) that deepens on hover;
   `translateZ` child elements for parallax inside tilted cards.
3. **Sheen:** a radial-gradient overlay whose center follows the pointer
   (`--mx/--my` custom properties) sells the 3D read.
4. **Discipline:** one pointermove handler per container (delegate), transforms
   only (never layout properties), and everything inside
   `@media (prefers-reduced-motion: no-preference)` — the static layout must
   stand on its own without motion.
5. Escalate to WebGL/three.js only for an actual 3D scene (models, cameras) —
   never for card tilt.
