# turnHandler.py (drop-in)
from __future__ import annotations
from typing import Dict, List, Optional, Tuple, Iterable
import re

import gameRenderer
import gameSetup
from actions import validate_action, activate_action


# ============================= Utilities ==============================

_REPRISH = re.compile(r"^<[^>]*object at 0x[0-9A-Fa-f]+>$")

def _is_reprish(s) -> bool:
    return isinstance(s, str) and _REPRISH.match(s) is not None

def _safe_str(x):
    """Return a clean string or None. Never return objects or repr junk."""
    if x is None:
        return None
    if isinstance(x, (int, float, bool)):
        x = str(x)
    if isinstance(x, str):
        s = x.strip()
        if not s or _is_reprish(s):
            return None
        return s
    # objects -> prefer name, then uid
    name = getattr(x, "name", None)
    uid  = getattr(x, "uid", None)
    return _safe_str(name) or _safe_str(uid)

def _uid_name_from_obj(obj):
    """Duck-type: extract uid/name from ANY object that has those attrs."""
    uid = getattr(obj, "uid", None)
    name = getattr(obj, "name", None)
    uid = str(uid).strip() if uid is not None else None
    name = str(name).strip() if name is not None else None
    return uid or None, name or None

def _normalize_step(raw: dict) -> dict:
    """
    Canonicalize a raw step:
    - keep live objects ONLY in object slots
    - *_id/*_name are strings only (never objects or repr junk)
    - DUCK-TYPE entities (any object with .uid/.name)
    Accepts both 'requested action' and 'requested_action', and topic/topic_of_conversation.
    """
    import re
    REPRISH = re.compile(r"^<[^>]*object at 0x[0-9A-Fa-f]+>$")

    def safe_str(x):
        if x is None:
            return None
        if isinstance(x, (int, float, bool)):
            x = str(x)
        if isinstance(x, str):
            s = x.strip()
            if not s or REPRISH.match(s):
                return None
            return s
        # objects → prefer name, then uid
        name = getattr(x, "name", None)
        uid  = getattr(x, "uid", None)
        name = str(name).strip() if name is not None else None
        uid  = str(uid).strip() if uid is not None else None
        return name or uid or None

    out = dict(raw or {})

    # object slots: accept ANY object (duck-typed elsewhere)
    tgt_obj  = out.get("target")
    sec_obj  = out.get("second target") or out.get("indirect_target")
    item_obj = out.get("item")
    loc_obj  = out.get("location")

    # textual tokens (from parser/grid)
    tgt_tok  = out.get("target_id") or out.get("target_name")
    sec_tok  = out.get("indirect_target_id") or out.get("indirect_target_name")
    item_tok = out.get("item_id") or out.get("item_name")
    loc_tok  = out.get("location_id") or out.get("location_name")

    out["action"] = safe_str(out.get("action")) or "0"
    out["requested action"] = (
        safe_str(out.get("requested action")) or safe_str(out.get("requested_action")) or "0"
    )
    out["topic"] = safe_str(out.get("topic")) or safe_str(out.get("topic_of_conversation")) or "0"

    # keep only live objects in object slots
    out["target"] = tgt_obj if tgt_obj is not None else None
    out["second target"] = sec_obj if sec_obj is not None else None
    out["item"] = item_obj if item_obj is not None else None
    out["location"] = loc_obj if loc_obj is not None else None

    def fill_pair(obj, tok, id_key, name_key):
        uid, name = (None, None)
        if obj is not None:
            uid, name = _uid_name_from_obj(obj)
        tok = safe_str(tok)
        if uid is None and name is None and tok:
            uid = tok
            name = tok
        out[id_key] = uid
        out[name_key] = name

    fill_pair(out["target"], tgt_tok, "target_id", "target_name")
    fill_pair(out["second target"], sec_tok, "indirect_target_id", "indirect_target_name")
    fill_pair(out["item"], item_tok, "item_id", "item_name")
    fill_pair(out["location"], loc_tok, "location_id", "location_name")

    # Debug
    try:
        def _ty(v): return type(v).__name__
        print("[DEBUG][TurnHandler._normalize_step] result:")
        print(f"  action={out['action']!r}  requested={out['requested action']!r}  topic={out['topic']!r}")
        print(f"  target=({_ty(out['target'])}) {getattr(out['target'],'name',None) or getattr(out['target'],'uid',None)}")
        print(f"  second target=({_ty(out['second target'])}) {getattr(out['second target'],'name',None) or getattr(out['second target'],'uid',None)}")
        print(f"  item=({_ty(out['item'])}) {getattr(out['item'],'name',None) or getattr(out['item'],'uid',None)}")
        print(f"  location=({_ty(out['location'])}) {getattr(out['location'],'name',None) or getattr(out['location'],'uid',None)}")
        print(f"  ids: tgt={out['target_id']!r}, sec={out['indirect_target_id']!r}, item={out['item_id']!r}, loc={out['location_id']!r}")
        print(f"  names: tgt={out['target_name']!r}, sec={out['indirect_target_name']!r}, item={out['item_name']!r}, loc={out['location_name']!r}")
    except Exception:
        pass

    return out

def _bind_step_entities(step: dict, actor) -> dict:
    """
    Turn string tokens (target_id/target_name/item_name/location_name) into LIVE objects
    so validate_action(...) has what it needs.
    Searches current area + party first, then globals (duck-typed).
    """
    import gameSetup

    def s(x):
        if x is None:
            return None
        if isinstance(x, (int, float, bool)):
            x = str(x)
        if isinstance(x, str):
            x = x.strip()
            return x if x else None
        n = getattr(x, "name", None)
        u = getattr(x, "uid", None)
        return (n or u) if (n or u) else None

    def looks_like_uid(txt: str) -> bool:
        txt = txt.lower()
        return txt.startswith(("char_", "npc_", "item_", "area_", "loc_", "subarea_"))

    # ---------- enumerate world things ----------
    def iter_characters() -> Iterable[gameRenderer.Character]:
        seen = set()
        here = getattr(gameSetup.player, "current_area", None)
        if here:
            for c in getattr(here, "characters", []) or []:
                if id(c) not in seen: seen.add(id(c)); yield c
        for c in getattr(gameSetup.player, "party", []) or []:
            if id(c) not in seen: seen.add(id(c)); yield c
        for area in getattr(gameSetup, "allAreas", []) or []:
            for c in getattr(area, "characters", []) or []:
                if id(c) not in seen: seen.add(id(c)); yield c
        for coll_name in ("allCharacters", "characters", "CHARACTERS", "characters_by_uid"):
            coll = getattr(gameSetup, coll_name, None)
            if coll:
                it = coll.values() if isinstance(coll, dict) else coll
                for c in it:
                    if id(c) not in seen: seen.add(id(c)); yield c

    def iter_items():
        for it in getattr(gameSetup.player, "inventory", []) or []:
            yield it
        here = getattr(gameSetup.player, "current_area", None)
        if here:
            for it in getattr(here, "items", []) or []: yield it
            for it in getattr(here, "key_items", []) or []: yield it
            for who in getattr(here, "characters", []) or []:
                for it in getattr(who, "inventory", []) or []: yield it

    def iter_locations():
        for a in getattr(gameSetup, "allAreas", []) or []:
            yield a

    # ---------- resolvers ----------
    def resolve_character(token):
        if token is None: return None
        if hasattr(token, "name") or hasattr(token, "uid"): return token
        txt = s(token)
        if not txt: return None
        low = txt.lower()
        if looks_like_uid(txt):
            for ch in iter_characters():
                if s(getattr(ch, "uid", None)) and s(getattr(ch, "uid")).lower() == low:
                    return ch
        for ch in iter_characters():
            nm = s(getattr(ch, "name", None))
            if nm and nm.lower() == low:
                return ch
        if low == "clem":
            for ch in iter_characters():
                nm = s(getattr(ch, "name", None))
                if nm and nm.lower() == "clementine":
                    return ch
        return None

    def resolve_item(token):
        if token is None: return None
        if hasattr(token, "name") or hasattr(token, "uid"): return token
        txt = s(token)
        if not txt: return None
        low = txt.lower()
        if looks_like_uid(txt):
            for it in iter_items():
                if s(getattr(it, "uid", None)) and s(getattr(it, "uid")).lower() == low:
                    return it
        for it in iter_items():
            nm = s(getattr(it, "name", None))
            if nm and nm.lower() == low:
                return it
        return None

    def resolve_location(token):
        if token is None: return None
        if hasattr(token, "name") or hasattr(token, "uid"): return token
        txt = s(token)
        if not txt: return None
        low = txt.lower()
        if looks_like_uid(txt):
            for a in iter_locations():
                if s(getattr(a, "uid", None)) and s(getattr(a, "uid")).lower() == low:
                    return a
        for a in iter_locations():
            nm = s(getattr(a, "name", None))
            if nm and nm.lower() == low:
                return a
        return None

    # ---------- bind into the step ----------
    out = dict(step or {})
    tgt_tok = out.get("target") or out.get("target_name") or out.get("target_id")
    sec_tok = out.get("second target") or out.get("indirect_target_name") or out.get("indirect_target_id")
    itm_tok = out.get("item") or out.get("item_name") or out.get("item_id")
    loc_tok = out.get("location") or out.get("location_name") or out.get("location_id")

    if out.get("target") is None:         out["target"] = resolve_character(tgt_tok)
    if out.get("second target") is None:  out["second target"] = resolve_character(sec_tok)
    if out.get("item") is None:           out["item"] = resolve_item(itm_tok)
    if out.get("location") is None:       out["location"] = resolve_location(loc_tok)

    # Backfill *_id/*_name from bound objects
    def backfill(obj_key, id_key, name_key):
        obj = out.get(obj_key)
        if obj is not None:
            uid = _safe_str(getattr(obj, "uid", None))
            name = _safe_str(getattr(obj, "name", None))
            if out.get(id_key) is None and uid: out[id_key] = uid
            if out.get(name_key) is None and name: out[name_key] = name

    backfill("target", "target_id", "target_name")
    backfill("second target", "indirect_target_id", "indirect_target_name")
    backfill("item", "item_id", "item_name")
    backfill("location", "location_id", "location_name")

    # Debug snapshot
    try:
        def ty(v): return type(v).__name__
        def nm(v): return getattr(v, "name", None) or getattr(v, "uid", None) or str(v)
        print("[DEBUG][TurnHandler._bind_step_entities] after bind:")
        print(f"  target=({ty(out.get('target'))}) {nm(out.get('target'))}")
        print(f"  second target=({ty(out.get('second target'))}) {nm(out.get('second target'))}")
        print(f"  item=({ty(out.get('item'))}) {nm(out.get('item'))}")
        print(f"  location=({ty(out.get('location'))}) {nm(out.get('location'))}")
    except Exception:
        pass

    return out


# ========================= Internal data structures =========================

class ActionPlan:
    """One planned step for an owner."""
    def __init__(self, owner: gameRenderer.Character, step: dict, origin: str):
        self.owner = owner
        self.steps = [step] if step else []
        self.cursor = 0
        self.origin = origin  # "player" | "goodAI" | "evilAI" | "group-join" | "group-move"

    @property
    def has_next(self) -> bool:
        return 0 <= self.cursor < len(self.steps)

    def peek(self) -> Optional[dict]:
        if not self.has_next:
            return None
        return self.steps[self.cursor]


# =============================== Controller (LLM) hooks ===============================

def build_controller_prompt(controller_name: str, roster: List[gameRenderer.Character]) -> str:
    """
    Build a compact prompt string for an AI controller that will pick exactly one action
    for each character in `roster`. This only returns text; do NOT call any LLM here.
    """
    lines = [
        f"[AI-CONTROLLER: {controller_name}]",
        "You control the following actors this round:",
    ]
    for ch in roster:
        here = getattr(ch, "current_area", None)
        others = [
            c.name for c in getattr(here, "characters", []) or []
            if c is not ch and getattr(c, "is_alive", True)
        ] if here else []
        lines.append(f" - {ch.name} (speed={getattr(ch,'speed',5)}; area={getattr(here,'name','?')}; others={', '.join(others) if others else 'None'})")
    lines += [
        "",
        "Return exactly one line per actor using CSV-style key:value pairs:",
        "actor:<name>, action:<id>, requested_action:<id>, target:<name|uid>, indirect_target:<name|uid>, item:<name|uid>, location:<name|uid>, topic_of_conversation:<text>",
        "Only include fields that matter. Use '0' when not applicable.",
    ]
    return "\n".join(lines)

def parse_action_grid_line(line: str) -> Tuple[Optional[str], dict]:
    """
    Parse a single CSV-style 'key:value' line.
    Returns (actor_name|None, step_dict).
    """
    step = {
        "action": "0",
        "requested action": "0",
        "target_name": None,
        "indirect_target_name": None,
        "item_name": None,
        "location_name": None,
        "topic": "0",
    }
    actor_name: Optional[str] = None
    if not isinstance(line, str):
        return actor_name, step
    for chunk in line.split(","):
        if ":" not in chunk:
            continue
        k, v = chunk.split(":", 1)
        k = k.strip().lower()
        v = v.strip()
        if not v:
            continue
        if k == "actor":
            actor_name = v
        elif k in ("action", "topic", "topic_of_conversation"):
            if k == "topic_of_conversation":
                k = "topic"
            step[k] = v
        elif k in ("requested action", "requested_action"):
            step["requested action"] = v
        elif k == "target":
            step["target_name"] = v
        elif k in ("indirect_target", "second target", "indirect_target_name"):
            step["indirect_target_name"] = v
        elif k == "item":
            step["item_name"] = v
        elif k == "location":
            step["location_name"] = v
    return actor_name, _normalize_step(step)

def parse_action_grid(blob: str) -> List[Tuple[str, dict]]:
    """
    Parse a multi-line blob into [(actor_name, step_dict), ...].
    Lines without an 'actor:' key are ignored.
    """
    out: List[Tuple[str, dict]] = []
    if not isinstance(blob, str):
        return out
    for line in blob.splitlines():
        actor, step = parse_action_grid_line(line)
        if actor:
            out.append((actor, step))
    return out


# =============================== Turn Handler ===============================

class TurnHandler:
    """
    Round driver for: human player, good AI (friendly controller), evil AI (hostile controller).
    Queue exactly one action per actor for the round, then execute by speed.

    Interruption rules:
      • When a faster action involves B (as target/second target), B is 'engaged with' that faster actor.
      • If B later acts:
          - Allowed only if B's action involves the SAME counterpart (i.e., B targets that faster actor).
          - Otherwise (solo action or interacting with someone else) => B's action is canceled this round.
      • If a slower third party C tries to interact with B who is engaged with A, and C != A, C is canceled.
      • Ally piggyback: if C is in the same party as A, C may still act on B.
    """

    def __init__(self):
        self._event_manager = None
        self._plans_by_actor: Dict[gameRenderer.Character, ActionPlan] = {}
        self._round = 1

    # ------------------------------ Wiring ------------------------------

    def set_event_manager(self, em):
        self._event_manager = em

    # ------------------------------ Queueing ----------------------------

    def queue_step(self, owner: gameRenderer.Character, step: dict, origin: str):
        """Generic queue for any actor (player/friendly/hostile)."""
        step = _normalize_step(step)
        self._plans_by_actor[owner] = ActionPlan(owner, step, origin=origin)
        print(f"[TURN] Queued {origin} step for {owner.name}: {step.get('action')} → {step.get('target_name') or step.get('target_id') or step.get('location_name')}")

    def queue_player_step(self, step: dict):
        self.queue_step(gameSetup.player, step, origin="player")

    def queue_controller_actions(self, mapping: Dict[gameRenderer.Character, dict], origin: str):
        """
        Queue a batch for a controller (Good/Evil) or system cascades.
        mapping: {Character -> step_dict}
        """
        for actor, step in (mapping or {}).items():
            if actor is None or step is None:
                continue
            self.queue_step(actor, step, origin=origin)

    def queue_from_grid_rows(self, rows: List[Tuple[gameRenderer.Character, str]], origin: str):
        """
        Convenience: rows = [(actor_obj, 'actor:<name>, action:..., ...'), ...]
        The 'actor:' value in the string is ignored; we trust actor_obj.
        """
        for actor, line in rows or []:
            _, step = parse_action_grid_line(line)
            self.queue_step(actor, step, origin=origin)

    # ------------------------------ Helpers -----------------------------

    def _sorted_actors(self) -> List[gameRenderer.Character]:
        actors = [a for a, plan in self._plans_by_actor.items() if plan.has_next]
        def key(a: gameRenderer.Character):
            return (-int(getattr(a, "speed", 5)), getattr(a, "name", getattr(a, "uid", "")))
        return sorted(actors, key=key)

    @staticmethod
    def _partners_from_step(step: dict) -> List[gameRenderer.Character]:
        out: List[gameRenderer.Character] = []
        for k in ("target", "second target"):
            v = step.get(k)
            if v is not None and hasattr(v, "name") and isinstance(v, gameRenderer.Character):
                out.append(v)
        return out

    @staticmethod
    def _party_set(ch: gameRenderer.Character) -> set:
        return set(getattr(ch, "party", []) or [])

    @staticmethod
    def _same_party(a: gameRenderer.Character, b: gameRenderer.Character) -> bool:
        pa = TurnHandler._party_set(a)
        pb = TurnHandler._party_set(b)
        return (b in pa) or (a in pb)

    # ------------------------------ Execution ---------------------------

    def _format_action_output(self, actor, step, origin, raw_text: str) -> str:
        """
        Normalize action output before it's added to the round log.
        For 'group-move' followers, keep the message concise and avoid repeating path flavor.
        """
        if not raw_text:
            return raw_text or ""

        try:
            text = str(raw_text)
            # Only tweak follower move messages
            if origin == "group-move" and (step.get("action") == "move"):
                # 1) Strip the optional path flavor the move handler may add:
                #    " You walk through <...>."
                import re
                text = re.sub(r"\s+You walk through[^.]*\.", "", text)

                # 2) Replace full "moves from A to B" with concise "follows to B"
                dest_obj = step.get("location")
                dest_name = getattr(dest_obj, "name", None) or step.get("location_name") or "the destination"
                actor_name = getattr(actor, "name", None) or getattr(actor, "uid", "Someone")
                text = f"{actor_name} follows to {dest_name}."

            return text
        except Exception:
            # If anything goes odd, fall back to the raw text
            return raw_text

    def run_one_round(self) -> str:
        """
        Execute exactly one round with speed ordering, interruption rules,
        and re-scanning so newly queued actions (group-move / group-join) can
        run in the same round. Any consumed step (executed, invalid, or
        interrupted) marks actor.has_acted = True to prevent re-selection.
        """
        outputs: List[str] = []
        print(f"[TURN] === Round {self._round} ===")

        # Engagement map: who is engaged with whom due to earlier faster actions.
        engaged_with: Dict[gameRenderer.Character, set[gameRenderer.Character]] = {}

        def mark_engaged(a: gameRenderer.Character, b: gameRenderer.Character):
            engaged_with.setdefault(a, set()).add(b)
            engaged_with.setdefault(b, set()).add(a)

        processed: set[gameRenderer.Character] = set()

        def _consume_step(plan: ActionPlan, actor: gameRenderer.Character):
            """Consume the actor's single step for this round and flag as acted."""
            try:
                plan.cursor = len(plan.steps)
                processed.add(actor)
                actor.has_acted = True   # <-- IMPORTANT: mark as acted
            except Exception:
                pass

        while True:
            progressed = False

            for actor in self._sorted_actors():
                if actor in processed:
                    continue

                plan = self._plans_by_actor.get(actor)
                if not plan or not plan.has_next:
                    processed.add(actor)
                    continue

                # Bind live objects for validation/interrupt checks.
                step = plan.peek()
                step = _bind_step_entities(step, actor)
                plan.steps[plan.cursor] = step

                partners = self._partners_from_step(step)
                actor_current_partners = engaged_with.get(actor, set())

                # A) Already engaged -> must involve at least one same counterpart.
                if actor_current_partners and not any(p in actor_current_partners for p in partners):
                    outputs.append(f"{actor.name}'s action is interrupted (they were engaged by an earlier action).")
                    _consume_step(plan, actor)
                    progressed = True
                    continue

                # B) Partner engaged with someone else -> blocked unless ally piggyback.
                blocked = False
                for p in partners:
                    p_engagers = engaged_with.get(p, set())
                    if p_engagers and actor not in p_engagers:
                        if not any(self._same_party(actor, eng) for eng in p_engagers):
                            blocked = True
                            break
                if blocked:
                    outputs.append(f"{actor.name}'s action is interrupted (their target is busy with another actor).")
                    _consume_step(plan, actor)
                    progressed = True
                    continue

                # ---- Execute (or surface validation error) ----
                try:
                    err = validate_action(step, self._event_manager, actor)
                    if err:
                        outputs.append(err)
                        _consume_step(plan, actor)
                    else:
                        txt = activate_action(step, self._event_manager, actor)
                        if txt:
                            pretty = self._format_action_output(actor, step, plan.origin, txt)
                            outputs.append(pretty)

                        # mark engagements created by this action
                        for p in partners:
                            mark_engaged(actor, p)

                        # post-action event checks
                        try:
                            em_msg = self._event_manager.check_for_event_triggers_after_action(actor)
                            if em_msg:
                                outputs.append(em_msg)
                        except Exception:
                            pass

                        _consume_step(plan, actor)

                except Exception as ex:
                    outputs.append(f"Action failed for {actor.name}: {ex}")
                    _consume_step(plan, actor)

                progressed = True

            if not progressed:
                break

        # Clear any remaining queued steps; next round will start fresh.
        self._plans_by_actor.clear()
        self._round += 1

        combined = "\n".join(o for o in outputs if o)
        if combined.strip():
            print("System response (TurnHandler Round Output):")
            print(combined)
        return combined


# ========================= Module-level façade (API) =========================

_HANDLER = TurnHandler()

def set_event_manager(event_manager):
    _HANDLER.set_event_manager(event_manager)

def queue_player_step(step: dict):
    _HANDLER.queue_player_step(step)

def queue_controller_actions(mapping: Dict[gameRenderer.Character, dict], origin: str):
    _HANDLER.queue_controller_actions(mapping, origin=origin)

def queue_from_grid_rows(rows: List[Tuple[gameRenderer.Character, str]], origin: str):
    _HANDLER.queue_from_grid_rows(rows, origin=origin)

def run_one_round() -> str:
    return _HANDLER.run_one_round()

def run_until_idle(event_manager=None, max_rounds: int = 50) -> str:
    """
    Back-compat runner that executes up to max_rounds rounds or stops early
    if a round produces no output. (Useful if some code still calls this.)
    """
    if event_manager is not None:
        try:
            set_event_manager(event_manager)
        except Exception:
            pass

    outputs: List[str] = []
    for _ in range(max_rounds):
        out = run_one_round()
        if out:
            outputs.append(out)
        else:
            break
    return "\n".join(o for o in outputs if o)
