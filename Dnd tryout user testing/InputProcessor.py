# InputProcessor.py

import re
import openai
from config import (
    parsing_message, story_message, conversation_message,
    useAI, model_parsing, model_storytelling, model_talking,  
    key, base_url, model_precheck, model_undo, precheck_message,
    model_validation, validation_message, showPrints
)

import gameSetup
from gameEvents import event_manager
from actions import activate_action, validate_action_sequence

conversation_mode = False
conversation_log: list[str] = []

# --- Allowed actions (keep in sync with actions.py & your system prompt) ---
ALLOWED_ACTIONS = {
    "move", "talk", "inform", "search", "pick_up", "use_item", "give_item",
    "equip_item", "unequip_item", "harm", "ask_action", "steal",
    "do_nothing", "exit_world", "stop_event", "join_party", "quit_party",
    "drop_item",
}

# Canonical keys we expect from the parser
_CANON_KEYS = [
    "action", "requested_action", "target", "indirect_target",
    "item", "location", "topic_of_conversation"
]

# Normalize variants to canonical keys
_KEY_ALIASES = {
    "requested action": "requested_action",
    "requested_action": "requested_action",

    "indirect_target": "indirect_target",
    "indirect_target": "indirect_target",
    "second target": "indirect_target",

    "topic": "topic_of_conversation",
    "topic_of_conversation": "topic_of_conversation",

    "action": "action",
    "target": "target",
    "item": "item",
    "location": "location",
}

# Keep this near AIprecheck (module-level constant)
_EXPECTED_PRECHECK_LABELS = {
    "clear",
    "undo",
    "redo",
    "question",
    "long",
    "insufficient",
    "unrelated",
    "impossible"
}

# Save previous text to give AI prompt context
previous_text = ""
# When not None, we are waiting for the player to confirm this action.
pending_confirmation_action = None      # type: dict | None
pending_confirmation_original_input = ""  # type: str

# --- Correction phase state ---
pending_correction_actions: list[dict] | None = None
pending_correction_failed_index: int = -1
pending_correction_original_input: str = ""
pending_correction_error: str = ""

# -----------------------
# Resolution helpers (IDs-first, tolerant to names)
# -----------------------

def _iter_all_areas():
    w = getattr(gameSetup, "drugstore_world", None)
    if not w:
        return []
    if hasattr(w, "sub_areas"):
        return list(w.sub_areas)
    return []

def _find_area_by_id(uid: str):
    for a in _iter_all_areas():
        if getattr(a, "uid", None) and a.uid.lower() == uid.lower():
            return a
    return None

def _find_area_by_name(name: str):
    # keep legacy behavior
    return gameSetup.drugstore_world.get_sub_area_by_name(name)

def _iter_all_characters():
    for a in _iter_all_areas():
        for c in getattr(a, "characters", []):
            yield c

def _find_character_by_id(uid: str):
    for c in _iter_all_characters():
        if getattr(c, "uid", None) and c.uid.lower() == uid.lower():
            return c
    return None

def _find_character_by_name(name: str):
    for c in _iter_all_characters():
        if getattr(c, "name", "").lower() == name.lower():
            return c
    return None

def _find_item_by_id(uid: str):
    # search floor, then inventories
    for a in _iter_all_areas():
        for it in getattr(a, "key_items", []):
            if getattr(it, "uid", None) and it.uid.lower() == uid.lower():
                return it
        for c in getattr(a, "characters", []):
            for it in getattr(c, "inventory", []):
                if getattr(it, "uid", None) and it.uid.lower() == uid.lower():
                    return it
    return None

def _find_item_by_name(name: str):
    for a in _iter_all_areas():
        for it in getattr(a, "key_items", []):
            if getattr(it, "name", "").lower() == name.lower():
                return it
        for c in getattr(a, "characters", []):
            for it in getattr(c, "inventory", []):
                if getattr(it, "name", "").lower() == name.lower():
                    return it
    return None

# -----------------------
# Processing & parsing
# -----------------------

def process_player_input(player_input):
    """
    Multi-intent aware processor.

    - If useAI is True, call AIparsing(player_input), which now returns the raw
      model output string (possibly with multiple numbered actions).
    - If useAI is False, treat player_input itself as that structured string.
    - Split into 1..N "action:...,requested_action:..." blocks.
    - For each block, extract canonical fields via _robust_extract_fields().
    - Resolve ids/names to live objects (characters/items/areas).
    - Return a LIST of parsed action dicts.
    """
    text = (player_input or "").strip()
    if not text:
        return []

    # ---- Stage 1: get raw structured text from the model or user ----
    if useAI:
        raw = AIparsing(text)  # now returns a string
    else:
        raw = text

    if not isinstance(raw, str):
        raw_str = str(raw or "")
    else:
        raw_str = raw

    raw_str = raw_str.strip()
    if not raw_str:
        return []

    # ---- Stage 2: split into individual action blocks ----
    # Preferred: model wraps each action in quotes as in your log:
    # 1. "action:...,requested_action:...,..."
    blocks = re.findall(r'"([^"]*action:[^"]*)"', raw_str)

    if not blocks:
        # Fallback: split on 1. 2. 3. style prefixes and keep pieces with 'action'
        pieces = re.split(r'\b\d+\s*[\.\)]\s*', raw_str)
        blocks = [p.strip(' "\'') for p in pieces if "action" in p]

    if not blocks:
        # Absolute fallback: treat the whole string as a single action block
        blocks = [raw_str]

    actions: list[dict] = []

    for block in blocks:
        # ---- Stage 3: extract canonical fields from this one block ----
        fields = _robust_extract_fields(block)

        best_action       = fields.get("action", None)
        final_request     = fields.get("requested_action", None)
        final_char        = fields.get("target", None)
        final_second_char = fields.get("indirect_target", None)
        final_item        = fields.get("item", None)
        final_area        = fields.get("location", None)

        def nz(s):
            # Convert model / parser sentinels into proper Nones
            if s is None:
                return None
            s = str(s).strip()
            if s.lower() in {"", "0", "none", "null", "nil", "n/a", "na", "None", "nothing"}:
                return None
            return s

        best_action       = nz(best_action)
        final_request     = nz(final_request)
        final_char        = nz(final_char)
        final_second_char = nz(final_second_char)
        final_item        = nz(final_item)
        final_area        = nz(final_area)

        parsed_input = {
            "action": best_action or None,
            "requested action": final_request or None,
            "requested_action": final_request or None,

            # raw ids/names – resolver will try IDs first, then names
            "target_id": final_char or None,
            "target_name": final_char or None,
            "indirect_target_id": final_second_char or None,
            "indirect_target_name": final_second_char or None,
            "item_id": final_item or None,
            "item_name": final_item or None,
            "location_id": final_area or None,
            "location_name": final_area or None,

            "target": None,
            "second target": None,
            "item": None,
            "location": None,
        }

        # ---- Stage 4: resolution logic (copied from your old process_player_input) ----
        target_id = parsed_input.get("target_id")
        indirect_target_id = parsed_input.get("indirect_target_id")
        item_id = parsed_input.get("item_id")
        location_id = parsed_input.get("location_id")

        target_name = parsed_input.get("target_name")
        indirect_target_name = parsed_input.get("indirect_target_name")
        item_name = parsed_input.get("item_name")
        location_name = parsed_input.get("location_name")

        target = None
        indirect_target = None
        item = None
        location = None

        # Characters: ID then local-name then global-name
        if target_id:
            target = _find_character_by_id(target_id) or target
        if not target and target_name:
            for character in gameSetup.player.current_area.characters + gameSetup.player.party:
                if character.name.lower() == target_name.lower():
                    target = character
                    break
            if not target:
                target = _find_character_by_name(target_name)

        if indirect_target_id:
            indirect_target = _find_character_by_id(indirect_target_id) or indirect_target
        if not indirect_target and indirect_target_name:
            for character in gameSetup.player.current_area.characters + gameSetup.player.party:
                if character.name.lower() == indirect_target_name.lower():
                    indirect_target = character
                    break
            if not indirect_target:
                indirect_target = _find_character_by_name(indirect_target_name)

        # Items: floor -> player inv -> other local inv (ID first, then name)
        if item_id:
            item = _find_item_by_id(item_id)
        if not item and item_name:
            # local floor
            for area_item in gameSetup.player.current_area.key_items:
                if area_item.name.lower() == item_name.lower():
                    item = area_item
                    break
            # player inventory
            if not item:
                for inv_item in gameSetup.player.inventory:
                    if inv_item.name.lower() == item_name.lower():
                        item = inv_item
                        break
            # other locals
            if not item:
                for character_in_area in gameSetup.player.current_area.characters:
                    if character_in_area == gameSetup.player:
                        continue
                    for npc_item in character_in_area.inventory:
                        if npc_item.name.lower() == item_name.lower():
                            item = npc_item
                            break
                    if item:
                        break
            if not item:
                item = _find_item_by_name(item_name)

        # Areas: by ID then name
        if location_id:
            location = _find_area_by_id(location_id)
        if not location and location_name:
            location = _find_area_by_name(location_name)

        parsed_input.update({
            "target": target,
            "second target": indirect_target,
            "item": item,
            "location": location,

            "target_id": target_id,
            "indirect_target_id": indirect_target_id,
            "item_id": item_id,
            "location_id": location_id,
            "target_name": target_name,
            "indirect_target_name": indirect_target_name,
            "item_name": item_name,
            "location_name": location_name,
        })

        actions.append(parsed_input)

    # Safety net: never return an empty list
    if not actions:
        actions.append({
            "action": "do_nothing",
            "requested action": None,
            "requested_action": None,
            "target_id": None, "target_name": None,
            "indirect_target_id": None, "indirect_target_name": None,
            "item_id": None, "item_name": None,
            "location_id": None, "location_name": None,
            "target": None, "second target": None,
            "item": None, "location": None,
            "topic": None, "topic_of_conversation": None,
        })

    return actions


def edit_system_message(system_message: str) -> str:
    """
    Replace names of controllable characters with 'The player'
    before sending the system message to the storytelling model.

    This does NOT change game state; it only rewrites the text
    passed to AIstorytelling.
    """
    if not system_message:
        return system_message

    text = system_message

    try:
        controllable_names = set()

        # Main player
        player = getattr(gameSetup, "player", None)
        if player is not None and getattr(player, "controllable", False):
            name = getattr(player, "name", "").strip()
            if name:
                controllable_names.add(name)

        # Any other controllable characters in the world
        world = getattr(gameSetup, "drugstore_world", None)
        if world is not None:
            areas = []
            if hasattr(world, "all_sub_areas"):
                areas = list(getattr(world, "all_sub_areas") or [])
            elif hasattr(world, "sub_areas"):
                areas = list(getattr(world, "sub_areas") or [])
            for area in areas:
                for c in getattr(area, "characters", []):
                    if getattr(c, "controllable", False):
                        name = getattr(c, "name", "").strip()
                        if name:
                            controllable_names.add(name)

        # Replace each controllable name with 'The player'
        # Use word boundaries so we don't clobber substrings.
        for name in controllable_names:
            pattern = r"\b" + re.escape(name) + r"\b"
            text = re.sub(pattern, "The player", text)

        return text

    except Exception as ex:
        if showPrints:
            print("[EDIT_SYSTEM_MESSAGE][ERROR]", ex)
        # Fail-safe: if anything goes wrong, return the original
        return system_message


def _handle_correction_reply(player_input: str,) -> tuple[str, int]:
    """
    We are in 'correction phase':
      - We already have pending_correction_actions (a full action list).
      - We know which index failed.
      - The user now types a short fix (usually just a name or place).
    This function:
      - Optionally cancels the action.
      - Parses the fix via process_player_input.
      - Merges the new slots into the failing action.
      - Re-runs validation + execution + storytelling.
    """
    global pending_correction_actions, pending_correction_failed_index,pending_correction_original_input, pending_correction_error, previous_text
    
    text = (player_input or "").strip()
    low = text.lower()

    # Allow user to back out of the whole thing.
    if low in {"no", "no.", "no!", "cancel", "cancel.", "stop", "never mind", "nevermind"}:
        msg = "Okay, we'll cancel that action. Try something else."
        pending_correction_actions = None
        pending_correction_failed_index = -1
        pending_correction_original_input = ""
        pending_correction_error = ""
        previous_text = msg
        return msg, 0

    if not text:
        msg = (
            "I understood that action, but I still need the missing detail. "
            "Please type just a character, item, or place name, or say 'cancel'."
        )
        previous_text = msg
        return msg, 0

    # Parse the fix as a mini action, but ONLY use its slots, never its action.
    patch_actions = process_player_input(text)
    if not patch_actions:
        msg = (
            "I couldn’t quite tell what you wanted to change. "
            "Please type just a character, item, or place name, or say 'cancel'."
        )
        previous_text = msg
        return msg, 0

    patch = patch_actions[0]

    actions = pending_correction_actions or []
    if not actions:
        # Safety fallback
        msg = "Something went wrong while fixing that action. Please try a new command."
        pending_correction_actions = None
        pending_correction_failed_index = -1
        pending_correction_original_input = ""
        pending_correction_error = ""
        previous_text = msg
        return msg, 0

    idx = pending_correction_failed_index
    if idx < 0 or idx >= len(actions):
        idx = 0
    original = actions[idx]

    def _merge_slot(obj_key: str, id_key: str, name_key: str):
        obj = patch.get(obj_key)
        obj_id = patch.get(id_key)
        obj_name = patch.get(name_key)

        if not (obj or obj_id or (isinstance(obj_name, str) and obj_name.strip())):
            return

        if obj is not None:
            original[obj_key] = obj
        if obj_id is not None:
            original[id_key] = obj_id
        if isinstance(obj_name, str) and obj_name.strip():
            original[name_key] = obj_name.strip()

    # Merge the typical entity slots
    _merge_slot("target", "target_id", "target_name")
    _merge_slot("second target", "indirect_target_id", "indirect_target_name")
    _merge_slot("item", "item_id", "item_name")
    _merge_slot("location", "location_id", "location_name")

    # Optional topic propagation
    topic_text = patch.get("topic") or patch.get("topic_of_conversation")
    if isinstance(topic_text, str) and topic_text.strip():
        original["topic"] = topic_text.strip()

    # Remember raw input for storytelling (prefer original, fall back to fix)
    story_input = pending_correction_original_input or player_input

    # Clear correction state before re-validating, to avoid loops if something explodes.
    pending_correction_actions = None
    pending_correction_failed_index = -1
    pending_correction_original_input = ""
    pending_correction_error = ""

    # Re-validate the full sequence.
    try:
        error_msg = validate_action_sequence(
            actions,
            event_manager,
            gameSetup.player,
        )
    except Exception as ex:
        if showPrints:
            print("[VALIDATE_SEQUENCE][ERROR after correction]", ex)
        msg = f"(Internal validation error while checking your corrected action: {ex})"
        previous_text = msg
        return msg, 0

    if error_msg:
        # If it STILL fails, just show the raw error (you can later feed it again
        # through conversation if you want a second correction round).
        previous_text = error_msg
        return error_msg, 0

    # Execute all actions with the patched one
    system_parts: list[str] = []
    for act in actions:
        try:
            player_turn = activate_action(
                act,
                event_manager,
                gameSetup.player,
            )
        except Exception as ex:
            player_turn = f"(Internal execution error for action {act.get('action','0')}: {ex})"
        if player_turn:
            system_parts.append(player_turn)

    response = "\n".join(system_parts) + ("\n" if system_parts else "")
    if showPrints:
        print("System respons:")
        print(response)

    # Apply your “Lee Everett -> The player” substitution
    edited_response = edit_system_message(response)

    gameOver = checkEnd()

    first_action = actions[0] if actions else {}
    try:
        story = AIstorytelling(
            story_input,
            first_action.get("action", None),
            edited_response,
        )
    except Exception as ex:
        if showPrints:
            print("[AISTORY][ERROR after correction]", ex)
        story = edited_response or "(The world reacts in silence.)"

    previous_text = edited_response
    return story, gameOver


def checkEnd():
    """
    Good end if the player is in the special 'far_away' area OR Larry is healed (>=30).
    """
    p = gameSetup.player
    cur = getattr(p, "current_area", None)

    # Dead end
    if p.health == 0:
        return 1

    # Good end: far away or Larry healed
    in_far_away = False
    try:
        in_far_away = (cur is gameSetup.far_away)
    except Exception:
        in_far_away = False

    if not in_far_away and cur is not None:
        name = getattr(cur, "name", "").strip().lower()
        flag = bool(getattr(cur, "is_far_away", False))
        in_far_away = flag or (name == "far away" or name == "far_away")

    if in_far_away or getattr(gameSetup.larry, "health", 0) >= 30:
        return 2

    return 0


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1].strip()
    return s


def _robust_extract_fields(raw: str) -> dict:
    """
    Extract key/value pairs from the model output, tolerating:
      - ':' OR '=' delimiters
      - any key order
      - mixed-case and alias keys
      - optional quotes around values
    Returns dict with canonical keys; missing -> "0" (string) at this stage.
    """
    if not isinstance(raw, str):
        raw = str(raw or "")

    pattern = re.compile(
        r'\b('
        r'action|requested_action|requested action|'
        r'target|second_target|Second_target|second target|'
        r'item|location|topic|topic_of_conversation|'
        r'indirect_target|indirect target'
        r')\b\s*[:=]\s*(.*?)\s*(?=,|;|$)',
        re.IGNORECASE,
    )

    found = {}
    for m in pattern.finditer(raw):
        raw_key = (m.group(1) or "").strip()
        raw_val = (m.group(2) or "").strip()
        key = _KEY_ALIASES.get(raw_key, raw_key).lower()
        if key == "topic":
            key = "topic_of_conversation"
        val = _strip_quotes(raw_val)
        # inside _robust_extract_fields, after val = _strip_quotes(raw_val)
        low = val.strip().lower()
        if low in {"none", "null", "nil", "n/a", "na", "nothing", "", "None"}:
            val = "0"

        # keep "0" as a pure *text* sentinel here; we'll convert to None later
        found[key] = val if val != "" else "0"

    # ensure all canonical keys exist
    out = {k: found.get(k, "0") for k in _CANON_KEYS}

    # ---- normalize actions safely ----
    act_raw = out.get("action") or ""
    req_raw = out.get("requested_action") or ""

    act = str(act_raw).strip().lower().replace(" ", "_")
    req = str(req_raw).strip().lower().replace(" ", "_")

    if act == "investigate":
        act = "search"
    if req == "investigate":
        req = "search"

    # Only auto-ask if there *is* a requested action and no sensible top-level action
    if req and req != "0" and act not in ("ask_action", "talk", "move", "harm", "search", "pick_up"):
        act = "ask_action"

    out["action"] = act if act else "0"
    out["requested_action"] = req if req else "0"

    return out


def _needs_confirmation(action_dict: dict, player) -> str | None:
    """
    Return a short description of the 'extreme' action that should be confirmed,
    or None if this action is fine to execute immediately.

    Rules:
    - Direct:
        * harm against friendly characters or self
        * move to the same location you're already in
        * search a friendly NPC (unless dead)
        * steal from a friendly NPC
        * do_nothing (explicit confirmation with custom phrasing)
    - ask_action:
        * asking an NPC to do any of the above

    Notes:
    - Hostiles (e.g., zombies) should NOT trigger harm-confirmation.
      We treat a character as hostile if:
        * they have hostile=True, OR
        * their state suggests aggression (attack/hostile/enemy), OR
        * either direction of friendship_with() is <= 1
    """
    # Late import to avoid circulars
    try:
        import gameRenderer
        Character = gameRenderer.Character
    except Exception:
        Character = None

    def is_character(obj) -> bool:
        if obj is None:
            return False
        if Character is not None and isinstance(obj, Character):
            return True
        return hasattr(obj, "friendship_with") and hasattr(obj, "current_area")

    def safe_friendship(a, b, default: int = 5) -> int:
        try:
            return int(a.friendship_with(b))
        except Exception:
            return default

    def looks_hostile_entity(c) -> bool:
        if c is None:
            return False
        try:
            if bool(getattr(c, "hostile", False)):
                return True
        except Exception:
            pass
        try:
            st = getattr(c, "state", None)
            if isinstance(st, str) and st.strip().lower() in {"attack", "hostile", "enemy"}:
                return True
        except Exception:
            pass
        return False

    def is_hostile_to_player(char) -> bool:
        if not char or not is_character(char) or player is None:
            return False
        if looks_hostile_entity(char):
            return True

        # IMPORTANT: if either direction is <= 1, treat as hostile.
        # This captures "0 = immutable hostility" correctly.
        try:
            if safe_friendship(player, char, default=5) <= 1:
                return True
        except Exception:
            pass
        try:
            if safe_friendship(char, player, default=5) <= 1:
                return True
        except Exception:
            pass
        return False

    def is_friendly_to_player(char) -> bool:
        if not char or not is_character(char) or player is None:
            return False
        if char is player:
            return False

        # Hostiles are never "friendly" for confirmation purposes
        if is_hostile_to_player(char):
            return False

        # Party is always friendly
        try:
            if char in (getattr(player, "party", []) or []):
                return True
        except Exception:
            pass

        # Otherwise, use either direction (but do NOT override hostility)
        try:
            if safe_friendship(player, char, default=5) >= 3:
                return True
        except Exception:
            pass
        try:
            if safe_friendship(char, player, default=5) >= 3:
                return True
        except Exception:
            pass

        return False

    def describe_char(c) -> str:
        if c is None:
            return "someone"
        try:
            return getattr(c, "name", "someone") or "someone"
        except Exception:
            return "someone"

    def describe_area(a) -> str:
        if a is None:
            return "somewhere"
        try:
            return getattr(a, "name", "somewhere") or "somewhere"
        except Exception:
            return "somewhere"

    def describe_item_from_action(d: dict) -> str | None:
        it = d.get("item")
        if it is not None:
            try:
                nm = getattr(it, "name", None)
                if nm:
                    return nm
            except Exception:
                pass
        nm = d.get("item_name")
        if isinstance(nm, str) and nm.strip():
            return nm.strip()
        iid = d.get("item_id")
        if isinstance(iid, str) and iid.strip():
            return iid.strip()
        return None

    act = (action_dict.get("action") or "").strip().lower()
    req = (action_dict.get("requested action") or action_dict.get("requested_action") or "").strip().lower()

    # -------- Special: do_nothing should be confirmed --------
    if act in {"do_nothing", "donothing", "idle"}:
        return (
            "do nothing. This action would not result in tangible change in the game as it is not within the game mechanics. "
            "Would you like to carry it out anyway"
        )

    # -------- Direct player actions --------
    if act == "harm":
        victim = action_dict.get("target")
        if not is_character(victim):
            return None

        # Do NOT confirm harm against hostiles (e.g., zombies)
        if is_hostile_to_player(victim):
            return None

        weapon = describe_item_from_action(action_dict)

        if victim is player:
            return f"harm yourself{f' using {weapon}' if weapon else ''}"

        if getattr(victim, "is_alive", True) and is_friendly_to_player(victim):
            return f"harm {describe_char(victim)}{f' using {weapon}' if weapon else ''}"

    if act == "move":
        dest = action_dict.get("location")
        here = getattr(player, "current_area", None)
        if dest is not None and here is dest:
            return f"move to {describe_area(dest)} (where you already are)"

    if act == "search":
        person = action_dict.get("target")
        if is_character(person) and getattr(person, "is_alive", True):
            if is_friendly_to_player(person) or person is player:
                return f"search {describe_char(person)}"

    if act == "steal":
        victim = action_dict.get("target")
        if is_character(victim) and getattr(victim, "is_alive", True):
            if is_friendly_to_player(victim) or victim is player:
                stolen = describe_item_from_action(action_dict)
                if stolen:
                    return f"steal {stolen} from {describe_char(victim)}"
                return f"steal from {describe_char(victim)}"

    # -------- Asking someone else to do extreme things --------
    if act == "ask_action":
        asked = action_dict.get("target")
        second = action_dict.get("second target") or action_dict.get("indirect_target")
        dest = action_dict.get("location")
        weapon_or_item = describe_item_from_action(action_dict)

        if req == "do_nothing":
            return (
                f"ask {describe_char(asked)} to do nothing. This action would not result in tangible change in the game as it is not within the game mechanics. "
                "Would you like to carry it out anyway"
            )

        # ask X to harm Y / you
        if req == "harm":
            victim = second if is_character(second) else player

            # Don't confirm asking to harm hostiles
            if is_character(victim) and is_hostile_to_player(victim):
                return None

            if victim is player:
                return f"ask {describe_char(asked)} to harm you{f' using {weapon_or_item}' if weapon_or_item else ''}"
            if is_character(victim) and getattr(victim, "is_alive", True) and is_friendly_to_player(victim):
                return f"ask {describe_char(asked)} to harm {describe_char(victim)}{f' using {weapon_or_item}' if weapon_or_item else ''}"

        elif req == "move":
            here_asked = getattr(asked, "current_area", None)
            if dest is not None and here_asked is dest:
                return f"ask {describe_char(asked)} to move to {describe_area(dest)} (where they already are)"

        elif req == "search":
            person = second if is_character(second) else None
            if person and getattr(person, "is_alive", True) and is_friendly_to_player(person):
                return f"ask {describe_char(asked)} to search {describe_char(person)}"

        elif req == "steal":
            victim = second if is_character(second) else player
            if victim is player:
                return f"ask {describe_char(asked)} to steal{f' {weapon_or_item}' if weapon_or_item else ''} from you"
            if is_character(victim) and getattr(victim, "is_alive", True) and is_friendly_to_player(victim):
                if weapon_or_item:
                    return f"ask {describe_char(asked)} to steal {weapon_or_item} from {describe_char(victim)}"
                return f"ask {describe_char(asked)} to steal from {describe_char(victim)}"

    return None


# OpenAI parses player input into actions
def AIprecheck(user_text: str) -> str:
    """
    Lightweight pre-check before intent parsing.

    Returns a *single word* label (case-insensitive), one of:
      - clear
      - undo
      - redo
      - question
      - long
      - insufficient
      - unrelated
      -impossible

    If the model returns anything else, we retry up to 3 times.
    On repeated failure, we fall back to 'clear' so the game still runs.
    """
    def _normalize(raw: str) -> str:
        raw = (raw or "").strip()
        first = re.split(r"\s+", raw)[0] if raw else ""
        lab = first.lower().strip().strip(".,:;!\"'()[]{}")
        return lab

    client = None
    try:
        client = openai.OpenAI(api_key=key, base_url=base_url)
    except Exception as ex:
        if showPrints:
            print("[PRECHECK][ERROR] could not init client:", ex)
        return "clear"

    last_raw = ""
    for strike in range(1, 3):  # 1..3
        try:
            response = client.chat.completions.create(
                model=model_precheck,
                messages=[
                    precheck_message,
                    {"role": "user", "content": user_text},
                ],
                max_tokens=16,
                temperature=0,
            )
            last_raw = (response.choices[0].message.content or "").strip()
            label = _normalize(last_raw)

            if label in _EXPECTED_PRECHECK_LABELS:
                if showPrints:
                    print(f"[PRECHECK] raw='{last_raw}' -> label='{label}'")
                return label
            if showPrints:
                print(f"[PRECHECK][WARN] invalid label raw='{last_raw}' -> '{label}' (strike {strike}/3)")
        except Exception as ex:
            if showPrints:
                print(f"[PRECHECK][ERROR] attempt {strike}/3:", ex)
    if showPrints:
        print(f"[PRECHECK][FAILOPEN] invalid label after 3 strikes; last_raw='{last_raw}'. Returning 'clear'.")
    return "clear"


def AIundo(user_text: str, snapshots: list[dict]) -> int:
    """
    Undo selector (LLM-only; no local heuristics).

    Input: user_text + snapshots in chronological order (oldest -> newest).
    Output: single integer:
      0 = cancel
      1..N = snapshot number to revert to (1-based, INCLUDING the last snapshot)

    NOTE: The last snapshot is included as a valid choice and marked [MOST RECENT].
    """
    try:
        if showPrints:
            print("\n[AIUNDO] called")
            print("[AIUNDO] user_text:", repr(user_text))

        snaps = snapshots if isinstance(snapshots, list) else []
        n_total = len(snaps)
        if showPrints:
            print("[AIUNDO] snapshots count:", n_total)

        if n_total < 2:
            if showPrints:
                print("[AIUNDO] not enough snapshots to undo (need >=2). Returning 0.")
            return 0

        # Choices INCLUDE the last snapshot now
        n_choices = n_total  # valid revert targets: 1..n_choices

        summary_lines: list[str] = []
        for i, snap in enumerate(snaps, start=1):
            meta = {}
            state = {}
            if isinstance(snap, dict) and "state" in snap:
                state = snap.get("state") or {}
                meta = snap.get("meta") or {}
            elif isinstance(snap, dict):
                meta = snap.get("meta") or {}
                state = snap.get("state") or {}
            else:
                meta = {}
                state = {}

            # location
            player_area = "Unknown"
            try:
                if isinstance(meta, dict) and meta.get("player_area"):
                    player_area = meta.get("player_area") or player_area
            except Exception:
                pass
            if (not player_area or player_area == "Unknown") and isinstance(state, dict):
                try:
                    w = state.get("world", {})
                    if isinstance(w, dict) and w.get("player_area"):
                        player_area = w.get("player_area") or player_area
                except Exception:
                    pass

            # input
            inp = ""
            try:
                if isinstance(meta, dict):
                    inp = meta.get("player_input", "") or meta.get("input", "") or ""
            except Exception:
                inp = ""
            if not isinstance(inp, str):
                inp = str(inp)
            inp = inp.strip()
            if len(inp) > 80:
                inp = inp[:80] + "…"
            if not inp:
                inp = "(no input recorded)"

            suffix = " [MOST RECENT]" if i == n_total else ""
            summary_lines.append(f"{i}. location={player_area} | input={inp}{suffix}")

        if showPrints:
            print("[AIUNDO] choices being sent to model (INCLUDING most recent):")
        for line in summary_lines:
            if showPrints:
                print("  " + line)

        sys_msg = {
            "role": "system",
            "content": (
                "You are an undo-selector for an RPG.\n"
                "You will receive the player's undo request and a chronological list of snapshots.\n"
                "The MOST RECENT snapshot is included and marked.\n"
                "Return ONLY a single integer:\n"
                "  0 = cancel\n"
                "  1..M = revert to that snapshot number\n"
                "Important rules:\n"
                "- Never output words, only the integer.\n"
                "- If the player wants to restart / go back to the beginning / turn 1, choose 1.\n"
                "- If the player asks to undo the last mistake without specifying, choose the MOST RECENT snapshot (the largest number).\n"
            )
        }

        user_msg = (
            f"Undo request:\n{user_text}\n\n"
            "Snapshots you may revert to (chronological, oldest to newest; most recent included):\n"
            + "\n".join(summary_lines)
            + "\n\n"
            "Return the snapshot number (0 to cancel)."
        )

        client = openai.OpenAI(api_key=key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model_undo,
            messages=[sys_msg, {"role": "user", "content": user_msg}],
            max_tokens=16,
            temperature=0,
        )

        raw = (resp.choices[0].message.content or "").strip()
        if showPrints:
            print("[AIUNDO] raw model output:", repr(raw))

        tok = re.split(r"\s+", raw)[0] if raw else "0"
        try:
            k = int(tok)
        except Exception:
            if showPrints:
                print("[AIUNDO] could not parse int from:", repr(tok), "-> returning 0")
            return 0

        # Clamp to valid range: 0..N (including last snapshot)
        if k < 0:
            k = 0
        if k > n_choices:
            if showPrints:
                print("[AIUNDO] model returned out-of-range; clamping.")
            k = n_choices

        if showPrints:
            print("[AIUNDO] final choice (0 cancel, 1..N):", k)
        return k

    except Exception as ex:
        if showPrints:
            print("[AIUNDO][ERROR]", ex)
        return 0


def AIvalidate(mode: str, payload: dict | None = None, **kwargs) -> bool:
    """
    Flexible validator.

    You can call either:
      AIvalidate("story", payload={...})
    OR:
      AIvalidate("story", candidate_text="...", player_name="...", ...)

    For convenience:
      - candidate_text is accepted as an alias:
          story        -> candidate_story
          conversation -> candidate_reply
    """
    try:
        # Merge payload + kwargs into a single dict
        data: dict = {}
        if isinstance(payload, dict):
            data.update(payload)
        data.update(kwargs)

        # Aliases (so old call-sites won't break)
        if "candidate_text" in data and "candidate_story" not in data and "candidate_reply" not in data:
            if str(mode).strip().lower() == "story":
                data["candidate_story"] = data["candidate_text"]
            else:
                data["candidate_reply"] = data["candidate_text"]

        def _parse_yesno_01(raw: str) -> int:
            tok = re.split(r"\s+", (raw or "").strip())[0] if raw else "0"
            try:
                return 1 if int(tok) == 1 else 0
            except Exception:
                return 0

        def _call_validator(rules_text: str) -> bool:
            client = openai.OpenAI(api_key=key, base_url=base_url)
            resp = client.chat.completions.create(
                model=model_validation,
                messages=[validation_message, {"role": "user", "content": rules_text}],
                max_tokens=16,  # Azure min >= 16
                temperature=0,
            )
            raw = (resp.choices[0].message.content or "").strip()
            return _parse_yesno_01(raw) == 1

        m = str(mode).strip().lower()

        if m == "story":
            rules = f"""
MODE: STORYTELLING OUTPUT VALIDATION

Main character name that must NOT appear verbatim anywhere in the story:
{data.get("player_name","")}

THIS TURN PLAYER INPUT:
{data.get("player_input","")}

THIS TURN SYSTEM RECOGNIZED ACTION (may be None):
{data.get("recognized_action",None)}

WORLD/SYSTEM RESULT (ground truth of what happened):
{data.get("world_system_result","")}

PREVIOUS REJECTED STORY (if any):
{data.get("previous_rejected","")}

CANDIDATE STORY (to be shown to player):
{data.get("candidate_story","")}

RULES (return 1 ONLY if ALL are satisfied, else 0):
1) Must be written in second-person perspective ("you").
2) The MC's actual name above must NOT appear anywhere.
3) Must reflect the action(s) and consequences implied by WORLD/SYSTEM RESULT; no contradictions.
4) If action is do_nothing / nothing happened, reflect inaction / no meaningful change.

Return ONLY a single digit: 0 or 1.

Examples of good (1) stories:

Alright, so you step outside, with nothing but abandoned buildings and debris as far as the eye can see, when suddenly three zombies appear!

Ok, you look around, finding all kinds of useless items, broken electronics, empty, dirty bottles, but unfortunately nothing that could be of use to you.

Cool! You take a swing at the barricade, cracking it open. The dark hole opening up to you reveals a new area waiting to be explored...

You try to climb to the attic, but fall down with a loud thud on the ground. You decide it might be better not to try that again.

Okay then, you leave your flashlight on the floor, the light dancing through dirty shelves and peeling wallpaper...

Examples of bad (0) stories:

Lee Everett exchanges information with Clementine.

You do nothing, as the gloomy atmosphere continues to wash over everyone.

Clementine agrees to talk, starting a conversation as her eyes light up. 

You go to the pharmacy, but suddenly realize the path there doesn't exist.

Lee Everett hits the zombie, dealing 5 damage with his flashlight.


""".strip()
            return _call_validator(rules)

        if m == "conversation":
            rules = f"""
MODE: CONVERSATION OUTPUT VALIDATION

PRECHECK LABEL (may be None):
{data.get("precheck_label",None)}

PLAYER MESSAGE:
{data.get("user_text","")}

PREVIOUS REJECTED REPLY (if any):
{data.get("previous_rejected","")}

CANDIDATE REPLY (to be shown to player):
{data.get("candidate_reply","")}

RULES (return 1 ONLY if ALL are satisfied, else 0):
1) If a label is provided, reply must follow the label intent.
2) Must NOT narrate new in-world events or pretend actions were executed.
3) Must not roleplay as an NPC. 
4) Must be at most 2 sentences.
5) Must not tell the player to wait for something.

Return ONLY a single digit: 0 or 1.
""".strip()
            return _call_validator(rules)

        return True  # unknown mode -> fail-open

    except Exception as ex:
        if showPrints:
            print("[AIvalidate][ERROR]", ex)
        return True  # fail-open so you never deadlock


def AIparsing(action_text):
    """
    Query parsing model; robustly extract:
      action, requested_action, target, indirect_target, item, location, topic_of_conversation
    """
    player_area = gameSetup.player.current_area

    def char_lines():
        lines = []
        for a in _iter_all_areas():
            for c in a.characters:
                lines.append(f"ID: {getattr(c,'uid','')}, Name: {c.name}, Area: {a.name}")
        return "\n".join(lines)

    def item_lines():
        lines = []
        for a in _iter_all_areas():
            for it in a.key_items:
                lines.append(f"ID: {getattr(it,'uid','')}, Name: {it.name}, Area: {a.name}")
            for c in a.characters:
                for it in c.inventory:
                    lines.append(f"ID: {getattr(it,'uid','')}, Name: {it.name}, Holder: {c.name}")
        return "\n".join(lines)

    def area_lines():
        return "\n".join([f"ID: {getattr(a,'uid','')}, Name: {a.name}" for a in _iter_all_areas()])

    context_text = f"""
    === CHARACTERS (use the ID when referencing) ===
    {char_lines()}

    === ITEMS (use the ID when referencing) ===
    {item_lines()}

    === LOCATIONS (use the ID when referencing) ===
    {area_lines()}

    === PREVIOUS STORY ===
    {previous_text}

    === USER INPUT TO BE PARSED ===
    {action_text}
    """

    user_message = {"role": "user", "content": context_text}
    client = openai.OpenAI(api_key=key, base_url=base_url)
    response = client.chat.completions.create(
        model=model_parsing,
        messages=[parsing_message, user_message],
        max_tokens=200,
        temperature=0
    )

    response_content = response.choices[0].message.content or ""
    response_content = re.sub(r'\s+', ' ', response_content).strip()
    if showPrints:
        print("Raw AI response:")
        print(response_content)

    return response_content


def AIstorytelling(player_action, action_result, system_message):
    player_area = gameSetup.player.current_area
    chars_str = player_area.get_all_characters()

    locs_str = f"Name: {player_area.name}, "
    for linked_area in player_area.get_linked_areas():
        locs_str += f"Name: {linked_area.name}, "

    party_names = ", ".join([m.name for m in gameSetup.player.party]) if gameSetup.player.party else "[]"

    base_context = f"""
=== NEARBY CHARACTERS ===
{chars_str}
Of which in player party:
{party_names}

=== CURRENT PLAYER LOCATION ===
{gameSetup.player.current_area.name}

=== EXISTING WORLD ===
{gameSetup.drugstore_world}

=== PAST TURN WORLD RESPONSE ===
{previous_text}

=== THIS TURN PLAYER INPUT ===
{player_action}

=== THIS TURN SYSTEM RECOGNIZED ACTION ===
{action_result}

=== THIS TURN WORLD RESPONSE ===
{system_message}
""".strip()

    client = openai.OpenAI(api_key=key, base_url=base_url)

    player_name = getattr(getattr(gameSetup, "player", None), "name", "") or ""
    recognized_action = str(action_result or "").strip()

    last_bad = ""
    for attempt in range(1, 4):
        extra = ""
        if attempt > 1:
            extra = f"""

=== PREVIOUS OUTPUT (REJECTED BY VALIDATOR) ===
{last_bad}

Fix the issues that caused rejection. Do NOT repeat the same mistakes.
""".strip()

        context_text = base_context + ("\n\n" + extra if extra else "")
        user_message = {"role": "user", "content": context_text}

        resp = client.chat.completions.create(
            model=model_storytelling,
            messages=[story_message, user_message],
            max_tokens=16384,
            temperature=2,
        )
        candidate = (resp.choices[0].message.content or "").strip()

        ok = AIvalidate(
            "story",
            candidate_text=candidate,
            player_name=player_name,
            player_input=str(player_action or ""),
            recognized_action=recognized_action,
            world_system_result=str(system_message or ""),
        )

        if showPrints:
            print(f"[VALIDATION][STORY] attempt {attempt}/3 ->", "OK" if ok else "REJECT")

        if ok:
            return candidate

        last_bad = candidate

    return "(ERROR: response could not be generated for storytelling.)"


def AIconversation(user_text: str, precheck_label: str | None = None, extra_instructions: str | None = None) -> str:
    """
    Conversation mode with strict knowledge gating.

    - Lists ONLY what the player currently knows (via Character.knowledge + known_* sets),
      PLUS the current area (because you're standing there).
    - Known areas / characters / items include descriptions when available.
    - KNOWN AREA CONNECTIONS reveals ONLY direct neighbours of the CURRENT area, by name
      (even if those neighbour areas are not yet in the player's knowledge).
    - ACTIVE EVENTS shows ONLY events in the player's current area, and notes which
      (known) NPCs are involved + which exits (to neighbour areas) are blocked (if detectable).

    New:
    - `extra_instructions` lets callers append task-specific guidance without needing a label.
    - `precheck_label` still works, but we only inject guidance text (no "Label:" block).
    """

    if showPrints:
        print("[AIconversation] precheck_label:", repr(precheck_label))  # DEBUG

    p = getattr(gameSetup, "player", None)
    world = getattr(gameSetup, "drugstore_world", None)

    # ---- Safe defaults ----
    known_areas_text = "(none)"
    known_chars_text = "(none)"
    known_items_text = "(none)"
    cur_loc_name = "Unknown"
    party_names = "[]"
    connections_text = "(none)"
    events_text = "(none)"

    # ---- Collect player knowledge sets (robust to missing attrs) ----
    knowledge: dict = {}
    known_area_uids: set[str] = set()
    known_char_uids: set[str] = set()
    known_item_uids: set[str] = set()
    cur_area = None

    if p is not None:
        try:
            cur_area = getattr(p, "current_area", None)
            if cur_area is not None:
                cur_loc_name = getattr(cur_area, "name", "Unknown") or "Unknown"
        except Exception:
            cur_area = None

        try:
            party_names = ", ".join([m.name for m in getattr(p, "party", [])]) or "[]"
        except Exception:
            party_names = "[]"

        knowledge = getattr(p, "knowledge", {}) or {}

        try:
            known_area_uids = set(getattr(p, "known_areas", set()) or set())
        except Exception:
            known_area_uids = set()
        try:
            known_char_uids = set(getattr(p, "known_people", set()) or set())
        except Exception:
            known_char_uids = set()
        try:
            known_item_uids = set(getattr(p, "known_items", set()) or set())
        except Exception:
            known_item_uids = set()

        # Fallback: derive from knowledge dict if sets are empty
        if isinstance(knowledge, dict) and knowledge:
            if not known_area_uids:
                known_area_uids = {uid for uid, e in knowledge.items()
                                   if isinstance(e, dict) and e.get("entity_type") == "area"}
            if not known_char_uids:
                known_char_uids = {uid for uid, e in knowledge.items()
                                   if isinstance(e, dict) and e.get("entity_type") == "character"}
            if not known_item_uids:
                known_item_uids = {uid for uid, e in knowledge.items()
                                   if isinstance(e, dict) and e.get("entity_type") == "item"}

        # Always include the current area as known
        try:
            if cur_area is not None:
                cu = getattr(cur_area, "uid", None)
                if isinstance(cu, str) and cu:
                    known_area_uids.add(cu)
        except Exception:
            pass

    # ---- Build reverse maps for fallbacks (by uid) ----
    area_by_uid: dict[str, object] = {}
    char_by_uid: dict[str, object] = {}
    item_by_uid: dict[str, object] = {}

    try:
        if world is not None:
            for a in getattr(world, "sub_areas", []) or []:
                auid = getattr(a, "uid", None)
                if isinstance(auid, str) and auid:
                    area_by_uid[auid] = a

                for c in getattr(a, "characters", []) or []:
                    cuid = getattr(c, "uid", None)
                    if isinstance(cuid, str) and cuid:
                        char_by_uid[cuid] = c

                    for it in getattr(c, "inventory", []) or []:
                        iuid = getattr(it, "uid", None)
                        if isinstance(iuid, str) and iuid:
                            item_by_uid[iuid] = it

                    eq = getattr(c, "equipment", None)
                    if isinstance(eq, dict):
                        for it in eq.values():
                            if it is None:
                                continue
                            iuid = getattr(it, "uid", None)
                            if isinstance(iuid, str) and iuid:
                                item_by_uid[iuid] = it

                for it in getattr(a, "key_items", []) or []:
                    iuid = getattr(it, "uid", None)
                    if isinstance(iuid, str) and iuid:
                        item_by_uid[iuid] = it
    except Exception:
        pass

    def _entry_meta(uid: str, entry: dict | None, fallback_obj: object | None) -> tuple[str, str]:
        entry = entry if isinstance(entry, dict) else {}
        snap = entry.get("snapshot", {}) if isinstance(entry.get("snapshot", {}), dict) else {}

        name = entry.get("name") or snap.get("name")
        desc = entry.get("description") or snap.get("description")

        if fallback_obj is not None:
            if not name:
                name = getattr(fallback_obj, "name", None)
            if not desc:
                desc = getattr(fallback_obj, "description", None)

        return (str(name) if name else uid, str(desc) if desc else "")

    # ---- Known locations ----
    if known_area_uids:
        lines: list[str] = []
        for uid in sorted(known_area_uids):
            entry = knowledge.get(uid, {}) if isinstance(knowledge, dict) else {}
            obj = area_by_uid.get(uid)
            name, desc = _entry_meta(uid, entry, obj)
            is_outdated = bool(entry.get("is_outdated")) if isinstance(entry, dict) else False
            suffix = " (outdated)" if is_outdated else ""
            if desc:
                lines.append(f"ID: {uid}, Name: {name}{suffix}, Desc: {desc}")
            else:
                lines.append(f"ID: {uid}, Name: {name}{suffix}")
        known_areas_text = "\n".join(lines) if lines else "(none)"

    # ---- Known characters ----
    if known_char_uids and p is not None:
        lines: list[str] = []
        for uid in sorted(known_char_uids):
            entry = knowledge.get(uid, {}) if isinstance(knowledge, dict) else {}
            obj = char_by_uid.get(uid)
            name, desc = _entry_meta(uid, entry, obj)

            is_outdated = bool(entry.get("is_outdated")) if isinstance(entry, dict) else False
            suffix = " (outdated)" if is_outdated else ""

            area_label = ""
            try:
                loc = getattr(obj, "current_area", None) if obj is not None else None
            except Exception:
                loc = None

            if loc is not None:
                try:
                    loc_name = getattr(loc, "name", None)
                    loc_uid = getattr(loc, "uid", None)
                except Exception:
                    loc_name, loc_uid = None, None

                visible = False
                try:
                    visible = bool(getattr(p, "can_see_area")(loc)) if hasattr(p, "can_see_area") else False
                except Exception:
                    visible = False

                if (isinstance(loc_uid, str) and loc_uid in known_area_uids) or visible:
                    if isinstance(loc_name, str) and loc_name:
                        area_label = f", Area: {loc_name}"

            if desc:
                lines.append(f"ID: {uid}, Name: {name}{area_label}{suffix}, Desc: {desc}")
            else:
                lines.append(f"ID: {uid}, Name: {name}{area_label}{suffix}")
        known_chars_text = "\n".join(lines) if lines else "(none)"

    # ---- Known items ----
    if known_item_uids and p is not None:
        lines: list[str] = []
        for uid in sorted(known_item_uids):
            entry = knowledge.get(uid, {}) if isinstance(knowledge, dict) else {}
            obj = item_by_uid.get(uid)
            name, desc = _entry_meta(uid, entry, obj)

            is_outdated = bool(entry.get("is_outdated")) if isinstance(entry, dict) else False
            suffix = " (outdated)" if is_outdated else ""

            holder_label = ""
            area_label = ""

            try:
                holder = getattr(obj, "holder", None) if obj is not None else None
            except Exception:
                holder = None
            if holder is not None:
                try:
                    h_uid = getattr(holder, "uid", None)
                    h_name = getattr(holder, "name", None)
                except Exception:
                    h_uid, h_name = None, None
                if isinstance(h_uid, str) and h_uid in known_char_uids and isinstance(h_name, str) and h_name:
                    holder_label = f", Holder: {h_name}"

            try:
                pos = getattr(obj, "position", None) if obj is not None else None
            except Exception:
                pos = None
            if pos is not None:
                try:
                    a_uid = getattr(pos, "uid", None)
                    a_name = getattr(pos, "name", None)
                except Exception:
                    a_uid, a_name = None, None

                visible = False
                try:
                    visible = bool(getattr(p, "can_see_area")(pos)) if hasattr(p, "can_see_area") else False
                except Exception:
                    visible = False

                if (isinstance(a_uid, str) and a_uid in known_area_uids) or visible:
                    if isinstance(a_name, str) and a_name:
                        area_label = f", Area: {a_name}"

            if desc:
                lines.append(f"ID: {uid}, Name: {name}{holder_label}{area_label}{suffix}, Desc: {desc}")
            else:
                lines.append(f"ID: {uid}, Name: {name}{holder_label}{area_label}{suffix}")
        known_items_text = "\n".join(lines) if lines else "(none)"

    # ---- World summary ----
    def _world_summary(w) -> str:
        if not w:
            return "(unavailable)"
        title = getattr(w, "title", "Unknown")
        relation_to_mc = getattr(w, "relation_to_mc", "")
        chaos_state = getattr(w, "chaos_state", "Unknown")
        current_dilemma = getattr(w, "current_dilemma", "")
        current_goal = getattr(w, "current_goal", "")
        return (
            f"Title: {title}\n"
            f"Relation to protagonist: {relation_to_mc}\n"
            f"Chaos state: {chaos_state}\n"
            f"Current dilemma: {current_dilemma}\n"
            f"Current goal: {current_goal}"
        )
    world_text = _world_summary(world)

    # ---- Area connections (current only) ----
    def _current_connections() -> str:
        if p is None:
            return "(none)"
        area = getattr(p, "current_area", None)
        if area is None:
            return "(none)"

        try:
            area_name = getattr(area, "name", "Unknown") or "Unknown"
        except Exception:
            area_name = "Unknown"

        try:
            neighbors = list(area.get_linked_areas())
        except Exception:
            neighbors = []
            for lp in getattr(area, "linking_points", []) or []:
                try:
                    b = lp.get_other_area(area)
                    if b:
                        neighbors.append(b)
                except Exception:
                    pass

        shown: list[str] = []
        for b in neighbors:
            try:
                shown.append(getattr(b, "name", "Unknown") or "Unknown")
            except Exception:
                continue

        seen = set()
        uniq = []
        for nm in shown:
            if nm in seen:
                continue
            seen.add(nm)
            uniq.append(nm)

        return f"{area_name} -> [{', '.join(uniq)}]" if uniq else f"{area_name} -> []"

    connections_text = _current_connections()

    # ---- Active events (current area only) ----
    def _events_overview() -> str:
        if p is None:
            return "(none)"
        area = getattr(p, "current_area", None)
        if area is None:
            return "(none)"

        try:
            events_here = list(getattr(area, "active_events", []) or [])
        except Exception:
            events_here = []

        if not events_here:
            return "(none)"

        try:
            neighbors = list(area.get_linked_areas())
        except Exception:
            neighbors = []

        lines: list[str] = []
        for ev in events_here:
            if ev is None:
                continue
            try:
                if hasattr(ev, "is_active") and not getattr(ev, "is_active", True):
                    continue
            except Exception:
                pass

            ev_name = getattr(ev, "name", None) or getattr(ev, "event_type", None) or type(ev).__name__
            ev_desc = getattr(ev, "description", None) or getattr(ev, "details", None) or ""

            known_participants: list[str] = []
            try:
                parts = getattr(ev, "participants", None)
                if parts:
                    for ch in list(parts):
                        try:
                            cuid = getattr(ch, "uid", None)
                            cname = getattr(ch, "name", None)
                        except Exception:
                            continue
                        if isinstance(cuid, str) and cuid in known_char_uids and isinstance(cname, str) and cname:
                            known_participants.append(cname)
            except Exception:
                pass
            known_participants = sorted(set(known_participants))

            blocked: list[str] = []
            if hasattr(ev, "is_move_allowed") and neighbors:
                for nb in neighbors:
                    try:
                        ok = bool(ev.is_move_allowed(area, nb))
                    except Exception:
                        ok = True
                    if not ok:
                        try:
                            blocked.append(getattr(nb, "name", "Unknown") or "Unknown")
                        except Exception:
                            blocked.append("Unknown")
            blocked = sorted(set(blocked))

            area_name = getattr(area, "name", "Unknown") or "Unknown"
            line = f"{ev_name} in {area_name}"
            if known_participants:
                line += f" (involving: {', '.join(known_participants)})"
            if blocked:
                line += f" [blocks exits to: {', '.join(blocked)}]"
            if ev_desc:
                line += f" | Desc: {ev_desc}"
            lines.append(line)

        return "\n".join(lines) if lines else "(none)"

    events_text = _events_overview()

    # ---- Previous conversation inputs (this session) ----
    try:
        if conversation_log:
            prev_convo = "\n".join(f"- {msg}" for msg in conversation_log[-25:])
        else:
            prev_convo = "(none yet)"
    except Exception:
        prev_convo = "(unavailable)"

    # ---- Label guidance (optional) ----
    guidance = ""
    if precheck_label:
        lab = precheck_label.strip().lower()

        if lab.startswith("question"):
            guidance = (
                "Answer the player's question directly in-character. "
                "Do NOT trigger or infer in-game actions."
            )
        elif lab.startswith("long"):
            guidance = (
                "The player's message tries to do too many things at once. "
                "Ask them to split it into shorter commands."
            )
        elif lab.startswith("insufficient"):
            guidance = (
                "The message is too short/unclear to map to an action. "
                "Ask what they want to do next."
            )
        elif lab.startswith("unrelated"):
            guidance = (
                "Acknowledge briefly, then steer them back to what they want their character to do next."
            )
        elif lab.startswith("validation"):
            guidance = (
                "Explain what required detail is missing/invalid, and ask for ONLY that missing detail. "
                "Do NOT change the underlying intended action."
            )
        elif lab.startswith("undo"):
            guidance = (
                "Summarize the undo using the provided FROM/TO details, then reassure the player they can continue. "
                "Do NOT invent extra world changes."
            )
        elif lab.startswith("idle"):
            guidance = (
                "Check if the player is still there, ask if they want to continue, and remind them of possible next actions."
            )
        elif lab.startswith("impossible"):
            guidance = (
                "The player gave an action that was considered impossible, inform them of this."
            )
        elif lab.startswith("suggestion"):
            guidance = (
                "Provide a concise, concrete next-step suggestion based ONLY on the known state (no inventions)."
            )

    # ---- Extra instructions block (label guidance + caller instructions) ----
    extra_blocks: list[str] = []
    if isinstance(guidance, str) and guidance.strip():
        extra_blocks.append(guidance.strip())
    if isinstance(extra_instructions, str) and extra_instructions.strip():
        extra_blocks.append(extra_instructions.strip())

    extra_block_text = ""
    if extra_blocks:
        extra_block_text = "\n\n=== EXTRA INSTRUCTIONS ===\n" + "\n\n".join(extra_blocks) + "\n"

    # ---- Build context sent to the talking model ----
    context_text = f"""
NOTE: All lists below include ONLY elements the player currently knows about.
They are NOT the complete set of game elements; anything unknown is intentionally omitted.

=== WORLD SUMMARY (limited fields only) ===
{world_text}

=== KNOWN LOCATIONS ===
{known_areas_text}

=== KNOWN AREA CONNECTIONS ===
{connections_text}

=== KNOWN CHARACTERS ===
{known_chars_text}

=== KNOWN ITEMS ===
{known_items_text}

=== ACTIVE EVENTS (current area only) ===
{events_text}

=== CURRENT PLAYER LOCATION ===
{cur_loc_name}

=== PLAYER PARTY ===
{party_names}

=== PREVIOUS STORY (context only; do not reveal unknown content) ===
{previous_text}

=== PREVIOUS CONVERSATION (this session) ===
{prev_convo}
{extra_block_text}
=== PLAYER MESSAGE ===
{user_text}

Give your responses in max 2 sentences.
""".strip()

    if showPrints:
        print(conversation_message, context_text)

    client = openai.OpenAI(api_key=key, base_url=base_url)
    attempts = 3
    previous_rejected = ""

    for attempt in range(1, attempts + 1):
        # IMPORTANT: include the previous rejected reply so we don't loop
        retry_block = ""
        if previous_rejected:
            retry_block = f"""

=== PREVIOUS REJECTED REPLY (validator returned 0; DO NOT repeat its mistakes) ===
{previous_rejected}
"""

        # Build your context_text as you already do, then append:
        context_text = (
            context_text
            + (f"\n\n=== EXTRA INSTRUCTIONS ===\n{extra_instructions}\n" if extra_instructions else "")
            + retry_block
        )

        client = openai.OpenAI(api_key=key, base_url=base_url)
        response = client.chat.completions.create(
            model=model_talking,
            messages=[conversation_message, {"role": "user", "content": context_text}],
            max_tokens=800,
            temperature=0.8,
        )
        out = (response.choices[0].message.content or "").strip()

        ok = AIvalidate(
            "conversation",
            payload={
                "candidate_reply": out,
                "user_text": user_text,
                "precheck_label": precheck_label,
                "previous_rejected": previous_rejected,
            },
        )
        if showPrints:
            print(f"[VALIDATION][CONVO] attempt {attempt}/3 -> {'OK' if ok else 'REJECT'}")

        if ok:
            # (keep your existing conversation_log behavior here)
            return out

        previous_rejected = out

    if showPrints:
        print("[AIconversation] rejected 2 times -> returning '' to trigger parsing fallback")
    return ""


# Launch the actual story step-by-step
def get_story(player_input: str):
    """
    Single entry point for a player turn.

    Pipeline (your intended design):
      0) Start-of-turn knowledge refresh (current area + visible contents).
         Also ensure an INITIAL undo snapshot exists (so the first mistake is undoable).
      1) If we're waiting for an UNDO confirmation reply: interpret yes/no (bypass precheck).
      2) If we're in a correction phase: treat this as a slot-fix reply (bypass precheck).
      3) If we're waiting for a risky-action confirmation reply: interpret yes/no (bypass precheck).
      4) Otherwise run AIprecheck on raw text:
           - undo      -> run AIundo, then ask for confirmation to revert
           - question / long / insufficient / unrelated -> AIconversation
           - clear     -> normal game flow
      5) For clear:
           - parse into action list
           - run _needs_confirmation on the FIRST action; if needed, ask confirmation and stop
           - validate_action_sequence:
               * if SINGLE action fails -> enter correction phase and ask for only the missing detail
               * if MULTI action chain fails -> rephrase with AIconversation and ask the player to retry
           - execute all actions
           - end-of-turn knowledge refresh (current area + visible contents)
           - save undo snapshot (dedupe consecutive identical states)
           - edit_system_message, then AIstorytelling
    """
    global previous_text

    # ---- Robust globals: avoid NameErrors if the file is mid-refactor ----
    g = globals()
    g.setdefault("pending_confirmation_action", None)
    g.setdefault("pending_confirmation_original_input", "")
    g.setdefault("pending_correction_actions", None)
    g.setdefault("pending_correction_failed_index", -1)
    g.setdefault("pending_correction_original_input", "")
    g.setdefault("pending_correction_error", "")
    g.setdefault("pending_undo_confirm_choice", None)          # int | None
    g.setdefault("pending_undo_confirm_from_index", None)      # int | None
    g.setdefault("undo_snapshots", [])                         # list[dict]
    g.setdefault("completed_action_turns", 0)  # counts only turns that reach story generation

    text = (player_input or "").strip()
    if not text:
        return "Lee Everett hesitates, unsure what to do.", 0

    # ------------------------------------------------------------------
    # 0) Start-of-turn knowledge refresh (FIXED: records same-room chars/items)
    # ------------------------------------------------------------------
    try:
        p = getattr(gameSetup, "player", None)
        if p is not None:
            if hasattr(p, "refresh_known_state"):
                p.refresh_known_state()

            area = getattr(p, "current_area", None)
            if area is not None and hasattr(p, "remember"):
                try:
                    p.remember(area, reason="You are here right now.")
                except Exception:
                    pass

                for c in getattr(area, "characters", []) or []:
                    try:
                        p.remember(c, reason="You can see them in this area.")
                    except Exception:
                        pass

                for it in getattr(area, "key_items", []) or []:
                    try:
                        p.remember(it, reason="You can see it here.")
                    except Exception:
                        pass
    except Exception as ex:
        if showPrints:
            print("[KNOWLEDGE][ERROR at turn start]", ex)

    # ------------------------------------------------------------------
    # 0b) Ensure an INITIAL undo snapshot exists (so the 1st turn is undoable)
    # ------------------------------------------------------------------
    try:
        import saveLoad

        snapshots = g["undo_snapshots"]
        if isinstance(snapshots, list) and len(snapshots) == 0:
            state = saveLoad._serialize_current_state()
            meta = {
                "player_input": "(start)",
                "player_area": getattr(getattr(gameSetup.player, "current_area", None), "name", "Unknown"),
            }
            snapshots.append({"state": state, "meta": meta})
            if showPrints:
                print(f"[UNDO] initial snapshot saved, count={len(snapshots)}")
    except Exception as ex:
        if showPrints:
            print("[UNDO][INIT SNAPSHOT ERROR]", ex)

    # ------------------------------------------------------------------
    # 1) UNDO confirmation phase (bypass precheck)
    # ------------------------------------------------------------------
    if g["pending_undo_confirm_choice"] is not None:
        ans = text.strip().lower()

        def _is_yes(s: str) -> bool:
            if not s:
                return False
            # Keep it simple and robust (no heuristics beyond yes/no parsing)
            return s in {"y", "yes", "yeah", "yep", "sure", "ok", "okay"} or s.startswith("yes")

        def _snap_label(snap: object, default: str) -> str:
            if not isinstance(snap, dict):
                return default
            meta = snap.get("meta") or {}
            loc = meta.get("player_area") or "Unknown"
            inp = meta.get("player_input") or ""
            if isinstance(inp, str) and len(inp) > 80:
                inp = inp[:80] + "…"
            return f"location={loc} | input={inp or '(no input)'}"

        if _is_yes(ans):
            try:
                import saveLoad

                snapshots = g.get("undo_snapshots") or []
                if not isinstance(snapshots, list) or not snapshots:
                    raise RuntimeError("No undo snapshots available.")

                n = len(snapshots)
                if n < 2:
                    # Should rarely happen now that you save an initial snapshot, but keep it safe.
                    raise RuntimeError("Not enough undo history to revert.")

                # Current snapshot is the last one
                from_idx_1based = int(g.get("pending_undo_confirm_from_index") or n)
                if from_idx_1based < 1 or from_idx_1based > n:
                    from_idx_1based = n

                # The chosen target MUST be a past snapshot (exclude current n)
                to_idx_1based = int(g["pending_undo_confirm_choice"])
                to_idx_1based = max(1, min(to_idx_1based, n - 1))

                from_snap = snapshots[from_idx_1based - 1]
                to_snap = snapshots[to_idx_1based - 1]

                state_dict = to_snap.get("state") if isinstance(to_snap, dict) else None
                if not isinstance(state_dict, dict):
                    raise RuntimeError("Selected snapshot is missing a valid 'state' dict.")

                # APPLY IN-PLACE (new saveLoad design)
                if not hasattr(saveLoad, "apply_game_state_dict"):
                    raise RuntimeError("saveLoad.apply_game_state_dict is missing (cannot apply undo).")

                ok = bool(saveLoad.apply_game_state_dict(state_dict))
                if not ok:
                    raise RuntimeError("apply_game_state_dict returned False.")

                # Truncate history so the applied snapshot becomes the new "current"
                g["undo_snapshots"] = snapshots[:to_idx_1based]

                # Clear pending confirmation flags
                g["pending_undo_confirm_choice"] = None
                g["pending_undo_confirm_from_index"] = None

                # Refresh player knowledge AFTER undo (so same-room chars/items show up)
                try:
                    p = getattr(gameSetup, "player", None)
                    if p is not None:
                        if hasattr(p, "refresh_known_state"):
                            p.refresh_known_state()

                        area = getattr(p, "current_area", None)
                        if area is not None and hasattr(p, "remember"):
                            try:
                                p.remember(area, reason="You are here right now (after undo).")
                            except Exception:
                                pass
                            for c in getattr(area, "characters", []) or []:
                                try:
                                    p.remember(c, reason="You can see them in this area (after undo).")
                                except Exception:
                                    pass
                            for it in getattr(area, "key_items", []) or []:
                                try:
                                    p.remember(it, reason="You can see it here (after undo).")
                                except Exception:
                                    pass
                except Exception as ex:
                    if showPrints:
                        print("[KNOWLEDGE][ERROR after undo]", ex)

                summary = (
                    "UNDO SUMMARY:\n"
                    f"FROM: snapshot {from_idx_1based} ({_snap_label(from_snap, 'current')})\n"
                    f"TO:   snapshot {to_idx_1based} ({_snap_label(to_snap, 'target')})\n"
                )

                reply = AIconversation(summary, precheck_label="undo")
                previous_text = reply
                return reply, 0

            except Exception as ex:
                if showPrints:
                    print("[UNDO][APPLY ERROR]", ex)
                g["pending_undo_confirm_choice"] = None
                g["pending_undo_confirm_from_index"] = None
                msg = f"(Undo failed internally: {ex})"
                previous_text = msg
                return msg, 0

        # Anything other than "yes" cancels
        g["pending_undo_confirm_choice"] = None
        g["pending_undo_confirm_from_index"] = None
        msg = "Okay, I won’t undo that. Continue from here."
        previous_text = msg
        return msg, 0


    # ------------------------------------------------------------------
    # 2) Correction phase (bypass precheck)
    # ------------------------------------------------------------------
    if g["pending_correction_actions"] is not None:
        return _handle_correction_reply(player_input)

    # ------------------------------------------------------------------
    # 3) Risky-action confirmation phase (bypass precheck)
    # ------------------------------------------------------------------
    forced_actions: list[dict] | None = None
    story_input = player_input

    if g["pending_confirmation_action"] is not None:
        ans = text.lower()
        if ("yes" in ans) or ("yeah" in ans):
            forced_actions = g["pending_confirmation_action"]
            story_input = g["pending_confirmation_original_input"] or player_input

            g["pending_confirmation_action"] = None
            g["pending_confirmation_original_input"] = ""
        else:
            msg = "Okay, I won't do that. Try another action."
            g["pending_confirmation_action"] = None
            g["pending_confirmation_original_input"] = ""
            previous_text = msg
            return msg, 0

    # ------------------------------------------------------------------
    # 4) Pre-check (ONLY if not forced_actions)
    # ------------------------------------------------------------------
    if forced_actions is None:
        label = AIprecheck(text)
        lab_low = (label or "").strip().lower()
        if showPrints:
            print(f"[PRECHECK] label={lab_low!r}")

        if lab_low == "undo":
            snapshots = g.get("undo_snapshots") or []
            n = len(snapshots) if isinstance(snapshots, list) else 0

            if n < 2:
                summary = (
                    "UNDO SUMMARY:\n"
                    "FROM: (current)\n"
                    "TO: (none)\n"
                    "No undo history exists yet."
                )
                reply = AIconversation(summary, precheck_label="undo")
                previous_text = reply
                return reply, 0

            choice = AIundo(player_input, snapshots)
            if not isinstance(choice, int) or choice <= 0:
                summary = (
                    "UNDO SUMMARY:\n"
                    "FROM: (current)\n"
                    "TO: (cancelled)\n"
                    "Undo was cancelled."
                )
                reply = AIconversation(summary, precheck_label="undo")
                previous_text = reply
                return reply, 0

            if choice >= n:
                choice = n - 1

            to_meta = (snapshots[choice - 1].get("meta") or {}) if isinstance(snapshots[choice - 1], dict) else {}
            to_loc = to_meta.get("player_area", "Unknown")
            to_inp = to_meta.get("player_input", "")
            if isinstance(to_inp, str) and len(to_inp) > 60:
                to_inp = to_inp[:60] + "…"

            confirm_msg = (
                f"Do you want to undo to snapshot {choice} "
                f"(location: {to_loc}, last input: {to_inp or '(none)'})? "
                "Write yes to continue, anything else to cancel."
            )
            g["pending_undo_confirm_choice"] = choice
            g["pending_undo_confirm_from_index"] = n
            previous_text = confirm_msg
            return confirm_msg, 0

        if lab_low == "redo":
            msg = "Okay."
            previous_text = msg
            return msg, 0

        if lab_low and lab_low != "clear":
            reply = AIconversation(player_input, precheck_label=label)

            if isinstance(reply, str) and reply.strip():
                previous_text = reply
                return reply, 0

            # If we got here, convo failed 3x and returned ""
            if showPrints:
                print("[PRECHECK] AIconversation rejected 3x -> falling back to action parsing")

        player_actions = process_player_input(player_input)
        if not player_actions:
            return "Lee Everett hesitates, unsure what to do.", 0

        first_action = player_actions[0]
        desc = _needs_confirmation(first_action, gameSetup.player)
        if desc:
            # If _needs_confirmation returns a full custom prompt (e.g., for do_nothing),
            # use it verbatim instead of wrapping it in "Do I understand correctly..."
            low_desc = desc.strip().lower()
            is_custom = (
                "would you like to carry it out anyway" in low_desc
                or low_desc.startswith("this action would")
            )

            if is_custom:
                confirm_msg = f"{desc.strip()} Write yes to continue, anything else to cancel."
            else:
                confirm_msg = (
                    f"Do I understand correctly that you want to {desc.strip()}? "
                    "Write yes to continue, anything else to cancel."
                )

            g["pending_confirmation_action"] = player_actions
            g["pending_confirmation_original_input"] = player_input
            previous_text = confirm_msg
            return confirm_msg, 0


    else:
        player_actions = forced_actions
        if not player_actions:
            msg = "Okay."
            previous_text = msg
            return msg, 0
        first_action = player_actions[0]

    # ------------------------------------------------------------------
    # 5) Validate BEFORE execution
    # ------------------------------------------------------------------
    try:
        error = validate_action_sequence(player_actions, event_manager, gameSetup.player)
    except Exception as ex:
        if showPrints:
            print("[VALIDATE_SEQUENCE][ERROR]", ex)
        msg = f"(Internal validation error while checking your actions: {ex})"
        previous_text = msg
        return msg, 0

    if error:
        if len(player_actions) > 1:
            conv_line = ""
            try:
                conv_line = AIconversation(
                    f"When trying to carry out my actions, the game reported: {error}",
                    precheck_label="validation_error",
                )
            except Exception as ex:
                if showPrints:
                    print("[CHAIN VALIDATION][AIconversation ERROR]", ex)

            msg = (conv_line + "\n\n" if conv_line else "") + "Please try again with a simpler, more precise command."
            previous_text = msg
            return msg, 0

        failed_index = 0
        base_error = error
        m = re.match(r"Action\s+(\d+):\s*(.*)", error)
        if m:
            try:
                failed_index = max(0, int(m.group(1)) - 1)
            except ValueError:
                failed_index = 0
            if m.group(2).strip():
                base_error = m.group(2).strip()

        g["pending_correction_actions"] = player_actions
        g["pending_correction_failed_index"] = failed_index
        g["pending_correction_original_input"] = player_input
        g["pending_correction_error"] = base_error

        conv_line = ""
        try:
            conv_line = AIconversation(
                f"When trying to carry out my action, the game reported: {base_error}",
                precheck_label="validation_error",
            )
        except Exception as ex:
            if showPrints:
                print("[CORRECTION][AIconversation ERROR]", ex)

        core_msg = (
            "Type only the missing or corrected detail (a character, item, or place), "
            "or write 'cancel' to cancel."
        )

        msg = f"{conv_line}\n\n{core_msg}" if conv_line else core_msg
        previous_text = msg
        return msg, 0

    # ------------------------------------------------------------------
    # 6) Execute all actions
    # ------------------------------------------------------------------
    system_parts: list[str] = []
    for act in player_actions:
        try:
            player_turn = activate_action(act, event_manager, gameSetup.player)
        except Exception as ex:
            player_turn = f"(Internal execution error for action {act.get('action','0')}: {ex})"
        if player_turn:
            system_parts.append(player_turn)

    response = "\n".join(system_parts) + ("\n" if system_parts else "")
    if showPrints:
        print("System respons:")
        print(response)

    # ------------------------------------------------------------------
    # 7) End-of-turn knowledge refresh
    # ------------------------------------------------------------------
    try:
        p = getattr(gameSetup, "player", None)
        if p is not None:
            if hasattr(p, "refresh_known_state"):
                p.refresh_known_state()
            area = getattr(p, "current_area", None)
            if area is not None and hasattr(p, "remember"):
                try:
                    p.remember(area, reason="You are here right now (after the turn).")
                except Exception:
                    pass
                for c in getattr(area, "characters", []) or []:
                    try:
                        p.remember(c, reason="You can see them in this area (after the turn).")
                    except Exception:
                        pass
                for it in getattr(area, "key_items", []) or []:
                    try:
                        p.remember(it, reason="You can see it here (after the turn).")
                    except Exception:
                        pass
    except Exception as ex:
        if showPrints:
            print("[KNOWLEDGE][ERROR at turn end]", ex)

    # ------------------------------------------------------------------
    # 8) Save undo snapshot (dedupe identical consecutive states)
    # ------------------------------------------------------------------
    try:
        import json
        import saveLoad

        snapshots = g["undo_snapshots"]
        state = saveLoad._serialize_current_state()
        meta = {
            "player_input": story_input,
            "player_area": getattr(getattr(gameSetup.player, "current_area", None), "name", "Unknown"),
        }

        def _stable(s):
            return json.dumps(s, sort_keys=True, ensure_ascii=False)

        if isinstance(snapshots, list) and snapshots:
            prev_state = snapshots[-1].get("state") if isinstance(snapshots[-1], dict) else None
            if isinstance(prev_state, dict) and _stable(prev_state) == _stable(state):
                if showPrints:
                    print("[UNDO] snapshot identical to previous; not saving duplicate.")
            else:
                snapshots.append({"state": state, "meta": meta})
                if showPrints:
                    print(f"[UNDO] snapshot saved, count={len(snapshots)}")
        elif isinstance(snapshots, list):
            snapshots.append({"state": state, "meta": meta})
            if showPrints:
                print(f"[UNDO] snapshot saved, count={len(snapshots)}")
    except Exception as ex:
        if showPrints:
            print("[UNDO][SNAPSHOT SAVE ERROR]", ex)

    # ------------------------------------------------------------------
    # 9) Story generation
    # ------------------------------------------------------------------
    edited_response = edit_system_message(response)
    gameOver = checkEnd()

    first_action = player_actions[0] if player_actions else {}
    try:
        story = AIstorytelling(story_input, first_action.get("action", None), edited_response)
    except Exception as ex:
        if showPrints:
            print("[AISTORY][ERROR]", ex)
        story = edited_response or "(The world reacts in silence.)"

    previous_text = edited_response

    # --- Periodic suggestion every 2 completed action-turns ---
    if gameOver == None:
        try:
            g["completed_action_turns"] = int(g.get("completed_action_turns") or 0) + 1
        except Exception:
            g["completed_action_turns"] = 1

        if (g["completed_action_turns"] % 2) == 0:
            try:
                tip = AIconversation(
                    user_text=(
                        f"Player just did: {story_input}\n"
                        f"World/system result: {edited_response}\n"
                        "Give one concrete suggestion for what the player could do next."
                    ),
                    precheck_label="suggestion",
                    extra_instructions=(
                        "Suggest a single in-world action the player can perform to continue the story based on "
                        "current events, as a follow up of what the player just did, or to move towards the CURRENT GOAL. " \
                        "Try to give situationally relevant advice. "
                        "Base it ONLY on KNOWN LOCATIONS/CHARACTERS/ITEMS/ACTIVE EVENTS and the CURRENT GOAL. "
                        "Do NOT invent new entities, locations, or facts."
                    ),
                )
                if isinstance(tip, str) and tip.strip():
                    story = story.rstrip() + "\n\n" + tip.strip()
            except Exception as ex:
                if showPrints:
                    print("[SUGGESTION][ERROR]", ex)

    return story, gameOver

