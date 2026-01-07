# saveLoad.py
"""
Save/Load with verbose debug and faithful inventory restoration.

- Uses module-level import of gameSetup only (no class imports).
- Persists dynamic state to save_state.json and story to saveStory.txt.
- Can create a baseline_state.json (only if missing).
- Restores item descriptions and rebuilds inventories deterministically from the
  character save block to avoid "neutralized" items after load.
- Provides _deserialize_to_world(...) for backwards compatibility with undo pipelines.
"""

import json
import os
from typing import List, Dict, Any, Optional

import gameSetup  # use module-level objects

STATE_PATH = "save_state.json"
BASELINE_PATH = "baseline_state.json"
STORY_PATH = "saveStory.txt"


# ---------------------------------------------------------------------
# Collection helpers (no class imports)
# ---------------------------------------------------------------------

def _collect_characters(world) -> List[object]:
    seen = set()
    chars: List[object] = []

    # From areas
    for area in getattr(world, "sub_areas", []) or []:
        for c in getattr(area, "characters", []) or []:
            if id(c) not in seen:
                chars.append(c)
                seen.add(id(c))

    # Ensure player
    p = getattr(gameSetup, "player", None)
    if p is not None and id(p) not in seen:
        chars.append(p)
        seen.add(id(p))

    # Ensure party members are included too
    for c in list(chars):
        for p2 in getattr(c, "party", []) or []:
            if p2 is None:
                continue
            if id(p2) not in seen:
                chars.append(p2)
                seen.add(id(p2))

    return chars


def _collect_items(world, characters) -> List[object]:
    seen = set()
    items: List[object] = []

    # From areas: key_items + items (if present)
    for area in getattr(world, "sub_areas", []) or []:
        for it in getattr(area, "key_items", []) or []:
            if id(it) not in seen:
                items.append(it)
                seen.add(id(it))
        for it in getattr(area, "items", []) or []:
            if id(it) not in seen:
                items.append(it)
                seen.add(id(it))

    # From characters: inventory + weapon + equipment (if present)
    for c in characters or []:
        for it in getattr(c, "inventory", []) or []:
            if id(it) not in seen:
                items.append(it)
                seen.add(id(it))

        w = getattr(c, "weapon", None)
        if w is not None and id(w) not in seen:
            items.append(w)
            seen.add(id(w))

        eq = getattr(c, "equipment", None)
        if isinstance(eq, dict):
            for it in eq.values():
                if it is not None and id(it) not in seen:
                    items.append(it)
                    seen.add(id(it))

    return items


def _find_area_holding(world, item):
    # Look in both key_items and items
    for area in getattr(world, "sub_areas", []) or []:
        if item in (getattr(area, "key_items", []) or []):
            return area
        if item in (getattr(area, "items", []) or []):
            return area
    return None


# ---------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------

def _serialize_current_state() -> dict:
    world = getattr(gameSetup, "drugstore_world", None)
    player = getattr(gameSetup, "player", None)

    if world is None:
        print("[SAVE][ERROR] drugstore_world is None")
        return {}

    characters = _collect_characters(world)
    items = _collect_items(world, characters)

    # Characters
    chars_data: Dict[str, Any] = {}
    for c in characters:
        name = getattr(c, "name", None) or f"Character@{id(c)}"
        inv_names = [getattr(it, "name", str(it)) for it in (getattr(c, "inventory", []) or [])]
        party_names = [getattr(p, "name", str(p)) for p in (getattr(c, "party", []) or [])]

        friendships: Dict[str, Any] = {}
        for other in characters:
            try:
                friendships[getattr(other, "name", str(other))] = c.friendships.get(other, 5)
            except Exception:
                pass

        # equipment is optional and shouldn’t break older loads
        equipment_names: Dict[str, Optional[str]] = {}
        eq = getattr(c, "equipment", None)
        if isinstance(eq, dict):
            for slot, it in eq.items():
                try:
                    equipment_names[str(slot)] = getattr(it, "name", None) if it is not None else None
                except Exception:
                    equipment_names[str(slot)] = None

        chars_data[name] = {
            "uid": getattr(c, "uid", None),
            "name": name,
            "health": getattr(c, "health", 100),
            "controllable": getattr(c, "controllable", False),
            "gender": getattr(c, "gender", 2),
            "current_area": getattr(getattr(c, "current_area", None), "name", None),
            "is_alive": getattr(c, "is_alive", True),
            "has_acted": getattr(c, "has_acted", False),
            "state": getattr(c, "state", ""),
            "weapon": getattr(getattr(c, "weapon", None), "name", None),
            "inventory": inv_names,
            "party": party_names,
            "friendships": friendships,
            "equipment": equipment_names,  # optional
            # OCEAN traits (if present)
            "openness": getattr(c, "openness", 5),
            "conscientiousness": getattr(c, "conscientiousness", 5),
            "extraversion": getattr(c, "extraversion", 5),
            "agreeableness": getattr(c, "agreeableness", 5),
            "neuroticism": getattr(c, "neuroticism", 5),
        }

    # Items
    items_data: Dict[str, Any] = {}
    for it in items:
        iname = getattr(it, "name", None) or f"Item@{id(it)}"
        pos_area_obj = getattr(it, "position", None) or _find_area_holding(world, it)

        items_data[iname] = {
            "uid": getattr(it, "uid", None),
            "name": iname,
            "description": getattr(it, "description", ""),
            "position_area": getattr(pos_area_obj, "name", None),
            "holder": getattr(getattr(it, "holder", None), "name", None),
            "robustness": getattr(it, "robustness", 0),
            "damage": getattr(it, "damage", 0),
            "is_medicine": getattr(it, "is_medicine", False),
            "is_healing_item": getattr(it, "is_healing_item", False),
            "is_weapon": getattr(it, "is_weapon", False),
            "is_equipped": getattr(it, "is_equipped", False),
        }

    # World dynamic bits
    world_data = {
        "title": getattr(world, "title", ""),
        "chaos_state": getattr(world, "chaos_state", 0),
        "current_dilemma": getattr(world, "current_dilemma", ""),
        "current_goal": getattr(world, "current_goal", ""),
        "player_area": getattr(getattr(player, "current_area", None), "name", None) if player else None,
    }

    # Areas + links
    areas: Dict[str, Any] = {}
    links: Dict[str, Any] = {}
    _seen = set()

    for area in getattr(world, "sub_areas", []) or []:
        # links: key is order-independent A::B
        for link in getattr(area, "linking_points", []) or []:
            try:
                a1 = getattr(link, "area_a", None) or getattr(link, "area1", None) or getattr(link, "from_area", None)
                a2 = getattr(link, "area_b", None) or getattr(link, "area2", None) or getattr(link, "to_area", None)
                n1 = getattr(a1, "name", None)
                n2 = getattr(a2, "name", None)
                if not (n1 and n2):
                    continue
                key = "::".join(sorted([n1, n2]))
                if key in _seen:
                    continue
                _seen.add(key)
                links[key] = getattr(link, "description", "")
            except Exception:
                continue

        aname = getattr(area, "name", None) or f"Area@{id(area)}"
        areas[aname] = {
            "uid": getattr(area, "uid", None),
            "name": aname,
            "characters": [getattr(c, "name", str(c)) for c in (getattr(area, "characters", []) or [])],
            "key_items": [getattr(it, "name", str(it)) for it in (getattr(area, "key_items", []) or [])],
            "items": [getattr(it, "name", str(it)) for it in (getattr(area, "items", []) or [])],
        }

    return {
        "world": world_data,
        "characters": chars_data,
        "items": items_data,
        "areas": areas,
        "links": links,
    }


# ---------------------------------------------------------------------
# Backwards-compat undo apply entrypoint
# ---------------------------------------------------------------------

class DeserializeResult:
    """
    Compatible return type for undo pipelines.
    - Can be unpacked: ok, msg = DeserializeResult(...)
    - Can be used as a boolean: if DeserializeResult(...): ...
    """
    __slots__ = ("ok", "msg")

    def __init__(self, ok: bool, msg: str = ""):
        self.ok = bool(ok)
        self.msg = str(msg or "")

    def __iter__(self):
        yield self.ok
        yield self.msg

    def __bool__(self):
        return self.ok

    def __repr__(self):
        return f"DeserializeResult(ok={self.ok}, msg={self.msg!r})"


def _deserialize_to_world(state_or_snapshot, *args, **kwargs):
    """
    Backwards-compatible helper for undo pipelines that expect:
        ok, msg = saveLoad._deserialize_to_world(...)

    Accepts either:
      - raw state dict produced by _serialize_current_state(), OR
      - snapshot wrapper {"state": <state>, "meta": {...}}, OR
      - JSON string containing either of the above.

    Returns: DeserializeResult(ok: bool, msg: str)
    """
    try:
        print("\n[SAVELOAD][_deserialize_to_world] called")
        print("[SAVELOAD][_deserialize_to_world] type:", type(state_or_snapshot).__name__)

        state = state_or_snapshot

        # JSON string?
        if isinstance(state, str):
            state = json.loads(state)

        # wrapper snapshot?
        if isinstance(state, dict) and "state" in state and isinstance(state["state"], dict):
            state = state["state"]

        if not isinstance(state, dict):
            msg = f"Expected dict state, got {type(state).__name__}"
            print("[SAVELOAD][_deserialize_to_world] ERROR:", msg)
            return DeserializeResult(False, msg)

        ok = apply_game_state_dict(state)
        print("[SAVELOAD][_deserialize_to_world] apply_game_state_dict ->", ok)
        return DeserializeResult(bool(ok), "" if ok else "apply_game_state_dict returned False")

    except Exception as ex:
        print("[SAVELOAD][_deserialize_to_world][ERROR]", ex)
        return DeserializeResult(False, str(ex))



# ---------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------

def save_game_state() -> None:
    data = _serialize_current_state()
    if not data:
        print("[SAVE][WARN] no data to save")
        return
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[SAVE] -> {STATE_PATH} | chars={len(data.get('characters', {}))}, items={len(data.get('items', {}))}, areas={len(data.get('areas', {}))}")
    except Exception as ex:
        print("[SAVE][ERROR]", ex)


def _save_to_path(path: str) -> None:
    data = _serialize_current_state()
    if not data:
        print(f"[SAVE][WARN] no data to save to {path}")
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[SAVE] -> {path} | chars={len(data.get('characters', {}))}, items={len(data.get('items', {}))}, areas={len(data.get('areas', {}))}")
    except Exception as ex:
        print(f"[SAVE][ERROR] writing {path}:", ex)


def make_baseline_if_missing() -> None:
    if not os.path.exists(BASELINE_PATH):
        print(f"[BASELINE] Creating baseline at {BASELINE_PATH}...")
        _save_to_path(BASELINE_PATH)
    else:
        print(f"[BASELINE] Exists at {BASELINE_PATH}")


def load_game_state() -> bool:
    if not os.path.exists(STATE_PATH):
        print(f"[LOAD] No save at {STATE_PATH}")
        return False
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as ex:
        print("[LOAD][ERROR] reading state:", ex)
        return False

    print(f"[LOAD] <- {STATE_PATH}")
    return apply_game_state_dict(data)


def load_baseline_state() -> bool:
    if not os.path.exists(BASELINE_PATH):
        print(f"[BASELINE] Missing; cannot load {BASELINE_PATH}")
        return False
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as ex:
        print("[BASELINE][ERROR] reading baseline:", ex)
        return False

    print(f"[BASELINE] <- {BASELINE_PATH}")
    return apply_game_state_dict(data)


# ---------------------------------------------------------------------
# Apply serialized state to the live world
# ---------------------------------------------------------------------

def apply_game_state_dict(data: dict) -> bool:
    """
    Apply a serialized state dict directly (same format as _serialize_current_state()).
    This is used by undo snapshots so we can revert without writing to disk.
    """
    try:
        world = getattr(gameSetup, "drugstore_world", None)
        if world is None:
            print("[APPLY_STATE][ERROR] drugstore_world is None")
            return False

        # Maps
        name_to_area = {getattr(a, "name", f"Area@{id(a)}"): a for a in (getattr(world, "sub_areas", []) or [])}

        characters = _collect_characters(world)
        name_to_char = {getattr(c, "name", f"Character@{id(c)}"): c for c in characters}

        items = _collect_items(world, characters)
        name_to_item = {getattr(it, "name", f"Item@{id(it)}"): it for it in items}

        print(f"[APPLY_STATE] in-memory: areas={len(name_to_area)}, chars={len(name_to_char)}, items={len(name_to_item)}")
        print(f"[APPLY_STATE] saved: areas={len((data.get('areas') or {}))}, chars={len((data.get('characters') or {}))}, items={len((data.get('items') or {}))}")

        # 1) Clear placement
        for area in getattr(world, "sub_areas", []) or []:
            try:
                area.characters = []
            except Exception:
                pass
            try:
                area.key_items = []
            except Exception:
                pass
            try:
                area.items = []
            except Exception:
                pass

        for c in characters:
            try:
                c.inventory = []
            except Exception:
                pass
            try:
                c.party = []
            except Exception:
                pass
            # weapon pointer exists in your codebase; safe to clear
            try:
                c.weapon = None
            except Exception:
                pass

        for it in items:
            try:
                it.holder = None
            except Exception:
                pass
            try:
                it.position = None
            except Exception:
                pass
            try:
                it.is_equipped = False
            except Exception:
                pass

        # 2) Apply character core fields + current_area placement (party/inv later)
        for cname, cdata in (data.get("characters", {}) or {}).items():
            c = name_to_char.get(cname)
            if c is None:
                continue

            c.health = cdata.get("health", getattr(c, "health", 100))
            c.controllable = cdata.get("controllable", getattr(c, "controllable", False))
            c.gender = cdata.get("gender", getattr(c, "gender", 2))
            c.is_alive = cdata.get("is_alive", getattr(c, "is_alive", True))
            c.has_acted = cdata.get("has_acted", getattr(c, "has_acted", False))
            c.state = cdata.get("state", getattr(c, "state", ""))

            # OCEAN (if present)
            for trait in ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"):
                try:
                    setattr(c, trait, cdata.get(trait, getattr(c, trait, 5)))
                except Exception:
                    pass

            area_name = cdata.get("current_area")
            if area_name and area_name in name_to_area:
                c.current_area = name_to_area[area_name]
                try:
                    if c not in c.current_area.characters:
                        c.current_area.characters.append(c)
                except Exception:
                    pass

        # 3) Friendships (after all characters exist)
        for cname, cdata in (data.get("characters", {}) or {}).items():
            c = name_to_char.get(cname)
            if c is None:
                continue
            try:
                c.friendships = {}
            except Exception:
                pass
            for oname, lvl in (cdata.get("friendships", {}) or {}).items():
                other = name_to_char.get(oname)
                if other is None:
                    continue
                try:
                    c.friendships[other] = int(lvl)
                except Exception:
                    c.friendships[other] = 5

        # 4) Restore item attributes (placement later)
        for iname, idata in (data.get("items", {}) or {}).items():
            it = name_to_item.get(iname)
            if it is None:
                continue

            # Restore description & attributes
            try:
                it.description = idata.get("description", getattr(it, "description", ""))
            except Exception:
                pass
            for k in ("robustness", "damage", "is_medicine", "is_healing_item", "is_weapon", "is_equipped"):
                try:
                    setattr(it, k, idata.get(k, getattr(it, k, False if k.startswith("is_") else 0)))
                except Exception:
                    pass

        # 5) Rebuild parties (now that all chars exist)
        for cname, cdata in (data.get("characters", {}) or {}).items():
            c = name_to_char.get(cname)
            if c is None:
                continue
            party_names = cdata.get("party", []) or []
            new_party = []
            for pn in party_names:
                pc = name_to_char.get(pn)
                if pc is not None:
                    new_party.append(pc)
            try:
                c.party = new_party
            except Exception:
                pass

        # 6) FINAL: rebuild inventories exactly from saved character lists (deterministic)
        for cname, cdata in (data.get("characters", {}) or {}).items():
            c = name_to_char.get(cname)
            if c is None:
                continue

            desired_inv = cdata.get("inventory", []) or []
            try:
                c.inventory = []
            except Exception:
                pass

            for iname in desired_inv:
                it = name_to_item.get(iname)
                if it is None:
                    continue

                # Remove from any previous holder/area lists
                prev_holder = getattr(it, "holder", None)
                if prev_holder is not None and prev_holder is not c:
                    try:
                        prev_holder.inventory.remove(it)
                    except Exception:
                        pass

                prev_area = getattr(it, "position", None)
                if prev_area is not None:
                    try:
                        if it in getattr(prev_area, "key_items", []):
                            prev_area.key_items.remove(it)
                    except Exception:
                        pass
                    try:
                        if it in getattr(prev_area, "items", []):
                            prev_area.items.remove(it)
                    except Exception:
                        pass

                # Assign to this character
                try:
                    it.holder = c
                except Exception:
                    pass
                try:
                    it.position = None
                except Exception:
                    pass
                try:
                    if it not in c.inventory:
                        c.inventory.append(it)
                except Exception:
                    pass

            # weapon pointer (keep previous behavior)
            wname = cdata.get("weapon")
            if isinstance(wname, str) and wname:
                w = name_to_item.get(wname)
                if w is not None:
                    try:
                        c.weapon = w
                    except Exception:
                        pass
                    # ensure weapon isn't "lost"
                    try:
                        if w not in c.inventory:
                            c.inventory.append(w)
                        w.holder = c
                        w.position = None
                    except Exception:
                        pass

        # 7) Restore area floor items/occupants from file (consistency pass)
        for aname, adata in (data.get("areas", {}) or {}).items():
            area = name_to_area.get(aname)
            if area is None:
                continue

            # characters
            new_chars = []
            for n in (adata.get("characters", []) or []):
                ch = name_to_char.get(n)
                if ch is not None:
                    new_chars.append(ch)
            try:
                area.characters = new_chars
            except Exception:
                pass

            # key_items + items
            new_key_items = []
            for n in (adata.get("key_items", []) or []):
                it = name_to_item.get(n)
                if it is None:
                    continue
                # only place if not held
                if getattr(it, "holder", None) is None:
                    try:
                        it.position = area
                    except Exception:
                        pass
                    new_key_items.append(it)
            try:
                area.key_items = new_key_items
            except Exception:
                pass

            new_items = []
            for n in (adata.get("items", []) or []):
                it = name_to_item.get(n)
                if it is None:
                    continue
                if getattr(it, "holder", None) is None:
                    try:
                        it.position = area
                    except Exception:
                        pass
                    new_items.append(it)
            try:
                area.items = new_items
            except Exception:
                pass

        # 8) Restore saved link descriptions and resolve blockade events if appropriate
        links = data.get("links", {}) or {}
        try:
            import gameEvents as _ge  # local import to avoid circulars
        except Exception:
            _ge = None

        _seen = set()
        for area in getattr(world, "sub_areas", []) or []:
            for link in getattr(area, "linking_points", []) or []:
                try:
                    a1 = getattr(link, "area_a", None) or getattr(link, "area1", None) or getattr(link, "from_area", None)
                    a2 = getattr(link, "area_b", None) or getattr(link, "area2", None) or getattr(link, "to_area", None)
                    n1 = getattr(a1, "name", None)
                    n2 = getattr(a2, "name", None)
                    if not (n1 and n2):
                        continue
                    key = "::".join(sorted([n1, n2]))
                    if key in _seen:
                        continue
                    _seen.add(key)

                    saved_desc = links.get(key)
                    if saved_desc is not None:
                        link.description = saved_desc

                        # If there is a blockade event tied to this link and the description
                        # equals the resolved text, mark it resolved so it doesn't respawn.
                        if _ge is not None:
                            for ev in list(getattr(_ge.event_manager, "active_events", []) or []):
                                try:
                                    if getattr(ev, "linking_point", None) is link and getattr(ev, "resolved_description", None) == saved_desc:
                                        ev.resolve()
                                except Exception:
                                    pass
                except Exception:
                    continue

        # 9) World bits
        w = data.get("world", {}) or {}
        try:
            world.chaos_state = w.get("chaos_state", getattr(world, "chaos_state", 0))
        except Exception:
            pass
        try:
            world.current_dilemma = w.get("current_dilemma", getattr(world, "current_dilemma", ""))
        except Exception:
            pass
        try:
            world.current_goal = w.get("current_goal", getattr(world, "current_goal", ""))
        except Exception:
            pass

        # 10) Player area override
        p = getattr(gameSetup, "player", None)
        p_area = w.get("player_area")
        if p is not None and p_area and p_area in name_to_area:
            try:
                p.current_area = name_to_area[p_area]
            except Exception:
                pass
            try:
                if p not in p.current_area.characters:
                    p.current_area.characters.append(p)
            except Exception:
                pass

        print("[APPLY_STATE] Applied successfully.")
        return True

    except Exception as ex:
        print("[APPLY_STATE][ERROR]", ex)
        return False


# ---------------------------------------------------------------------
# Story persistence
# ---------------------------------------------------------------------

def save_story_text(text: str) -> None:
    try:
        with open(STORY_PATH, "w", encoding="utf-8") as f:
            f.write(text or "")
        # keep it quiet; or print if you prefer:
        # print(f"[SAVE] Story -> {STORY_PATH} (chars={len(text or '')})")
    except Exception as ex:
        print("[SAVE][ERROR] Story save failed:", ex)


def load_story_text() -> str:
    if not os.path.exists(STORY_PATH):
        return ""
    try:
        with open(STORY_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def clean_story_text(text: str) -> str:
    lines = (text or "").splitlines()
    cleaned = [ln for ln in lines if not ln.strip().lower().startswith("player input:")]
    return "\n".join(cleaned)
