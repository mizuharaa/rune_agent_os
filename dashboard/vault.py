"""Wisp secret vault: DPAPI-encrypted secrets with per-mission scoping.

Fixes the credential soup: instead of every agent inheriting every token in
the operator's environment, secrets live DPAPI-encrypted in
state/vault.json (only this Windows account can decrypt), and a mission
sees ONLY the keys explicitly granted to its cid. Ungranted missions get
nothing. Values never appear in API responses or on the wire — names only.

Reuses askpass.py's DPAPI primitives; stdlib otherwise.
"""
import json
import os
import re

import askpass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT = os.path.join(ROOT, "state", "vault.json")
GRANTS = os.path.join(ROOT, "state", "vault-grants.json")
NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class VaultError(Exception):
    pass


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, path)


def set_secret(name, value):
    if not NAME.match(str(name or "")):
        raise VaultError("secret name must be ENV_VAR style (A-Z, 0-9, _)")
    if not value:
        raise VaultError("empty secret value")
    doc = _load(VAULT)
    doc[name] = askpass.protect(str(value))
    _write(VAULT, doc)


def keys():
    return sorted(_load(VAULT))


def forget(name):
    doc = _load(VAULT)
    if name not in doc:
        return False
    del doc[name]
    _write(VAULT, doc)
    grants = _load(GRANTS)  # a forgotten secret can't stay granted anywhere
    changed = False
    for cid in list(grants):
        if name in grants[cid]:
            grants[cid] = [k for k in grants[cid] if k != name]
            changed = True
        if not grants[cid]:
            del grants[cid]
            changed = True
    if changed:
        _write(GRANTS, grants)
    return True


def grant(cid, names):
    """Replace a mission's grant set. Empty list revokes everything."""
    cid = str(cid or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cid):
        raise VaultError("valid mission cid required")
    have = set(_load(VAULT))
    names = [str(n) for n in (names or [])]
    missing = [n for n in names if n not in have]
    if missing:
        raise VaultError("not in vault: %s" % ", ".join(missing))
    grants = _load(GRANTS)
    if names:
        grants[cid] = sorted(set(names))
    else:
        grants.pop(cid, None)
    _write(GRANTS, grants)
    return grants.get(cid, [])


def grants():
    return _load(GRANTS)


def env_for(cid):
    """Decrypted env mapping for ONE mission — only its granted keys."""
    granted = _load(GRANTS).get(str(cid or ""), [])
    if not granted:
        return {}
    doc = _load(VAULT)
    out = {}
    for name in granted:
        blob = doc.get(name)
        if blob:
            try:
                out[name] = askpass.unprotect(blob)
            except OSError:
                continue  # wrong Windows account or corrupt blob: skip, never crash a mission
    return out


if __name__ == "__main__":
    # self-check: round-trip, scoping, revoke-on-forget (no test file in root)
    set_secret("WISP_SELFCHECK_TOKEN", "s3cret-value")
    assert "WISP_SELFCHECK_TOKEN" in keys()
    grant("selfcheck-cid", ["WISP_SELFCHECK_TOKEN"])
    assert env_for("selfcheck-cid") == {"WISP_SELFCHECK_TOKEN": "s3cret-value"}
    assert env_for("other-cid") == {}, "scoping leaked"
    assert forget("WISP_SELFCHECK_TOKEN")
    assert env_for("selfcheck-cid") == {}, "grant survived forget"
    print("VAULT_SELF_CHECK_OK")
