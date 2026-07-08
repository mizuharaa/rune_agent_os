# web-design

Design and iterate web UI with the playwright MCP (configured in `.mcp.json`):
never ship a surface you haven't looked at.

- **Trigger:** /webdesign
- **Earn:** `python skills/engine.py use web-design` after each shipped surface

## Process

1. **Palette first, computed not eyeballed** — validate categorical colors with
   the dataviz validator against the actual surface (light AND dark if both ship).
2. **Build → look → critique → iterate.** Serve the page, then with playwright:
   `browser_navigate` → `browser_take_screenshot` → read the screenshot and
   critique it as the designer agent would (slop checklist: template gradients,
   unaligned grids, fake-looking data, dead space, buried key info). Fix. Repeat
   until a pass finds nothing structural. Minimum one full iteration.
3. **Check the states a screenshot hides:** empty data, overflow text, narrow
   viewport (`browser_resize` to 390px), console errors (`browser_console_messages`).
4. **Motion:** transitions for state changes only; respect
   `prefers-reduced-motion`; motion must carry information (see 3d-interaction).
5. Log the iteration on the wire and record the use.
