# actions.py
from typing import Iterable
import gameRenderer
import gameSetup
from typing import Optional
from gameEvents import (
    FightEvent,
    BlockadeEvent,
    EventManager
)

# ==== Validating actions ====

def validate_action(action_dict: dict, event_manager: EventManager, action_taker: gameRenderer.Character) -> Optional[str]:
    """
    Polite, specific validation for all actions, including ask_action.
    - Handles ID or name fallbacks.
    - For 'examine'/'search', if ANY of item/character/location is valid, don't block.
    - For 'ask_action', validates as if the asked character is the actor.
    - SPECIAL: 'inform' gracefully degrades to 'talk' when the subject doesn't exist in the world.
    """
    # ---------- Helpers: world-wide resolvers ----------
    try:
        import gameSetup
        WORLD = getattr(gameSetup, "drugstore_world", None)
    except Exception:
        WORLD = None

    def iter_all_areas():
        if not WORLD:
            return []
        if hasattr(WORLD, "all_sub_areas"):
            return list(WORLD.all_sub_areas)
        if hasattr(WORLD, "sub_areas"):
            return list(WORLD.sub_areas)
        # Fallback: walk graph
        seen, stack = set(), [action_taker.current_area]
        out = []
        while stack:
            a = stack.pop()
            if a in seen:
                continue
            seen.add(a)
            out.append(a)
            if hasattr(a, "get_linked_areas"):
                for nb in a.get_linked_areas():
                    if nb not in seen:
                        stack.append(nb)
        return out

    def find_character_by_name(name: str):
        if not name:
            return None, None
        name_low = name.strip().lower()
        for area in iter_all_areas():
            for c in getattr(area, "characters", []):
                if getattr(c, "name", "").lower() == name_low:
                    return c, area
        return None, None

    def find_character_by_uid(uid: str):
        if not uid:
            return None, None
        for area in iter_all_areas():
            for c in getattr(area, "characters", []):
                if getattr(c, "uid", None) == uid:
                    return c, area
        return None, None

    def find_item_anywhere(name_or_uid: str):
        if not name_or_uid:
            return None, None, None, None
        key = name_or_uid.strip().lower()
        for area in iter_all_areas():
            # area floor
            for it in getattr(area, "key_items", []):
                id_hit = (getattr(it, "uid", "").lower() == key)
                name_hit = (getattr(it, "name", "").lower() == key)
                if id_hit or name_hit:
                    return it, "floor", area, None
            # held
            for c in getattr(area, "characters", []):
                for it in getattr(c, "inventory", []):
                    id_hit = (getattr(it, "uid", "").lower() == key)
                    name_hit = (getattr(it, "name", "").lower() == key)
                    if id_hit or name_hit:
                        return it, "held", c.current_area, c
        return None, None, None, None

    def find_area_by_name_or_uid(name_or_uid: str):
        if not name_or_uid:
            return None
        key = name_or_uid.strip().lower()
        for area in iter_all_areas():
            if getattr(area, "name", "").lower() == key or getattr(area, "uid", "").lower() == key:
                return area
        return None

    # ---------- Friendly phrasing ----------
    def polite(s: str) -> str:
        if not s:
            return s
        return s if s.endswith((".", "!", "?")) else s + "."

    # ---------- Multi-candidate helpers ----------
    def any_ok(*msgs) -> bool:
        return any(m is None for m in msgs if m is not Ellipsis)

    def pick_best_error(*msgs) -> Optional[str]:
        ordered = [m for m in msgs if (m is not None and m is not Ellipsis)]
        return ordered[0] if ordered else None

    # ---------- Pull fields ----------
    action_identifier = action_dict.get("action")

    requested_action = action_dict.get("requested action")

    target = action_dict.get("target")
    indirect_target = action_dict.get("second target")
    item = action_dict.get("item")
    location = action_dict.get("location")

    # raw ids/names (support ID-first world)
    target_id = action_dict.get("target_id")
    indirect_target_id = action_dict.get("indirect_target_id")
    item_id = action_dict.get("item_id")
    location_id = action_dict.get("location_id")

    target_name = action_dict.get("target_name")
    indirect_target_name = action_dict.get("indirect_target_name")
    item_name = action_dict.get("item_name")
    location_name = action_dict.get("location_name")

    topic = action_dict.get("topic")

    # ---------- Global guards ----------
    if not getattr(action_taker, "is_alive", True):
        return f"{getattr(action_taker, 'name', 'This character')} can’t act because they’re dead."

    active_event = event_manager.get_active_event_for_character(action_taker)
    if active_event and not active_event.is_action_allowed(action_identifier):
        actor_name = getattr(action_taker, "name", "This character")
        msg = f"{actor_name} is in the middle of '{active_event.name}', so they can’t '{action_identifier}' right now"
        return polite(msg)

    # ---------- Explain helpers (parameterized by actor POV) ----------
    def explain_character(who, who_id_or_name, role: str, *, actor: gameRenderer.Character, allow_dead: bool = False):
        """Returns None if OK; otherwise a polite reason (relative to actor.current_area)."""
        # If we already have the object, decide with real presence/party checks
        if who is not None:
            if not allow_dead and not getattr(who, "is_alive", True):
                return f"{who.name} is dead and can’t participate"
            # Treat same-area OR party members as "here"
            if who in getattr(actor.current_area, "characters", []) or who in getattr(actor, "party", []):
                return None
            loc = getattr(who, "current_area", None)
            if loc and hasattr(loc, "name"):
                return f"{who.name} isn’t here — they’re in {loc.name}"
            return f"{who.name} isn’t here in {actor.current_area.name}"

        # Resolve by id/name if we don't have the object yet
        if who_id_or_name:
            found, area = find_character_by_uid(who_id_or_name)
            if not found:
                found, area = find_character_by_name(who_id_or_name)
            if found:
                if not allow_dead and not getattr(found, "is_alive", True):
                    return f"{found.name} is dead and can’t participate"

                # Prefer the character's actual current_area if 'area' probe is None
                real_area = area or getattr(found, "current_area", None)

                # Party members count as "here"
                if found in getattr(actor, "party", []):
                    return None

                # If we know where they are and it's not here, explain where
                if real_area and real_area is not actor.current_area:
                    return f"{found.name} isn’t here — they’re over in {real_area.name}"

                # Same area (or unknown but resolved) => allow interaction
                return None
            else:
                return f"{actor.name} doesn’t know anyone identified as '{who_id_or_name}'"

        return f"It is not clear which {role} is meant?"

    def explain_item(itm, itm_id_or_name, intent: str, *, actor: gameRenderer.Character):
        """Returns None if OK (relative to actor.current_area); otherwise a polite reason."""
        if itm is not None:
            known = getattr(itm, "known_by", [])
            if actor not in known and itm not in getattr(actor, "inventory", []):
                return f"{actor.name} doesn’t recognize '{itm.name}' well enough to {intent} it here"
            if itm in getattr(actor, "inventory", []):
                if intent == "pick up":
                    return f"'{itm.name}' isn’t on the floor — {actor.name} already has it"
                return None
            if itm in getattr(actor.current_area, "key_items", []):
                return None
            for c in getattr(actor.current_area, "characters", []):
                if itm in getattr(c, "inventory", []):
                    return f"'{itm.name}' is being held by {c.name} right now"
            return f"'{itm.name}' isn’t here in {actor.current_area.name}"
        if itm_id_or_name:
            found, where, area, holder = find_item_anywhere(itm_id_or_name)
            if found:
                if where == "floor" and area is not actor.current_area:
                    return f"'{found.name}' is on the floor in {area.name}, not here"
                if where == "held":
                    if area is actor.current_area:
                        return f"'{found.name}' is being held by {holder.name} right here"
                    else:
                        return f"'{found.name}' is with {holder.name} over in {area.name}"
                return f"{actor.name} can’t use '{found.name}' from here"
            else:
                return f"{actor.name} doesn’t see any item identified as '{itm_id_or_name}' anywhere"
        return f"It’s unclear which item {actor.name} wants to {intent}"

    def explain_location(loc, loc_id_or_name, intent: str = "move", *, actor: gameRenderer.Character, allow_current: bool = False):
        """Returns None if OK; otherwise a polite reason (relative to actor.current_area)."""
        if loc is not None:
            if loc == actor.current_area:
                if allow_current:
                    return None
                return f"{actor.name} is already in {loc.name}"
            if intent == "move":
                linked = set(getattr(actor.current_area, "get_linked_areas", lambda: [])())
                if loc not in linked:
                    return f"{actor.name} can’t reach {loc.name} directly from here"
                block = event_manager.validate_movement(actor.current_area, loc)
                if block:
                    return block
            return None
        if loc_id_or_name:
            found = find_area_by_name_or_uid(loc_id_or_name)
            if found is None:
                return f"I don’t know any place identified as '{loc_id_or_name}'"
            if found == actor.current_area:
                if allow_current:
                    return None
                return f"{actor.name} is already in {found.name}"
            if intent == "move":
                linked = set(getattr(actor.current_area, "get_linked_areas", lambda: [])())
                if found not in linked:
                    return f"{actor.name} can’t reach {found.name} directly from here"
                block = event_manager.validate_movement(actor.current_area, found)
                if block:
                    return block
            return None
        return f"It’s unclear where {actor.name} wants to {intent}"

    # ---------- Subject existence helper for INFORM ----------
    def subject_exists_in_world(item_id_or_name: Optional[str],
                               area_id_or_name: Optional[str],
                               person_id_or_name: Optional[str]) -> bool:
        # Any resolvable entity counts as existing
        if item_id_or_name:
            it, *_ = find_item_anywhere(item_id_or_name)
            if it:
                return True
        if area_id_or_name:
            if find_area_by_name_or_uid(area_id_or_name):
                return True
        if person_id_or_name:
            c, area = find_character_by_uid(person_id_or_name)
            if not c:
                c, area = find_character_by_name(person_id_or_name)
            if c:
                return True
        return False

    def first_nonempty(*vals) -> Optional[str]:
        for v in vals:
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    # ---------- Per-action validation helpers (actor-aware) ----------
    def validate_move(loc, loc_id_or_name, *, actor):
        # We call explain_location with a non-'move' intent so it doesn't enforce
        # adjacency or blockades here (those are done during execution).
        return explain_location(loc, loc_id_or_name, "travel", actor=actor)

    def validate_talk(tgt, tgt_id_or_name, *, actor):
        return explain_character(tgt, tgt_id_or_name, "person", actor=actor)

    def validate_pickup(itm, itm_id_or_name, *, actor):
        return explain_item(itm, itm_id_or_name, "pick up", actor=actor)

    def validate_use(itm, itm_id_or_name, tgt, tgt_id_or_name, *, actor):
        msg = explain_item(itm, itm_id_or_name, "use", actor=actor)
        if msg:
            return msg
        if tgt is not None or tgt_id_or_name:
            return explain_character(tgt, tgt_id_or_name, "person", actor=actor)
        return None

    def validate_give(itm, itm_id_or_name, recv, recv_id_or_name, *, actor):
        # must be in actor inventory
        if itm is not None:
            if itm not in getattr(actor, "inventory", []):
                return f"'{itm.name}' isn’t in {actor.name}'s inventory"
        else:
            found, where, area, holder = find_item_anywhere(itm_id_or_name)
            if not found:
                return f"{actor.name} doesn’t see any item identified as '{itm_id_or_name}'"
            if holder is not actor:
                if holder is None and where == "floor":
                    return f"'{found.name}' is on the floor in {area.name}, not in {actor.name}'s inventory"
                return f"{holder.name} has '{found.name}', not {actor.name}"
        return explain_character(recv, recv_id_or_name, "recipient", actor=actor)

    def validate_harm(tgt, tgt_id_or_name, *, actor):
        return explain_character(tgt, tgt_id_or_name, "target", actor=actor, allow_dead=False)

    def validate_steal(itm, itm_id_or_name, victim, victim_id_or_name, *, actor):
        """
        Validate a steal action:
          - victim must be a reachable character
          - an item must be specified
          - the victim must actually have that item (if specified by id/name)
        """
        # First: make sure the victim is a valid character in reach
        msg = explain_character(victim, victim_id_or_name, "victim", actor=actor, allow_dead=True)
        if msg:
            return msg

        # New: require some item specification at all
        if itm is None and not itm_id_or_name:
            return "There is no item specified to be stolen."

        # victim must have the item (if specified)
        if itm is not None:
            if itm not in getattr(victim, "inventory", []):
                return f"{victim.name} doesn’t have '{itm.name}'"
        elif itm_id_or_name:
            inv = getattr(victim, "inventory", [])
            if all(
                (getattr(it, "uid", "").lower() != itm_id_or_name.lower()
                 and getattr(it, "name", "").lower() != itm_id_or_name.lower())
                for it in inv
            ):
                return f"{victim.name} doesn’t have '{itm_id_or_name}'"

        return None

    def validate_search(loc, loc_id_or_name, person, person_id_or_name, *, actor):
        # area OR person is fine; allow_dead=True for person (can search a corpse)
        m_loc = m_person = Ellipsis
        if loc is not None or loc_id_or_name:
            m_loc = explain_location(loc, loc_id_or_name, "search", actor=actor, allow_current=True)
            if m_loc is not None:
                # For remote areas, we still disallow "remote" search (you must be there)
                loc_obj = loc or find_area_by_name_or_uid(loc_id_or_name)
                if loc_obj is not None and loc_obj is not actor.current_area:
                    m_loc = f"{loc_obj.name} isn’t this area — move there first to search it closely"
        if person is not None or person_id_or_name:
            m_person = explain_character(person, person_id_or_name, "person", actor=actor, allow_dead=True)
        # If either is OK, accept
        if any_ok(m_loc, m_person):
            return None
        return pick_best_error(m_loc, m_person)

    def validate_inform(receiver, receiver_id_or_name, *, actor):
        # Only presence of the receiver is required here; subject handling is special-cased below.
        return explain_character(receiver, receiver_id_or_name, "person", actor=actor)

    def validate_equip(itm, itm_id_or_name, *, actor):
        # item must be in inventory to equip
        if itm is not None:
            if itm not in getattr(actor, "inventory", []):
                return f"'{itm.name}' isn’t in {actor.name}'s inventory"
            return None
        if itm_id_or_name:
            found, where, area, holder = find_item_anywhere(itm_id_or_name)
            if not found:
                return f"{actor.name} doesn’t see any item identified as '{itm_id_or_name}'"
            if holder is not actor:
                if holder is None and where == "floor":
                    return f"'{found.name}' is on the floor in {area.name}, not in {actor.name}'s inventory"
                return f"{holder.name} has '{found.name}', not {actor.name}"
            return None
        return "Which item should be equipped?"

    def validate_unequip(itm, itm_id_or_name, *, actor):
        # Must be owned; if specified, prefer that
        if itm is not None:
            if itm not in getattr(actor, "inventory", []):
                return f"'{itm.name}' isn’t in {actor.name}'s inventory"
            return None
        if itm_id_or_name:
            found, where, area, holder = find_item_anywhere(itm_id_or_name)
            if not found:
                return f"{actor.name} doesn’t see any item identified as '{itm_id_or_name}'"
            if holder is not actor:
                return f"{actor.name} doesn’t have '{found.name}' equipped"
            return None
        return "Which item should be unequipped?"

    def validate_drop(itm, itm_id_or_name, *, actor):
        if itm is not None and itm not in getattr(actor, "inventory", []):
            return f"'{itm.name}' isn’t in {actor.name}'s inventory"
        if itm is None and itm_id_or_name:
            found, where, area, holder = find_item_anywhere(itm_id_or_name)
            if not found:
                return f"{actor.name} doesn’t see any item identified as '{itm_id_or_name}'"
            if holder is not actor:
                if holder is None and where == "floor":
                    return f"'{found.name}' is already on the floor in {area.name}"
                return f"{actor.name} doesn’t have '{found.name}' — {holder.name} does"
        if itm is None and not itm_id_or_name:
            return f"Which item should {actor.name} drop?"
        return None

    # ---------- Top-level action validation ----------
    if action_identifier == 'move':
        return polite(validate_move(location, location_id or location_name, actor=action_taker)) if validate_move(location, location_id or location_name, actor=action_taker) else None

    elif action_identifier == 'talk':
        return polite(validate_talk(target, target_id or target_name, actor=action_taker)) if validate_talk(target, target_id or target_name, actor=action_taker) else None

    elif action_identifier == 'examine':
        # Nothing at all?
        if not any([target, location, item, target_id, location_id, item_id, target_name, location_name, item_name]):
            return "Examine action is recognized, but not the matter to be examined."

        m_item = m_char = m_loc = Ellipsis
        if item or (item_id or item_name):
            m_item = explain_item(item, item_id or item_name, "examine", actor=action_taker)
        if target or (target_id or target_name):
            m_char = explain_character(target, target_id or target_name, "person", actor=action_taker)
        if location or (location_id or location_name):
            loc_err = explain_location(location, location_id or location_name, "examine", actor=action_taker, allow_current=True)
            m_loc = None if not loc_err else loc_err
        if any_ok(m_item, m_char, m_loc):
            return None
        return polite(pick_best_error(m_item, m_char, m_loc))

    elif action_identifier == 'search':
        msg = validate_search(location, location_id or location_name, target, target_id or target_name, actor=action_taker)
        return polite(msg) if msg else None

    elif action_identifier == 'inform':
        # Validate receiver presence first
        msg = validate_inform(target, target_id or target_name, actor=action_taker)
        if msg:
            return polite(msg)

        # SUBJECT RESOLUTION:
        # If the subject (item/area/person via indirect_target) does NOT exist anywhere in the world,
        # we silently convert this action to a TALK with topic text.
        subject_ok = subject_exists_in_world(
            item_id_or_name=item_id or item_name,
            area_id_or_name=location_id or location_name,
            person_id_or_name=indirect_target_id or indirect_target_name
        )

        if not subject_ok:
            # Build a topic from provided free text or the raw subject token(s)
            raw_subject = first_nonempty(
                topic,
                item_id, item_name,
                location_id, location_name,
                indirect_target_id, indirect_target_name
            ) or "that topic"

            # Mutate the action in-place: INFORM -> TALK (ask about X)
            action_dict["action"] = "talk"
            action_dict["topic"] = f"asking about {raw_subject}"
            # keep the same receiver in 'target'; clear non-applicable fields
            action_dict["item"] = None
            action_dict["location"] = None
            action_dict["second target"] = None
            # Validation already confirmed the receiver is here, so allow
            return None

        # Otherwise, normal inform validation passes (receiver is here); execution will handle knowledge.
        return None

    elif action_identifier == 'equip_item':
        msg = validate_equip(item, item_id or item_name, actor=action_taker)
        return polite(msg) if msg else None

    elif action_identifier == 'unequip_item':
        msg = validate_unequip(item, item_id or item_name, actor=action_taker)
        return polite(msg) if msg else None

    elif action_identifier == 'pick_up':
        msg = validate_pickup(item, item_id or item_name, actor=action_taker)
        return polite(msg) if msg else None

    elif action_identifier == 'use_item':
        msg = validate_use(item, item_id or item_name, target, target_id or target_name, actor=action_taker)
        return polite(msg) if msg else None

    elif action_identifier == 'give_item':
        msg = validate_give(item, item_id or item_name, target, target_id or target_name, actor=action_taker)
        return polite(msg) if msg else None

    elif action_identifier == 'harm':
        msg = validate_harm(target, target_id or target_name, actor=action_taker)
        return polite(msg) if msg else None

    elif action_identifier == 'steal':
        msg = validate_steal(item, item_id or item_name, target, target_id or target_name, actor=action_taker)
        return polite(msg) if msg else None

    elif action_identifier == 'ask_action':
        # Asked character must be present and alive enough to be asked
        asked = target
        asked_id_or_name = target_id or target_name
        if asked is None and not asked_id_or_name:
            return "Please ask one specific character to do one specific thing."
        # Validate that the asked char is here (relative to the player/action_taker)
        here_check = explain_character(asked, asked_id_or_name, "person", actor=action_taker)
        if here_check:
            return polite(here_check)
        if requested_action is None or requested_action == "0" or requested_action == "":
            return "It is clear an action is requested, but not which one."

        # Now validate as if ASKED CHARACTER is the actor.
        actor = asked

        # *** SPECIAL: asked to INFORM about a subject that doesn't exist -> redirect to TALK ***
        if requested_action == 'inform':
            subject_ok = subject_exists_in_world(
                item_id_or_name=item_id or item_name,
                area_id_or_name=location_id or location_name,
                person_id_or_name=indirect_target_id or indirect_target_name
            )
            if not subject_ok:
                raw_subject = first_nonempty(
                    topic,
                    item_id, item_name,
                    location_id, location_name,
                    indirect_target_id, indirect_target_name
                ) or "that topic"
                # Mutate request to TALK and set topic
                action_dict["requested action"] = "talk"
                action_dict["topic"] = f"asking about {raw_subject}"
                # No need to require a indirect_target (default will be the asker)
                # Fall through to the 'talk' validation below (as actor=asked)
                return None  # Presence validated; execution will talk

        if requested_action == 'move':
            msg = validate_move(location, location_id or location_name, actor=actor)

        elif requested_action == 'talk':
            # They talk to someone; default receiver is the asker if second target missing
            msg = validate_talk(indirect_target, indirect_target_id or indirect_target_name or action_taker.uid, actor=actor)

        elif requested_action == 'search':
            # Either person (indirect_target) or area (location); default to area/current
            msg = validate_search(location or actor.current_area, location_id or location_name or getattr(actor.current_area, "uid", actor.current_area.name),
                                  indirect_target, indirect_target_id or indirect_target_name, actor=actor)

        elif requested_action == 'pick_up':
            msg = validate_pickup(item, item_id or item_name, actor=actor)

        elif requested_action == 'use_item':
            msg = validate_use(item, item_id or item_name, indirect_target, indirect_target_id or indirect_target_name, actor=actor)

        elif requested_action == 'give_item':
            # They give item to someone; default receiver is the asker
            msg = validate_give(item, item_id or item_name, indirect_target or action_taker, (indirect_target_id or indirect_target_name or action_taker.uid), actor=actor)

        elif requested_action == 'equip_item':
            msg = validate_equip(item, item_id or item_name, actor=actor)

        elif requested_action == 'unequip_item':
            msg = validate_unequip(item, item_id or item_name, actor=actor)

        elif requested_action == 'harm':
            # They harm indirect_target; default victim is the asker
            msg = validate_harm(indirect_target or action_taker, (indirect_target_id or indirect_target_name or action_taker.uid), actor=actor)

        elif requested_action == 'steal':
            # They steal item from indirect_target; default victim is the asker
            msg = validate_steal(item, item_id or item_name, indirect_target or action_taker, (indirect_target_id or indirect_target_name or action_taker.uid), actor=actor)

        elif requested_action in ('join_party', 'quit_party'):
            # Only require that the ask target is here (already checked) — party logic happens on execution.
            msg = None

        elif requested_action == 'stop_event':
            ce = event_manager.get_active_event_for_character(actor)
            msg = None if ce else f"{actor.name} has no ongoing event to stop"

        elif requested_action == 'drop_item':
            msg = validate_drop(item, item_id or item_name, actor=actor)

        else:
            return polite(f"{action_taker.name} doesn’t know how to ask someone to '{requested_action}'")

        return polite(msg) if msg else None

    elif action_identifier == 'do_nothing':
        return None

    elif action_identifier == 'stop_event':
        active_event = event_manager.get_active_event_for_character(action_taker)
        if not active_event:
            return polite("There’s no ongoing event to stop")
        return None

    elif action_identifier == 'join_party':
        msg = explain_character(target, target_id or target_name, "person", actor=action_taker)
        return polite(msg) if msg else None

    elif action_identifier == 'quit_party':
        msg = explain_character(target, target_id or target_name, "person", actor=action_taker)
        return polite(msg) if msg else None

    elif action_identifier == 'drop_item':
        msg = validate_drop(item, item_id or item_name, actor=action_taker)
        return polite(msg) if msg else None

    else:
        return polite("No action could be recognized.")


def validate_action_sequence(
    actions: Iterable[dict],
    event_manager: EventManager,
    action_taker: "gameRenderer.Character",
) -> Optional[str]:
    """
    Validate a whole chain of actions against a *phantom* version of the world.

    - Uses your existing validate_action(...) for each step.
    - After each valid action, simulates how it would change location/inventory/party.
    - Later actions are validated against this hypothetical state.
    - At the end (or on error), restores every touched character/area so the real
      game state is unchanged.

    Returns:
      * None  -> all actions are valid together
      * str   -> human-readable error describing why the sequence is invalid
    """
    Character = getattr(gameRenderer, "Character", None)
    if Character is None or not isinstance(action_taker, Character):
        # Fallback: no fancy phantom state, just check one by one.
        for idx, act in enumerate(actions, start=1):
            err = validate_action(act, event_manager, action_taker)
            if err:
                return f"Action {idx}: {err}"
        return None

    # Everything we touch goes through this snapshot so we can restore it later.
    snapshot: dict = {
        "characters": {},  # ch -> {"current_area": ..., "inventory": [...], "party": [...]}
        "areas": {},       # area -> {"key_items": [...]}
    }

    # ---------- Local helpers (not visible outside this function) ----------

    def snap_char(ch):
        """Remember a character's important fields the first time we touch them."""
        if ch is None:
            return
        chars = snapshot["characters"]
        if ch not in chars:
            chars[ch] = {
                "current_area": getattr(ch, "current_area", None),
                "inventory": list(getattr(ch, "inventory", []) or []),
                "party":     list(getattr(ch, "party", []) or []),
            }

    def snap_area(area):
        """Remember an area's floor items the first time we touch it."""
        if area is None:
            return
        areas = snapshot["areas"]
        if area not in areas:
            areas[area] = {
                "key_items": list(getattr(area, "key_items", []) or []),
            }

    def move_item_between_chars(itm, src, dst):
        """Phantom: src gives itm to dst."""
        if itm is None or src is None or dst is None:
            return
        if Character is not None:
            if not isinstance(src, Character) or not isinstance(dst, Character):
                return
        snap_char(src)
        snap_char(dst)
        inv_src = getattr(src, "inventory", None)
        inv_dst = getattr(dst, "inventory", None)
        if inv_src is None or inv_dst is None:
            return
        if itm in inv_src:
            try:
                inv_src.remove(itm)
            except ValueError:
                pass
        if itm not in inv_dst:
            inv_dst.append(itm)

    def move_item_floor_to_char(itm, ch):
        """Phantom: pick up itm from the floor into ch's inventory."""
        if itm is None or ch is None:
            return
        area = getattr(ch, "current_area", None)
        if area is None:
            return
        snap_char(ch)
        snap_area(area)
        floor = getattr(area, "key_items", None)
        inv = getattr(ch, "inventory", None)
        if floor is None or inv is None:
            return
        if itm in floor:
            try:
                floor.remove(itm)
            except ValueError:
                pass
        if itm not in inv:
            inv.append(itm)

    def move_item_char_to_floor(itm, ch):
        """Phantom: drop itm from ch onto the floor."""
        if itm is None or ch is None:
            return
        area = getattr(ch, "current_area", None)
        if area is None:
            return
        snap_char(ch)
        snap_area(area)
        floor = getattr(area, "key_items", None)
        inv = getattr(ch, "inventory", None)
        if floor is None or inv is None:
            return
        if itm in inv:
            try:
                inv.remove(itm)
            except ValueError:
                pass
        if itm not in floor:
            floor.append(itm)

    def apply_phantom_effect(act_dict: dict):
        """
        Approximate how this action would change the world for validation purposes.
        Only touches fields that we can easily restore: location, inventory, party,
        and area.key_items.
        """
        act = (act_dict.get("action") or "").strip().lower()
        req = (act_dict.get("requested action")
               or act_dict.get("requested_action") or "").strip().lower()

        target = act_dict.get("target")
        second = act_dict.get("second target") or act_dict.get("indirect_target")
        item = act_dict.get("item")
        location = act_dict.get("location")

        # ---- Direct player actions ----
        if act == "move":
            if location is None:
                return
            snap_char(action_taker)
            action_taker.current_area = location
            return

        if act == "pick_up":
            move_item_floor_to_char(item, action_taker)
            return

        if act == "drop_item":
            move_item_char_to_floor(item, action_taker)
            return

        if act == "give_item":
            # action_taker gives item to target
            if Character is not None and isinstance(target, Character):
                move_item_between_chars(item, action_taker, target)
            else:
                # At least reflect that the item leaves the actor's inventory
                snap_char(action_taker)
                inv = getattr(action_taker, "inventory", None)
                if inv is not None and item in inv:
                    try:
                        inv.remove(item)
                    except ValueError:
                        pass
            return

        if act == "steal":
            # action_taker steals from target
            if Character is not None and isinstance(target, Character):
                move_item_between_chars(item, target, action_taker)
            return

        if act == "join_party":
            if Character is not None and isinstance(target, Character):
                snap_char(action_taker)
                party = getattr(action_taker, "party", None)
                if party is not None and target not in party:
                    party.append(target)
            return

        if act == "quit_party":
            if Character is not None and isinstance(target, Character):
                snap_char(action_taker)
                party = getattr(action_taker, "party", None)
                if party is not None and target in party:
                    try:
                        party.remove(target)
                    except ValueError:
                        pass
            return

        # ---- ask_action: simulate requested_action for the asked character ----
        if act == "ask_action" and req:
            asked = target if (Character is not None and isinstance(target, Character)) else None
            if asked is None:
                return

            # Party membership mirrors activate_action() logic
            if req in ("join_party", "quit_party"):
                party_owner = second if (second is not None and hasattr(second, "party")) else action_taker
                if party_owner is None:
                    return
                snap_char(party_owner)
                party = getattr(party_owner, "party", None)
                if party is None:
                    return
                if req == "join_party":
                    if asked not in party:
                        party.append(asked)
                else:
                    if asked in party:
                        try:
                            party.remove(asked)
                        except ValueError:
                            pass
                return

            if req == "give_item":
                # asked gives item to receiver (defaults to the asker)
                receiver = second if (Character is not None and isinstance(second, Character)) else action_taker
                move_item_between_chars(item, asked, receiver)
                return

            if req == "steal":
                # asked steals item from victim (defaults to the asker)
                victim = second if (Character is not None and isinstance(second, Character)) else action_taker
                move_item_between_chars(item, victim, asked)
                return

            if req == "move":
                if location is not None:
                    snap_char(asked)
                    asked.current_area = location
                return

            if req == "pick_up":
                move_item_floor_to_char(item, asked)
                return

            if req == "drop_item":
                move_item_char_to_floor(item, asked)
                return

            # Other requested actions (harm, use_item, talk, etc.) are ignored
            # here because they don't affect the bits of state we care about.
            return

        # Everything else: no changes needed for validation purposes.
        return

    # ---------- Main loop + restore ----------

    try:
        for idx, act in enumerate(actions, start=1):
            # 1) Normal validation for this step
            error = validate_action(act, event_manager, action_taker)
            if error:
                return f"Action {idx}: {error}"

            # 2) If ok, update phantom state so the next step sees the effects
            apply_phantom_effect(act)

        return None  # whole sequence valid

    finally:
        # Restore all characters
        for ch, saved in snapshot["characters"].items():
            try:
                setattr(ch, "current_area", saved["current_area"])
            except Exception:
                pass
            try:
                ch.inventory = list(saved["inventory"])
            except Exception:
                pass
            try:
                ch.party = list(saved["party"])
            except Exception:
                pass

        # Restore all areas
        for area, saved in snapshot["areas"].items():
            try:
                area.key_items = list(saved["key_items"])
            except Exception:
                pass


def activate_action(action_dict: dict, event_manager: EventManager, action_taker: gameRenderer.Character) -> str:
    """
    Executes the action given a valid action_dict and returns a single response string.
    Assumes that validate_action has already been called.

    Updated: ask_action now delegates to ANY supported action consistently, building a
    proper sub-action for the asked character and executing it immediately.
    """
    action_identifier = action_dict.get("action")
    requested_action = action_dict.get("requested action")
    target = action_dict.get("target")
    indirect_target = action_dict.get("second target")
    item = action_dict.get("item")
    location = action_dict.get("location")
    topic = action_dict.get("topic")

    responses = []

    if action_identifier == 'move':
        responses.append(process_move_action(location, action_taker, event_manager))

    elif action_identifier == 'talk':
        if not target.is_alive:
            return f"{target.name} lies dead on the floor. They cannot respond."
        responses.append(
            process_talk_action(
                target=target,
                character=action_taker,
                topic=topic or "",
                event_manager=event_manager,
                about_item=item,
                about_area=location,
                about_person=indirect_target,
            )
        )

    elif action_identifier == 'examine':
        if item:
            responses.append(process_examine_action(item, action_taker))
        elif target:
            responses.append(process_examine_action(target, action_taker))
        elif location:
            responses.append(process_examine_action(location, action_taker))

    elif action_identifier == 'search':
        responses.append(process_search_action(action_taker, location=location, person=target))

    elif action_identifier == 'equip_item':
        responses.append(process_equip_item_action(item, action_taker))

    elif action_identifier == 'unequip_item':
        responses.append(process_unequip_item_action(item, action_taker))

    elif action_identifier == 'pick_up':
        responses.append(process_pick_up_item_action(item, action_taker))

    elif action_identifier == 'use_item':
        responses.append(process_use_item_action(item, action_taker, event_manager, target))

    elif action_identifier == 'give_item':
        if not target.is_alive:
            return f"{target.name} is dead. They can't accept any item."
        responses.append(process_give_item_action(item, target, action_taker))

    elif action_identifier == 'harm':
        responses.append(process_harm_action(target, action_taker, event_manager))

    elif action_identifier == 'ask_action':
        # --- Unified "ask someone to do X" handling ---
        asked_char = target
        if asked_char is None:
            return "There’s no one to ask."

        if not asked_char.is_alive:
            return f"{asked_char.name} is dead. They cannot be asked to do anything."

        if asked_char not in action_taker.current_area.characters:
            return f"{asked_char.name} is not here to ask."

        if not requested_action:
            return "It is clear an action is requested, but not which one."

        # Build a sub-action dict that will be executed by asked_char as the ACTOR.
        sub = {
            "action": requested_action,
            "requested action": None,
            "target": None,
            "second target": None,
            "item": None,
            "location": None,
            "topic": None,
        }

        # Pass through commonly used fields
        sub["item"] = item
        sub["location"] = location
        sub["topic"] = topic

        # Map semantics per requested action
        if requested_action == "move":
            # location already set
            pass

        elif requested_action == "talk":
            # asked_char talks to someone (default to the player if none provided)
            sub["target"] = indirect_target if isinstance(indirect_target, gameRenderer.Character) else action_taker

        elif requested_action == "search":
            # If a person was specified as 'second target', search that person; else search an area.
            if isinstance(indirect_target, gameRenderer.Character):
                sub["target"] = indirect_target
            else:
                # default to searching the area (if none provided, their current area)
                sub["location"] = location or asked_char.current_area

        elif requested_action == "pick_up":
            # convenience: if the item is in the current area, let the asked_char 'know' it so they can see it
            if item and (item in action_taker.current_area.key_items) and (asked_char not in getattr(item, "known_by", [])):
                item.known_by.append(asked_char)

        elif requested_action == "use_item":
            # If a secondary target exists, the asked_char uses the item on them
            if isinstance(indirect_target, gameRenderer.Character):
                sub["target"] = indirect_target

        elif requested_action == "give_item":
            # asked_char gives 'item' to recipient (default to player)
            sub["target"] = indirect_target if isinstance(indirect_target, gameRenderer.Character) else action_taker

        elif requested_action == "equip_item":
            # item already passed
            pass

        elif requested_action == "unequip_item":
            # item already passed
            pass

        elif requested_action == "harm":
            # target to harm; default to the player if absent
            sub["target"] = indirect_target if isinstance(indirect_target, gameRenderer.Character) else action_taker

        elif requested_action == "steal":
            # steal 'item' from someone; default victim = player
            sub["target"] = indirect_target if isinstance(indirect_target, gameRenderer.Character) else action_taker

        elif requested_action == "drop_item":
            # item already passed
            pass

        elif requested_action == "stop_event":
            # handled as sub-action with no args
            pass

        elif requested_action in ("join_party", "quit_party"):
            # Keep special routing so party ownership matches intent:
            party_owner = indirect_target if isinstance(indirect_target, gameRenderer.Character) else action_taker
            if requested_action == 'join_party':
                responses.append(process_join_party_action(asked_char, party_owner))
            else:
                responses.append(process_quit_party_action(asked_char, party_owner))
            # Check post-events and return
            event_response = event_manager.check_for_event_triggers_after_action(action_taker)
            if event_response:
                responses.append(f"\n{event_response}")
            valid_responses = [resp for resp in responses if resp]
            return "\n".join(valid_responses)

        else:
            # If we missed a future action, just pass through (no extra mapping needed)
            pass

        # Prefix line (consistently execute the requested action now)
        responses.append(f"{asked_char.name} agrees to {requested_action}.")
        responses.append(activate_action(sub, event_manager, asked_char))

    elif action_identifier == 'steal':
        responses.append(process_steal_action(item, target, action_taker))

    elif action_identifier == 'do_nothing':
        responses.append(process_do_nothing_action(action_taker))

    elif action_identifier == 'stop_event':
        active_event = event_manager.get_active_event_for_character(action_taker)
        responses.append(process_stop_event(target, action_taker, active_event))

    elif action_identifier == 'join_party':
        if not target.is_alive:
            return f"{target.name} is dead. They cannot join your party."
        responses.append(process_join_party_action(target, action_taker))

    elif action_identifier == 'quit_party':
        if not target.is_alive:
            return f"{target.name} is dead. No need to leave them, they already left."
        responses.append(process_quit_party_action(target, action_taker))

    elif action_identifier == 'drop_item':
        responses.append(process_drop_item_action(item, action_taker))

    # After-action event checks
    event_response = event_manager.check_for_event_triggers_after_action(action_taker)
    if event_response:
        responses.append(f"\n{event_response}")

    valid_responses = [resp for resp in responses if resp]
    return "\n".join(valid_responses)


def process_move_action(area: 'gameRenderer.SubArea', character: 'gameRenderer.Character', event_manager) -> str:
    """
    Move the initiator, automatically walking through intermediate areas if needed.

    - If the destination is adjacent and reachable, this behaves like before.
    - If the destination is farther away but connected by a chain of linked areas,
      we walk step-by-step along the shortest path.
    - If the destination is unknown / not connected, we return an error and DO NOT move.
    - Party members who started in the same room still get a queued 'move' to the
      final destination; their own moves will resolve a path in the same way.
    """
    # Lazy import to avoid circulars
    try:
        import turnHandler as TH
    except Exception:
        TH = None

    here = getattr(character, "current_area", None)

    # # --- Guards ---
    if not getattr(character, "is_alive", True):
        return f"{character.name} cannot move while dead."
    if here is None:
        return f"{character.name} is out of bounds and cannot move."
    if here is area:
        return f"{character.name} stays in {getattr(area, 'name', 'here')}."

    # --- Find a path (possibly multi-step) honouring blockades ---

    from collections import deque

    def _neighbors(a):
        try:
            linked = getattr(a, "get_linked_areas", lambda: [])()
        except Exception:
            linked = []
        return list(linked or [])

    path = None
    try:
        visited = set()
        queue = deque()
        visited.add(here)
        queue.append((here, [here]))
        while queue:
            node, cur_path = queue.popleft()
            if node is area:
                path = cur_path
                break
            for nb in _neighbors(node):
                if nb in visited:
                    continue
                # Respect movement constraints (blockades etc.)
                blocked = None
                try:
                    blocked = event_manager.validate_movement(node, nb)
                except Exception:
                    blocked = None
                if blocked:
                    continue
                visited.add(nb)
                new_path = cur_path + [nb]
                if nb is area:
                    path = new_path
                    queue.clear()
                    break
                queue.append((nb, new_path))
    except Exception:
        path = None

    if not path:
        # No way to reach the destination due to lack of knowledge
        return f"{getattr(area, 'name', 'there')} cannot be reached from {getattr(here, 'name', 'here')}, its whereabouts are not known."

    # --- Remember who started with the leader (for queued follow) ---
    starting_area = here
    party = list(getattr(character, "party", []) or [])
    allies_in_start = [
        m for m in party
        if m is not character
        and getattr(m, "is_alive", True)
        and getattr(m, "current_area", None) is starting_area
    ]

    # --- Walk the path step by step ---
    msg_parts = []
    current = here

    for dest in path[1:]:
        prev = current

        # Final safety check on each hop
        try:
            blocked = event_manager.validate_movement(prev, dest)
            if blocked:
                msg_parts.append(blocked)
                break
        except Exception:
            blocked = None

        # Move one step
        character.move_to(dest)

        # Optional knowledge/memory flavor (best-effort)
        try:
            character.remember(character.current_area, reason="presence")
            for c in character.current_area.characters:
                if c is not character:
                    character.remember(c, reason="co_presence")
        except Exception:
            pass

        step_msg = f"{character.name} moves from {getattr(prev, 'name', 'somewhere')} to {getattr(dest, 'name', 'somewhere')}."

        # On the final step only, show the area description for controllable chars
        if dest is area and getattr(character, "controllable", False):
            try:
                step_msg += f" {dest.description}"
            except Exception:
                pass

        # Let events react after each hop
        try:
            tail = event_manager.check_for_event_triggers_after_action(character)
            if tail:
                step_msg += f" {tail}"
        except Exception:
            pass

        msg_parts.append(step_msg)
        current = dest

    msg = " ".join(msg_parts) if msg_parts else f"{character.name} doesn't move."

    # --- Queue group follow for those who STARTED with the leader ---
    final_dest = character.current_area
    if TH is not None and allies_in_start:
        mapping = {}
        for ally in allies_in_start:
            # Skip if ally is already in destination (safety)
            if getattr(ally, "current_area", None) is final_dest:
                continue
            mapping[ally] = {
                "action": "move",
                "requested action": "0",
                "target": None,
                "second target": None,
                "item": None,
                "location": final_dest,  # bind SubArea so the handler doesn't need to resolve tokens
                "topic": "0",
                "location_id": getattr(final_dest, "uid", None),
                "location_name": getattr(final_dest, "name", None),
            }
        if mapping:
            try:
                TH.queue_controller_actions(mapping, origin="group-move")
                # Optional: add a tiny info line so the story reflects queued followers
                names = ", ".join(a.name for a in mapping.keys())
                if names:
                    msg += f" {names} move with {character.name}."
            except Exception:
                # Best-effort queuing; don't break the leader's move text
                pass

    return msg


def process_talk_action(
    target: gameRenderer.Character,
    character: gameRenderer.Character,
    topic: str,
    event_manager: EventManager,
    *,
    about_item: Optional[gameRenderer.Item] = None,
    about_area: Optional[gameRenderer.SubArea] = None,
    about_person: Optional[gameRenderer.Character] = None,
    force_inform: bool = False,
) -> str:
    """
    Unified TALK / INFORM handler.

    - If we have a concrete subject (item / area / person), behave like the old
      process_inform_action: copy / align knowledge between speaker and listener.
    - If we *don't* have a subject (no item/person/location resolved), behave like
      the old process_talk_action (simple one-sided talk).
      Additionally, when the *player* is the speaker and there is no subject,
      append:
          "The player is talking about a topic not related to any item,
           location or person in this area."
    """
    # ------------------------------------------------------------------
    # 1. Decide if we have a "subject" (for inform-style behaviour)
    # ------------------------------------------------------------------
    subject = about_item or about_area or about_person

    # ------------------------------------------------------------------
    # 2. NO subject => plain talk + your extra message
    # ------------------------------------------------------------------
    if subject is None and not force_inform:
        # Player talks to NPC
        if character.controllable:
            base = f"{character.name} talks to {target.name}"
            if topic and topic != "0":
                base += f" about '{topic}'."
            else:
                base += " about the indicated topic. Write a dialogue between the characters."

            # Your requested extra message when *no* item/person/location is involved
            base += f"{character.name} is talking about a topic not related to any item, location or person in this area."
            return base

        # NPC talks to player (no auto-reply)
        if target.controllable:
            base = f"{character.name} talks to {target.name}"
            if topic and topic != "0":
                base += f" about '{topic}'."
            else:
                base += " about something. Write a dialogue between the characters."
            return base

        # NPC ↔ NPC chatter (only narrate if the player is present)
        if character.current_area == gameSetup.player.current_area:
            if topic and topic != "0":
                return f"{character.name} and {target.name} talk about '{topic}'."
            return f"{character.name} and {target.name} exchange a few words."

        # Off-screen NPC chatter: omit
        return ""

    # ------------------------------------------------------------------
    # 3. We DO have a subject ⇒ behave like the old process_inform_action
    # ------------------------------------------------------------------
    giver = character
    receiver = target

    # Presence rule: receiver must be co-present (same area) or in giver's party.
    if receiver not in getattr(giver.current_area, "characters", []) and receiver not in getattr(giver, "party", []):
        return f"{receiver.name} isn’t here to tell."

    # Decode subject type
    if isinstance(subject, gameRenderer.Item):
        ent_type = "item"
    elif isinstance(subject, gameRenderer.SubArea):
        ent_type = "area"
    elif isinstance(subject, gameRenderer.Character):
        ent_type = "character"
    else:
        ent_type = type(subject).__name__

    uid = getattr(subject, "uid", None) or f"unknown_{id(subject)}"
    display = getattr(subject, "name", ent_type)

    # ---- nested helpers (scoped to this function only) ----
    def has_truth_view(observer: gameRenderer.Character, entity) -> bool:
        """Does observer currently see the entity's true state?"""
        if isinstance(entity, gameRenderer.Item):
            # On floor here?
            if entity in getattr(observer.current_area, "key_items", []):
                return True
            # In someone's inventory here?
            for c in getattr(observer.current_area, "characters", []):
                if entity in getattr(c, "inventory", []):
                    return True
            # In party member's inventory?
            for p in getattr(observer, "party", []):
                if entity in getattr(p, "inventory", []):
                    return True
            # In own inventory?
            return entity in getattr(observer, "inventory", [])
        elif isinstance(entity, gameRenderer.Character):
            return (
                entity is observer
                or entity in getattr(observer.current_area, "characters", [])
                or entity in getattr(observer, "party", [])
            )
        elif isinstance(entity, gameRenderer.SubArea):
            return entity is getattr(observer, "current_area", None)
        return False

    def truth_snapshot_for(entity, observer: gameRenderer.Character) -> dict:
        """Build a 'truth' snapshot using the same schema as Character.remember()."""
        if isinstance(entity, gameRenderer.Item):
            return observer._snapshot_item(entity)
        elif isinstance(entity, gameRenderer.Character):
            return observer._snapshot_character(entity)
        elif isinstance(entity, gameRenderer.SubArea):
            return observer._snapshot_area(entity)
        else:
            return {
                "uid": getattr(entity, "uid", f"unknown_{id(entity)}"),
                "name": getattr(entity, "name", type(entity).__name__),
                "repr": repr(entity),
            }

    def apply_snapshot(who: gameRenderer.Character, snap: dict, *, reason: str, outdated: Optional[bool] = None):
        entry = {
            "entity_type": ent_type,
            "uid": uid,
            "name": display,
            "reason": reason,
            "snapshot": snap,
        }
        if outdated is not None:
            entry["is_outdated"] = bool(outdated)
        who.knowledge[uid] = entry

    # ---- gather current knowledge / truth alignment ----
    giver_entry = giver.get_known(uid)
    giver_has_truth = has_truth_view(giver, subject)

    if giver_entry is None and not giver_has_truth:
        # This mirrors the original process_inform_action behaviour.
        return f"{giver.name} doesn’t really know enough about {display} to explain it."

    receiver_entry = receiver.get_known(uid)
    receiver_has_truth = has_truth_view(receiver, subject)

    # Actual truth (as of now)
    truth = truth_snapshot_for(subject, giver)

    # Giver's notion (snapshot we would paste if not using truth)
    giver_snapshot = giver_entry["snapshot"] if giver_entry else (truth if giver_has_truth else None)

    giver_matches_truth = (giver_snapshot == truth) or giver_has_truth
    receiver_matches_truth = ((receiver_entry and receiver_entry.get("snapshot") == truth) or receiver_has_truth)

    # ---- truth wins: align both sides to truth, clear outdated ----
    if giver_matches_truth or receiver_matches_truth:
        apply_snapshot(giver, truth, reason=f"inform_truth:{receiver.uid}", outdated=False)
        apply_snapshot(receiver, truth, reason=f"informed_by:{giver.uid}", outdated=False)

        # maintain subject.known_by (generic)
        if not hasattr(subject, "known_by"):
            setattr(subject, "known_by", [])
        for ch in (giver, receiver):
            if ch not in subject.known_by:
                subject.known_by.append(ch)

        if giver_matches_truth and not receiver_matches_truth:
            return (
                f"{giver.name} shares details about {display}. "
                f"They’re up-to-date, so {receiver.name} updates their understanding."
            )
        elif receiver_matches_truth and not giver_matches_truth:
            return (
                f"{giver.name} shares details about {display}, but {receiver.name} "
                f"cross-checks what’s in front of them and corrects it. "
                f"Both now align on the current facts."
            )
        else:
            return f"{giver.name} and {receiver.name} synchronize on accurate information about {display}."

    # ---- neither side has truth: paste giver snapshot & mark both outdated ----
    if giver_snapshot is None:
        # Safety fallback; normally unreachable due to early check
        return f"{giver.name} lacks any reliable information about {display}."

    apply_snapshot(receiver, giver_snapshot, reason=f"informed_by:{giver.uid}", outdated=True)

    if giver_entry is None:
        apply_snapshot(giver, giver_snapshot, reason="inform_created", outdated=True)
    else:
        tagged = dict(giver_entry)
        tagged["is_outdated"] = True
        giver.knowledge[uid] = tagged

    if not hasattr(subject, "known_by"):
        setattr(subject, "known_by", [])
    for ch in (giver, receiver):
        if ch not in subject.known_by:
            subject.known_by.append(ch)

    giver_flagged = (giver_entry and giver_entry.get("is_outdated"))
    if giver_flagged:
        return (
            f"{giver.name} shares what they know about {display}, but admits it’s probably outdated. "
            f"Both of them mark this info as out-of-date."
        )
    else:
        return (
            f"{giver.name} explains what they know about {display}. Comparing notes, "
            f"they both realize the information isn’t current — it’s marked as outdated."
        )


def process_examine_action(target, character: gameRenderer.Character) -> str:
    """
    Process the character's attempt to examine something.

    Args:
        target (Item | Character | SubArea): The target to examine (object).
        character (Character): The character who is examining.

    Returns:
        str: The result of the action.
    """
    # Examine the current area (if target is that area)
    if isinstance(target, gameRenderer.SubArea) and target == character.current_area:
        if character.controllable:
            # Detailed Area Description
            area_description = f"{character.name} examines the area: {character.current_area.description}."

            # Reveal items in the area
            found_items = []
            for itm in character.current_area.key_items:
                if character not in itm.known_by:
                    itm.known_by.append(character)
                found_items.append(
                    f"{itm.name} (Robustness: {itm.robustness}, Damage: {itm.damage}"
                )

            if found_items:
                item_text = f"\{character.name} sees the following items lying around: {', '.join(found_items)}."
            else:
                item_text = "\nThere are no items here."

            # List characters in the area (excluding the examiner)
            found_characters = [
                c.name for c in character.current_area.characters if c != character
            ]
            if found_characters:
                character_text = f"\{character.name} sees the following people: {', '.join(found_characters)}."
            else:
                character_text = "\nThere are no people here."

            # Retrieve linking points and their status
            linking_text = "\nLinking Points to Adjacent Areas:"
            for link in character.current_area.linking_points:
                other_area = link.get_other_area(character.current_area)
                # Check for active BlockadeEvent on this linking point
                blockade = None
                for event in (character.current_area.active_events + other_area.active_events):
                    if (
                        isinstance(event, BlockadeEvent)
                        and event.linking_point == link
                        and event.is_active
                    ):
                        blockade = event
                        break
                if blockade:
                    linking_text += (
                        f"\n- To {other_area.name}: {link.description} "
                        f"[Barricaded, requires {blockade.required_item} to remove]"
                    )
                else:
                    linking_text += (
                        f"\n- To {other_area.name}: {link.description} [Open]"
                    )

            return f"{area_description}{item_text}{character_text}{linking_text}"
        else:
            # NPC examining area
            return f"{character.name} examines the area."

    # Examine the character themselves
    if isinstance(target, gameRenderer.Character) and target == character:
        if character.controllable:
            return f"{character.name} examines themself. Health: {character.health}, Inventory: {character.get_inventory_descriptions}, Name: {character.name}, Description: {character.description}"
        else:
            return f"{character.name} reflects on themselves."

    # Examine another character
    if isinstance(target, gameRenderer.Character) and target != character:
        target_character = target
        if character.controllable:
            # Friendship level with player
            friendship_level = target_character.friendship_with(character)
            friendship_text = f" Friendship with {character.name}: {friendship_level}/10."

            # Health of the character
            health_text = f" Health: {target_character.health}."

            # If friendship level allows, show inventory
            if friendship_level >= 5 or target_character in character.party:
                if target_character.inventory:
                    items_text = f" They are carrying: {target.get_inventory_descriptions}."
                    # Mark items as known
                    for itm in target_character.inventory:
                        if character not in itm.known_by:
                            itm.known_by.append(character)
                else:
                    items_text = " They are not carrying any items."
            else:
                items_text = "They seem to be hiding something, or maybe just holding back."

            return (
                f"{character.name} examines {target_character.name}."
                f"{health_text}{friendship_text}{items_text}"
                f"They are {target.description}."
                f"Personality: Openness {target.openness}, Conscientiousness {target.conscientiousness}, Extraversion {target.extraversion}, "
                f"Agreeableness {target.agreeableness}. Neuroticism {target.neuroticism}."
            )
        else:
            # NPC examining another character
            return f"{character.name} looks at {target_character.name}."

    # Examine an item
    if isinstance(target, gameRenderer.Item):
        target_item = target
        if character.controllable:
            return (
                f"{character.name} examines {target_item.name}: {target_item.description} "
                f"(Robustness: {target_item.robustness}, Damage: {target_item.damage})."
            )
        else:
            return f"{character.name} looks at {target_item.name}."

    # Examine an adjacent area
    if (
        isinstance(target, gameRenderer.SubArea)
        and target in character.current_area.get_linked_areas()
    ):
        if character.controllable:
            # Get number of people in the adjacent area
            num_people = len(target.characters)
            # Get active events in the adjacent area
            active_events = [ev.name for ev in target.active_events if ev.is_active]
            if active_events:
                events_text = f" Active events: {', '.join(active_events)}."
            else:
                events_text = " No active events."

            return (
                f"{character.name} look towards {target.name}: {target.description} "
                f"There are {num_people} people here.{events_text}"
            )
        else:
            # NPC examining adjacent area
            return f"{character.name} looks towards {target.name}."

    # If target not found or invalid
    return f"{character.name} can't examine '{getattr(target, 'name', target)}' because they/it are not here."


def process_pick_up_item_action(item: gameRenderer.Item, character: gameRenderer.Character) -> str:
    """
    Character picks up an item if it is present and known.
    Also: party members learn about the item immediately.
    """
    if item in character.current_area.key_items and character in item.known_by:
        character.add_item(item)
        character.current_area.key_items.remove(item)

        try:
            process_equip_item_action(item, character)
        except Exception:
            pass

        # Party knowledge propagation: everyone in your party now knows this item
        for mate in character.party:
            mate.remember(item, reason="party_member_pickup")
        return f"{character.name} picks up {item.name} and equips it in their hand."
    else:
        return f"{character.name} doesn't see {item.name} here."


def process_use_item_action(item, actor, event_manager, target=None) -> str:
    """
    Use an item that has already been resolved by the caller (activate_action).
    Also supports using the item on a target Character (e.g., healing), and
    still lets area events (e.g., barricades) react first.

    Args:
        item: Item instance (preferred). If not an Item, we'll try to resolve by name.
        actor: Character performing the use.
        event_manager: EventManager to notify area events.
        target: Optional Character to use the item on (healing, etc.).
    """
    # --- Resolve 'item' robustly in case a string/name slipped through ---
    try:
        from gameRenderer import Item as GRItem, Character as GRChar
    except Exception:
        GRItem = None
        GRChar = None

    it = None
    if GRItem is not None and isinstance(item, GRItem):
        it = item
    else:
        # Try to resolve by name (string or object with .name)
        name = (getattr(item, "name", None) or str(item) or "").strip().lower()
        if not name:
            return f"{getattr(actor, 'name', 'Someone')} can't find that item to use."
        # Inventory first
        for itx in getattr(actor, "inventory", []) or []:
            if getattr(itx, "name", "").lower() == name:
                it = itx
                break
        # Area pools if not found in inventory
        if it is None:
            area = getattr(actor, "current_area", None)
            if area:
                for pool in ("items", "key_items"):
                    for itx in getattr(area, pool, []) or []:
                        if getattr(itx, "name", "").lower() == name:
                            it = itx
                            break
                    if it is not None:
                        break

    if it is None:
        return f"{getattr(actor, 'name', 'Someone')} can't find that item to use."

    # --- 1) Let active events in the area react first (e.g., barricade) ---
    try:
        msg = event_manager.handle_item_use(actor, it) or ""
        if msg:
            return msg
    except Exception:
        # If events error out, fall through to local handling
        pass

    # --- 2) Targeted use (e.g., healing) if target is a Character ---
    if GRChar is not None and target is not None and isinstance(target, GRChar):
        if not getattr(target, "is_alive", True):
            return f"{target.name} cannot benefit from that."

        # Heuristic: treat items with a 'Medicate' ability as healing kits
        def _has_ability(itm, abil_name: str) -> bool:
            for ab in getattr(itm, "abilities", []) or []:
                if (getattr(ab, "name", "") or "").lower() == abil_name.lower():
                    return True
            return False

        if _has_ability(it, "Medicate"):
            HEAL = 30
            try:
                target.update_health(+HEAL)
            except Exception:
                try:
                    target.health = min(100, int(getattr(target, "health", 0)) + HEAL)
                except Exception:
                    pass
            return f"{actor.name} uses {getattr(it, 'name', 'an item')} on {target.name}, restoring {HEAL} health."

        # No special targeted effect known; narrate generic targeted use
        return f"{actor.name} uses {getattr(it, 'name', 'an item')} on {target.name}."

    # --- 3) Generic non-targeted use if nothing else handled it ---
    return f"{actor.name} uses {getattr(it, 'name', 'an item')}."


def process_give_item_action(
    item: gameRenderer.Item,
    target: gameRenderer.Character,
    character: gameRenderer.Character
) -> str:
    """
    Process the character's attempt to give an item to another character.

    Args:
        item (Item): The item to be given.
        target (Character): The character receiving the item.
        character (Character): The character giving the item.

    Returns:
        str: The result of the action.
    """
    # Check if the character has the item
    if item not in character.inventory:
        return f"{character.name} does not have {item.name} to give."

    # Check if the target character will accept the item
    friendship_level = target.friendship_with(character)
    if friendship_level >= 3:
        # Remove the item from the giver
        character.remove_item(item)

        # Add the item to the recipient
        target.add_item(item)

        try:
            process_equip_item_action(item, target)
        except Exception:
            print(Exception)

        # Increase friendship
        target.update_friendship_with(character, 1)

        return f"{character.name} gives {item.name} to {target.name}, they accept it happily."
    elif friendship_level < 3 and target.health <= 40:
        # Target takes it out of necessity

        # Remove the item from the giver
        character.remove_item(item)

        # Add the item to the recipient
        target.add_item(item)
        return f"{target.name} accepts {item.name} spitefully from {character.name}."
    else:
        # Target refuses the item
        return f"{target.name} refuses to accept {item.name} from {character.name}."


def process_join_party_action(
    target: gameRenderer.Character,
    character: gameRenderer.Character
) -> str:
    """
    Invite another character to join your party.
    Knowledge: after joining, every party member learns about the new member AND their items;
               the new member learns about all existing members AND their items, even for large groups.
    """
    # Make sure the target is actually in the same area
    if target not in character.current_area.characters:
        return f"{character.name} cannot join {target.name} because they are not here."

    # Too injured characters cannot move and also cannot join party
    if target.health <= 30 or character.health <= 30:
        return f"They are too injured to move and cannot come with."

    # Check friendship level
    friendship = target.friendship_with(character)
    if friendship >= 5:
        character.add_party_member(target)

        # --- Knowledge propagation for big groups ---
        # All current members (including the inviter) learn about the newcomer and their items.
        current_members = [character] + [m for m in character.party if m is not target]
        for m in current_members:
            m.remember(target, reason="party_join")
            for it in getattr(target, "inventory", []):
                m.remember(it, reason="party_item")

        # The newcomer learns about everyone already in the party + their items.
        for m in current_members:
            target.remember(m, reason="party_join")
            for it in getattr(m, "inventory", []):
                target.remember(it, reason="party_item")

        return (f"{target.name} has {'happily ' if friendship >= 7 else ''}joined {character.name}'s party!")
    else:
        return f"{target.name} refuses to join {character.name}'s party."


def process_quit_party_action(
    target: gameRenderer.Character,
    character: gameRenderer.Character
) -> str:
    """
    Process the character's attempt to remove a member from their party.

    Args:
        target (Character): The party member to remove.
        character (Character): The character performing the action.

    Returns:
        str: The result of the action.
    """
    # Check if the character is in a party
    if not character.party:
        return f"{character.name} is not in a party."

    # Make sure the target is actually in the party
    if target not in character.party:
        return f"{character.name} cannot find {target.name} in their party."

    # Remove the target from the party
    character.remove_party_member(target)

    # Provide feedback
    return f"{character.name} has removed {target.name} from their party."


def process_stop_event(
    target,
    character: gameRenderer.Character,
    current_event
) -> str:
    """
    Process the character's attempt to stop an event, such as a fight or a conversation.

    Args:
        target (Character or None): The target associated with stopping the event
                                    (not always used directly).
        character (Character): The character attempting to stop the event.
        current_event: Which event is attempting to be stopped.
    Returns:
        str: The result of the action.
    """
    # If it's a FightEvent, attempt to stop it
    if isinstance(current_event, FightEvent):
        return current_event.attempt_stop_fight(character)
    elif current_event.name == "Conversation":
        # If it's a ConversationEvent, attempt to stop it
        return current_event.handle_action('stop_event', [], character)
    else:
        return "This event cannot be stopped."


def process_harm_action(
    target: gameRenderer.Character,
    character: gameRenderer.Character,
    event_manager
) -> str:
    """
    Harm with group cascade (implemented in the action processor):

    • Attacker strikes once (no defender auto-retaliation).
    • Witnesses apply friendship-only penalties scaled by damage/kill (no actions).
    • Ensure a (non-blocking) FightEvent exists.
    • Group cascade: attacker's alive party members in the SAME AREA are assigned a harm action
      against a RANDOM defender in the victim's group (or the victim if solo). We inject/overwrite
      their queued step for this round via turnHandler, so speed/interrupt rules still apply.

    Returns a narration string for the attack (ally join messages are appended).
    """
    import random
    # Lazy import to avoid circulars and to let this run inside TurnHandler safely.
    try:
        import turnHandler as TH
    except Exception:
        TH = None  # If TH is unavailable, we will still execute the main strike.

    # ---------- tiny local helpers ----------
    def equipped_weapon(who: gameRenderer.Character):
        eq = getattr(who, "equipment", {}) or {}
        for slot in ("hand_right", "hand_left"):
            itm = eq.get(slot)
            if itm:
                return itm
        return getattr(who, "weapon", None)

    def compute_damage(attacker: gameRenderer.Character) -> int:
        """
        Simple, self-contained damage model:
          base = weapon.damage (or 5 if none)
          scale by (strength + skill) ~= 1.0x..2.0x
        """
        wpn = equipped_weapon(attacker)
        base = int(getattr(wpn, "damage", 0)) or 5
        strv = int(getattr(attacker, "strength", 5))
        skl  = int(getattr(attacker, "skill", 5))
        scaled = int(round(base * (1.0 + (strv + skl) / 20.0)))
        return max(1, scaled)

    def ensure_fight_event(loc, participants):
        from gameEvents import FightEvent
        existing = next((ev for ev in event_manager.active_events
                         if isinstance(ev, FightEvent) and ev.is_active and ev.location is loc), None)
        if existing:
            for p in participants:
                if p not in existing.participants and getattr(p, "is_alive", True):
                    existing.participants.append(p)
            if existing not in loc.active_events:
                loc.active_events.append(existing)
            return ""
        fe = FightEvent(loc, [p for p in participants if getattr(p, "is_alive", True)])
        event_manager.active_events.append(fe)
        loc.active_events.append(fe)
        return fe.description

    def present_alive_in_area(members, area):
        return [
            m for m in (members or [])
            if getattr(m, "is_alive", True) and getattr(m, "current_area", None) is area
        ]

    def group_for(ch: gameRenderer.Character):
        """Actor's 'group' = themselves + alive party in same area."""
        area = getattr(ch, "current_area", None)
        party_here = present_alive_in_area(getattr(ch, "party", []) or [], area)
        # keep order: self first, then party members
        return [ch] + [m for m in party_here if m is not ch]

    def make_bound_harm_step(attacker: gameRenderer.Character, defender: gameRenderer.Character) -> dict:
        return {
            "action": "harm",
            "requested action": "0",
            "target": defender,
            "second target": None,
            "item": None,
            "location": None,
            "topic": "0",
            "target_id": getattr(defender, "uid", None),
            "target_name": getattr(defender, "name", None),
        }

    # ---------- presence & sanity ----------
    here = character.current_area
    if here is None or target not in getattr(here, "characters", []):
        return f"{target.name} is not here."
    if not getattr(character, "is_alive", True):
        return f"{character.name} cannot attack while dead."
    if not getattr(target, "is_alive", True):
        return f"{target.name} is already dead."

    # ---------- the primary strike ----------
    dmg = compute_damage(character)
    target.update_health(-dmg)
    killed = target.health <= 0

    # Weapon label for flavor
    wpn = equipped_weapon(character)
    wname = getattr(wpn, "name", None) or "bare hands"

    # Victim becomes hostile to attacker (friendship -> 0) but does NOT counterattack
    try:
        cur = target.friendship_with(character)
        if cur > 0:
            target.update_friendship_with(character, -cur)
    except Exception:
        pass

    # Witness penalties only (no actions triggered)
    try:
        wit = gameRenderer.Character.witness_violence(
            perpetrator=character, victim=target, action="harm", damage=dmg, killed=killed
        )
    except TypeError:
        # Backward-compatible call if older 3-arg signature
        wit = gameRenderer.Character.witness_violence(character, target, "harm")
    except Exception:
        wit = ""

    # Death bookkeeping
    death_msg = ""
    if killed:
        target.is_alive = False
        target.health = 0
        try:
            target.party.clear()
        except Exception:
            pass
        here.description += f" {target.name} has died here."
        death_msg = f" {target.name} has died here."

    # Ensure a (non-blocking) fight event exists for the location
    ensure_fight_event(here, {character, target})

    # ---------- GROUP CASCADE (implemented here) ----------
    # Allies of the attacker (in same area, alive) will join against a random defender.
    join_msgs = []
    try:
        attackers = group_for(character)                      # [attacker, ally1, ally2...]
        defenders = group_for(target) if getattr(target, "party", None) else [target]
        defenders = [d for d in defenders if getattr(d, "is_alive", True)]

        # Always have at least the target
        if not defenders:
            defenders = [target]

        # Skip primary attacker; assign/overwrite ally steps via TurnHandler if available
        allies = [a for a in attackers if a is not character and getattr(a, "is_alive", True)
                  and getattr(a, "current_area", None) is here]

        if allies and TH is not None:
            mapping = {}
            for ally in allies:
                tgt = random.choice(defenders)
                step = make_bound_harm_step(ally, tgt)
                mapping[ally] = step
                join_msgs.append(f"{ally.name} joins the attack against {tgt.name}.")
            # Overwrite/inject for this round so speed/interrupt logic still applies
            TH.queue_controller_actions(mapping, origin="group-join")
    except Exception:
        # Group cascade is best-effort; don't break the primary attack if anything goes wrong
        pass

    # ---------- compose message ----------
    msg = f"{target.name} sustains {dmg} damage from {character.name} using {wname}."
    if wit:
        msg += f" {wit}"
    msg += death_msg
    if join_msgs:
        msg += " " + " ".join(join_msgs)

    return msg


def process_search_action(actor: gameRenderer.Character, *,
                          location: Optional[gameRenderer.SubArea] = None,
                          person: Optional[gameRenderer.Character] = None) -> str:
    """
    Search either the current/adjacent area (like old investigate) or a person's belongings (invasive).

    Knowledge handling:
      • Searching a PERSON: actor.remember(person) (full), reveal their inventory (mark items known + remember),
        and apply the existing friendship penalty.
      • Searching CURRENT AREA: actor.remember(area) (truth), remember every character there (truth),
        and remember all floor items (mark known_by).
      • Searching ADJACENT AREA: actor.remember(adj_area) and remember every character seen there,
        but tag those knowledge entries as is_outdated=True (peek, not co-present).

    Returns a descriptive string as before.
    """
    # --- PERSON SEARCH ---
    if person is not None:
        if person not in actor.current_area.characters and person not in actor.party:
            return f"{person.name} isn’t here to search."

        # Snapshot the person (truth: co-present or party)
        actor.remember(person, reason="search_person")

        inv = list(getattr(person, "inventory", []))
        # Mark items as known and remembered
        for itm in inv:
            if actor not in getattr(itm, "known_by", []):
                itm.known_by.append(actor)
            actor.remember(itm, reason="search_person_inventory")

        # Apply friendship penalty for invasive search (if alive)
        if getattr(person, "is_alive", True):
            old = person.friendship_with(actor)
            person.update_friendship_with(actor, -1)
            new = person.friendship_with(actor)
            parting_message = f"(their opinion of {actor.name} drops: {old} → {new})."
        else:
            parting_message = f"({actor.name} searched a corpse.)"

        # Equipped summary
        eq = getattr(person, "equipment", {})
        equipped_list = []
        if isinstance(eq, dict):
            for slot, itm in eq.items():
                if itm and getattr(itm, "is_equipped", False):
                    equipped_list.append(f"{slot}: {itm.name}")

        inv_names = ", ".join([i.name for i in inv]) if inv else "nothing"
        eq_text = "; their equipment consists of" + (": ".join(equipped_list) if equipped_list else "none")

        return (f"{actor.name} searches {person.name}. {actor.name} finds: {inv_names}. "
                f"Health: {person.health}. {eq_text} "
                f"{parting_message}")

    # --- AREA SEARCH ---
    area = location or actor.current_area

    # Search the current area (truthful, co-present)
    if area is actor.current_area:
        # Remember the area
        actor.remember(actor.current_area, reason="search_area")

        # Reveal & remember items on the floor
        found_items = []
        for itm in actor.current_area.key_items:
            if actor not in getattr(itm, "known_by", []):
                itm.known_by.append(actor)
            actor.remember(itm, reason="search_area_item")
            found_items.append(f"{itm.name} (Robustness: {itm.robustness}, Damage: {itm.damage})")

        # Remember every other character in the room (truth)
        others = [c for c in actor.current_area.characters if c is not actor]
        for c in others:
            actor.remember(c, reason="search_area_presence")

        # Compose description (kept close to your original)
        area_description = f"{actor.name} searches the area: {actor.current_area.description}."
        item_text = f"\{actor.name} uncovers the following items: {', '.join(found_items)}." if found_items else f" {actor.name} doesn't discover any additional items."
        character_text = f"\nNearby people: {', '.join([c.name for c in others])}." if others else "\nThere are no other people here."

        return f"{area_description}{item_text}{character_text}"

    # Quick look into an adjacent area (not co-present)
    if area in actor.current_area.get_linked_areas():
        # Remember the adjacent area as a peek and tag as outdated (not co-present)
        entry = actor.remember(area, reason="search_adjacent_area")
        if entry:
            entry["is_outdated"] = True
            actor.knowledge[area.uid] = entry

        # **NEW**: add people in the adjacent room to the knowledge base
        # Mark as is_outdated=True because this is a peek (may drift from truth)
        for c in getattr(area, "characters", []):
            centry = actor.remember(c, reason="search_adjacent_seen")
            if centry:
                centry["is_outdated"] = True
                actor.knowledge[c.uid] = centry

        num_people = len(area.characters)
        return f"{actor.name} peers into {area.name}: {area.description} There are {num_people} people there."

    return f"{actor.name} can’t really search {getattr(area, 'name', 'that place')} from here since it's not an adjacant area."


def process_equip_item_action(item: gameRenderer.Item, character: gameRenderer.Character) -> str:
    """
    Equip an item already in inventory:
      - If item has 'equip_slot', use it (head/torso/legs/hand_left/hand_right/extra).
      - If weapon-like (damage>0) and no slot given, use a free hand (right then left).
      - Else put into 'extra'.
    Uses Character.equip(...) if available; otherwise manipulates equipment dict.
    """
    if item not in character.inventory:
        return f"{character.name} does not have {item.name} to equip."

    # Prefer character.equip if provided by your Character class
    if hasattr(character, "equip"):
        try:
            out = character.equip(item)
            # If your equip() returns a message, surface it; else print a generic one
            if isinstance(out, str) and out.strip():
                return out
            # Generic success if no message returned
            item.is_equipped = True
            slot = getattr(item, "equip_slot", None) or ("hand_right" if getattr(item, "damage", 0) > 0 else "extra")
            return f"{character.name} equips {item.name} ({slot})."
        except Exception:
            # fall through to manual
            pass

    # Manual handling
    equip_slot = getattr(item, "equip_slot", None)
    equipment = getattr(character, "equipment", None)
    if equipment is None or not isinstance(equipment, dict):
        # initialize a basic equipment dict if missing
        character.equipment = {"head": None, "torso": None, "legs": None, "hand_left": None, "hand_right": None, "extra": None}
        equipment = character.equipment

    def place(slot_name: str):
        if equipment.get(slot_name):
            # unequip current occupant
            cur = equipment[slot_name]
            if cur:
                cur.is_equipped = False
        equipment[slot_name] = item
        item.is_equipped = True

    if equip_slot in ("head", "torso", "legs", "hand_left", "hand_right", "extra"):
        place(equip_slot)
        if getattr(item, "damage", 0) > 0:
            character.weapon = item
        return f"{character.name} equips {item.name} ({equip_slot})."

    # Weapon-like? choose a hand
    if getattr(item, "damage", 0) > 0:
        hand = "hand_right" if not equipment.get("hand_right") else ("hand_left" if not equipment.get("hand_left") else "hand_right")
        place(hand)
        character.weapon = item
        return f"{character.name} equips {item.name} ({hand})."

    # Otherwise go to 'extra'
    place("extra")
    return f"{character.name} equips {item.name} (extra)."


def process_unequip_item_action(item: gameRenderer.Item, character: gameRenderer.Character) -> str:
    """
    Unequip an equipped item; stays in inventory.
    Uses Character.unequip(...) if available; otherwise clears from equipment dict.
    """
    if not getattr(item, "is_equipped", False):
        return f"{item.name} isn’t equipped."

    if hasattr(character, "unequip"):
        try:
            out = character.unequip(item)
            if isinstance(out, str) and out.strip():
                return out
            item.is_equipped = False
            if getattr(character, "weapon", None) is item:
                character.weapon = None
            return f"{character.name} unequips {item.name}."
        except Exception:
            pass

    equipment = getattr(character, "equipment", {})
    removed = False
    if isinstance(equipment, dict):
        for slot, itm in list(equipment.items()):
            if itm is item:
                equipment[slot] = None
                removed = True
                break
    item.is_equipped = False
    if getattr(character, "weapon", None) is item:
        character.weapon = None
    return f"{character.name} unequips {item.name}." if removed else f"{character.name} wasn’t wearing {item.name}."


def process_steal_action(item: gameRenderer.Item, target: gameRenderer.Character, thief: gameRenderer.Character) -> str:
    """
    Process the thief's attempt to steal an item from the target.

    Args:
        item (Item): The item to be stolen.
        target (Character): The character from whom the item is stolen.
        thief (Character): The character performing the theft.

    Returns:
        str: The result of the action.
    """

    # 1) Check if the target is alive and in the same area or party
    potential_targets = thief.current_area.characters + thief.party
    if target not in potential_targets:
        return f"{target.name} is not here to steal from."

    # 2) Check if the target actually has the item
    if item not in target.inventory:
        return f"{target.name} does not have '{item.name}'."

    # 3) Perform the theft
    target.remove_item(item)
    thief.add_item(item)

    try:
        process_equip_item_action(item, thief)
    except Exception:
        pass

    # 4) Lower friendship (minimum 0)
    current_friendship = target.friendship_with(thief)
    new_friendship = max(0, current_friendship - 1)
    target.friendships[thief] = new_friendship

    return (f"{thief.name} aggressively takes '{item.name}' from {target.name}. "
            f"Friendship with {target.name} is now {new_friendship}/10.")


def process_do_nothing_action(character: gameRenderer.Character) -> str:
    """
    Process the character's attempt to do nothing.

    Args:
        character (Character): The character who is doing nothing.

    Returns:
        str: The result of the action.
    """
    return f"{character.name} performs an action which results in no change."


def process_drop_item_action(
    item: gameRenderer.Item,
    character: gameRenderer.Character
) -> str:
    """
    Process the character's attempt to drop an item.

    Args:
        item (Item): The item to drop.
        character (Character): The character dropping the item.

    Returns:
        str: The result of the action.
    """
    # Check if the character has the item
    if item not in character.inventory:
        return f"{character.name} does not have a '{item.name}' to let go of."

    # Remove from character inventory
    character.remove_item(item)
    # Add to the area's key items
    character.current_area.key_items.append(item)
    item.holder = None
    item.position = character.current_area

    # Return a success message
    return f"{character.name} lets go of the {item.name}, it now lies on the floor."
