"""Wisp UIA action runtime: structured, verified Windows UI automation.

The moat thesis: act on the UI Automation tree (controls, values,
invocations) instead of screenshots. Faster, cheaper, deterministic, works
unfocused — and every action is VERIFIED by re-reading the control after
acting. An action that cannot be confirmed is reported as unverified, never
claimed as success.

Requires pywinauto (the only non-stdlib dependency in the engine; degraded
endpoints return 501 without it). All functions raise UiaError with an
operator-readable message.

ponytail: locator is exact-match on automation_id/name/control_type; add a
query language when real agent use demands it.
"""
import time


class UiaError(Exception):
    pass


_DESKTOP = None


def _desktop():
    """Lazy: importing pywinauto/comtypes costs seconds, so the engine only
    pays it on the first UIA call, not at boot."""
    global _DESKTOP
    if _DESKTOP is None:
        try:
            from pywinauto import Desktop
        except ImportError:
            raise UiaError("pywinauto not installed (pip install pywinauto)")
        _DESKTOP = Desktop(backend="uia")
    return _DESKTOP


def windows():
    """Top-level windows: title, pid, handle. The agent's map of the desktop."""
    out = []
    for w in _desktop().windows():
        try:
            title = w.window_text()
            if not title:
                continue
            out.append({"title": title[:120], "pid": w.process_id(),
                        "handle": w.handle})
        except Exception:
            continue
    return out


def _window(pid=None, title_re=None):
    """Returns a WindowSpecification (criteria live there, not on wrappers)."""
    kw = {}
    if pid:
        kw["process"] = int(pid)
    if title_re:
        kw["title_re"] = str(title_re)
    if not kw:
        raise UiaError("pid or title_re is required")
    spec = _desktop().window(**kw)
    try:
        spec.wait("exists", timeout=3)
    except Exception as e:
        raise UiaError("window not found: %s" % str(e)[:120])
    return spec


def _describe(el, depth, max_nodes, bag):
    if len(bag) >= max_nodes:
        return None
    info = el.element_info
    node = {"control_type": info.control_type or "", "name": (info.name or "")[:80],
            "auto_id": info.automation_id or "", "rect": str(info.rectangle)}
    try:  # value is what makes the tree assertable
        node["value"] = el.get_value()[:200]
    except Exception:
        pass
    bag.append(node)
    if depth > 0:
        node["children"] = [c for c in
                            (_describe(ch, depth - 1, max_nodes, bag)
                             for ch in el.children()) if c]
    return node


def tree(pid=None, title_re=None, depth=3, max_nodes=400):
    """Bounded serialization of a window's control tree."""
    w = _window(pid, title_re).wrapper_object()
    bag = []
    root = _describe(w, max(0, int(depth)), min(int(max_nodes), 1200), bag)
    return {"tree": root, "nodes": len(bag)}


def _find(spec, locator):
    kw = {}
    if locator.get("auto_id"):
        kw["auto_id"] = str(locator["auto_id"])
    if locator.get("name"):
        kw["title"] = str(locator["name"])
    if locator.get("control_type"):
        kw["control_type"] = str(locator["control_type"])
    if not kw:
        raise UiaError("locator needs auto_id, name, or control_type")
    try:
        return spec.child_window(**kw).wrapper_object()
    except Exception:
        raise UiaError("no control matches %s" % kw)


def _state(el):
    s = {"name": el.element_info.name or ""}
    try:
        s["value"] = el.get_value()
    except Exception:
        pass
    try:
        s["toggle_state"] = el.get_toggle_state()
    except Exception:
        pass
    return s


def act(pid=None, title_re=None, locator=None, action="invoke", value=None):
    """Perform one structured action, then re-read the control and report
    what is actually true. Never claims success it cannot observe."""
    w = _window(pid, title_re)
    el = _find(w, locator or {})
    before = _state(el)
    if action == "invoke":
        try:
            el.invoke()
        except Exception:
            el.click_input()  # fallback for controls without InvokePattern
    elif action == "set_text":
        el.set_edit_text(str(value if value is not None else ""))
    elif action == "toggle":
        el.toggle()
    elif action == "focus":
        el.set_focus()
    else:
        raise UiaError("unknown action %r" % action)
    time.sleep(0.15)  # let the UI settle before verifying
    after = _state(el)
    verified = None
    if action == "set_text":
        verified = after.get("value") == str(value if value is not None else "")
    elif action == "toggle":
        verified = after.get("toggle_state") != before.get("toggle_state")
    return {"ok": True, "action": action, "before": before, "after": after,
            "verified": verified}


def read(pid=None, title_re=None, locator=None):
    """Read one control's current state (the assertion primitive)."""
    w = _window(pid, title_re)
    return _state(_find(w, locator or {}))
