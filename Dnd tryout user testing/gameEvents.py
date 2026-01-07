#gameEvents.py

from typing import List, Optional, TYPE_CHECKING, Union, Tuple
import random

if TYPE_CHECKING:
    from gameRenderer import SubArea, Character, LinkingPoint, World, Item

class Event:
    def __init__(self, name: str, description: str, location: 'SubArea', participants: Optional[List['Character']] = None):
        self.name = name
        self.description = description
        self.location = location
        self.participants = participants if participants else []
        self.is_active = True

    def is_action_allowed(self, action_identifier: str) -> bool:
        """
        Check if an action is allowed during the event.
        """
        return True  # By default, all actions are allowed

    def resolve(self):
        """
        Resolve the event.
        """
        self.is_active = False
        if self in self.location.active_events:
            self.location.active_events.remove(self)

    def resolve_if_needed(self):
        """
        Check if the event should be resolved.
        """
        pass  # To be overridden in subclasses if needed

    def handle_action(self, action_identifier: str, action_args: List[str], character: 'Character'):
        """
        Give a way to resolve the action
        """
        pass  # To be overridden in subclasses if needed

class EventManager:
    def __init__(self):
        self.active_events: List[Union[Event]] = []

    def get_event_by_name(self, event_name: str):
        for event in self.active_events:
            if event.name.lower() == event_name.lower():
                return event
        return None

    def get_active_event_for_character(self, character: 'Character') -> Optional[Event]:
        for event in self.active_events:
            if event.is_active and character in event.participants:
                return event
        return None

    def check_for_event_triggers_after_action(self, character: 'Character') -> Optional[str]:
        """
        After any action, check whether any NPC in the actor's current area is hostile
        toward the actor (friendship <= 1). If so, start a fight between the hostile(s)
        and the actor. Also let any existing fights that involve the actor or happen in
        the same area resolve if conditions are met.
        """
        messages: List[str] = []

        # --- 1) Let ongoing fights that involve the actor resolve if needed
        fights_to_check = [
            event for event in self.active_events
            if isinstance(event, FightEvent) and event.is_active and character in event.participants
        ]
        for fight_event in fights_to_check:
            resolve_message = fight_event.resolve_if_needed()
            if resolve_message:
                messages.append(resolve_message)
                if not fight_event.is_active and fight_event in self.active_events:
                    self.active_events.remove(fight_event)

        # --- 2) Also sanity-check any fights currently in the actor's area
        area = character.current_area
        area_fights = [
            event for event in self.active_events
            if isinstance(event, FightEvent) and event.is_active and event.location == area
        ]
        for fight_event in area_fights:
            resolve_message = fight_event.resolve_if_needed()
            if resolve_message:
                messages.append(resolve_message)
                if not fight_event.is_active and fight_event in self.active_events:
                    self.active_events.remove(fight_event)

        # If a fight in this area is already active after the resolution pass, don't spawn another.
        active_fight_in_area = next(
            (event for event in self.active_events
            if isinstance(event, FightEvent) and event.location == area and event.is_active),
            None
        )
        if active_fight_in_area:
            return '\n'.join(messages) if messages else None

        # --- 3) Trigger condition: any *NPC* in this area who is alive and hostile *to the actor*
        hostile_npcs = []
        for c in area.characters:
            if not c.controllable and c.is_alive:
                if c.friendship_with(character) <= 1:  # hostile to the actor
                    hostile_npcs.append(c)

        if not hostile_npcs:
            # Nothing to trigger
            return '\n'.join(messages) if messages else None

        # Build participants: hostile NPCs + the actor (if alive)
        participants = []
        seen = set()
        for p in hostile_npcs + ([character] if character.is_alive else []):
            # de-dup by object id to avoid "Carley, Carley"
            pid = id(p)
            if p.is_alive and pid not in seen:
                seen.add(pid)
                participants.append(p)

        # Need at least two alive unique participants (a hostile + the actor)
        if len(participants) < 2:
            return '\n'.join(messages) if messages else None

        # Spawn the fight
        fight_event = FightEvent(area, participants)
        self.active_events.append(fight_event)
        area.active_events.append(fight_event)
        messages.append(fight_event.description)

        return '\n'.join(messages) if messages else None

    def is_fight_event_active(self, character_a: 'Character', character_b: 'Character') -> bool:
        """
        Check if a fight event is already active between two specific characters.
        """
        for event in self.active_events:
            if (isinstance(event, FightEvent)
                and set(event.participants) == {character_a, character_b}
                and event.is_active):
                return True
        return False

    def validate_movement(self, current_area: 'SubArea', destination_area: 'SubArea') -> str | None:
        """
        Check whether movement from current_area to destination_area is allowed.

        - Looks at both the manager's active_events and the areas' active_events.
        - Ignores events that are inactive, resolved, or non-blocking.
        - Returns a human-facing reason string if blocked, else None.
        """
        from gameEvents import BlockadeEvent  # local import to avoid circulars

        def _collect_blockades() -> list[BlockadeEvent]:
            seen = set()
            out: list[BlockadeEvent] = []

            pools = [
                getattr(self, "active_events", []),
                getattr(current_area, "active_events", []),
                getattr(destination_area, "active_events", []),
            ]
            for pool in pools:
                for ev in pool or []:
                    if not isinstance(ev, BlockadeEvent):
                        continue
                    if not getattr(ev, "is_active", True):
                        continue
                    if getattr(ev, "is_resolved", False):
                        continue
                    if not getattr(ev, "is_blocking", True):
                        continue
                    eid = id(ev)
                    if eid in seen:
                        continue
                    seen.add(eid)
                    out.append(ev)
            return out

        blockades = _collect_blockades()
        if not blockades:
            return None

        for ev in blockades:
            if not ev.is_move_allowed(current_area, destination_area):
                return ev.description  # “The door to the pharmacy is barricaded.”

        return None

    def handle_item_use(self, character: 'Character', item: 'Item') -> str | None:
        """
        Let active events in the character's location react to an item being used.

        We look in BOTH:
          - the current area's active_events
          - the global manager's active_events

        This makes sure we still find the blockade even if only one of those
        collections has it registered (or after a module reload).
        """
        here = getattr(character, "current_area", None)
        item_name = getattr(item, "name", "")

        messages: list[str] = []
        seen_ids: set[int] = set()

        def _iter_events():
            pools = []
            if here is not None:
                pools.append(getattr(here, "active_events", []))
            pools.append(getattr(self, "active_events", []))

            for pool in pools:
                for ev in pool or []:
                    eid = id(ev)
                    if eid in seen_ids:
                        continue
                    seen_ids.add(eid)
                    yield ev

        for ev in _iter_events():
            if not getattr(ev, "is_active", True):
                continue
            if not hasattr(ev, "handle_action"):
                continue

            # Be tolerant of different handle_action signatures
            resp = ""
            try:
                resp = ev.handle_action("use_item", [item_name], character) or ""
            except TypeError:
                try:
                    resp = ev.handle_action("use_item", [item_name]) or ""
                except Exception:
                    resp = ""
            except Exception:
                resp = ""

            if resp:
                messages.append(resp)

        return "\n".join(messages) if messages else None
    
    def initialize_events(self, world: 'World'):
        """
        Initialize events (e.g., blockade) after the world is created.
        """
        storage_room = world.get_sub_area_by_name("Storage Room")
        pharmacy = world.get_sub_area_by_name("Pharmacy")

        # Find the linking point between storage_room and pharmacy
        door_to_pharmacy = None
        for link in storage_room.linking_points:
            if link.get_other_area(storage_room) == pharmacy:
                door_to_pharmacy = link
                break

        if door_to_pharmacy:
            # Define the blockade events
            blocked_directions_bidirectional = [(storage_room, pharmacy), (pharmacy, storage_room)]

            bidir_blockade_event = BlockadeEvent(
                name="Blocked Pharmacy Door",
                description="The door to the pharmacy is barricaded.",
                linking_point=door_to_pharmacy,
                blocked_directions=blocked_directions_bidirectional,
                required_item="Fire Axe",
                resolved_description="A busted open door."
            )

            # Add blockade to each area
            storage_room.active_events.append(bidir_blockade_event)
            pharmacy.active_events.append(bidir_blockade_event)
            self.active_events.append(bidir_blockade_event)
            print("Bidirectional BlockadeEvent initialized between Storage Room and Pharmacy.")
        else:
            print("Error: Linking point to Pharmacy not found.")


class FightEvent(Event):
    def __init__(self, location: 'SubArea', participants: List['Character']):
        """
        Initialize a fight event in the given location with the specified participants.
        This version does NOT restrict actions while the event is active.
        """
        names = ', '.join([c.name for c in participants])
        super().__init__(
            name="Fight",
            description=f"A fight has broken out among: {names}.",
            location=location,
            participants=participants
        )
        # NOTE: no allowed_actions list — all actions are permitted during fights.

    def is_action_allowed(self, action_identifier: str) -> bool:
        """
        During a fight, ALL actions are allowed.
        """
        return True

    def handle_action(self, action_identifier: str, action_args: List[str], character: 'Character') -> str:
        """
        Optional helpers for fight-specific commands. Since actions aren't restricted,
        these only add extra behavior when you explicitly route through the event.
        """
        if action_identifier == 'harm':
            return self._handle_harm_action(character, action_args)

        elif action_identifier == 'ask_action':
            # e.g., "Zombie1 to harm Larry"
            return self._handle_ask_action(character, action_args)

        elif action_identifier == 'stop_event':
            return self.attempt_stop_fight(character)

        elif action_identifier == 'move':
            # Movement is allowed; the engine handles the actual move.
            # We simply no-op here and let resolve_if_needed run later.
            return ""

        # If no branch matched, do nothing special
        return ""

    # --------------------------------------------------------------------------
    # Utilities
    # --------------------------------------------------------------------------
    def _calc_damage(self, attacker: 'Character') -> int:
        """
        Safe damage helper:
        - Prefer attacker's own calculate_damage() if present.
        - Fallback to a simple, self-contained model.
        """
        try:
            if hasattr(attacker, "calculate_damage"):
                return max(1, int(attacker.calculate_damage()))
        except Exception:
            pass

        # Fallback: weapon.damage (or 5) scaled by (strength + skill)
        def _equipped_weapon(who: 'Character'):
            eq = getattr(who, "equipment", {})
            for slot in ("hand_right", "hand_left"):
                itm = isinstance(eq, dict) and eq.get(slot)
                if itm:
                    return itm
            return getattr(who, "weapon", None)

        wpn = _equipped_weapon(attacker)
        base = int(getattr(wpn, "damage", 0)) or 5
        strv = int(getattr(attacker, "strength", 5))
        skl  = int(getattr(attacker, "skill", 5))
        return max(1, int(round(base * (1.0 + (strv + skl) / 20.0))))

    def _find_by_name_here_or_party(self, character: 'Character', name_lower: str) -> Optional['Character']:
        for c in self.location.characters + list(character.party):
            if c.name.lower() == name_lower:
                return c
        return None

    # --------------------------------------------------------------------------
    # 'harm' Action
    # --------------------------------------------------------------------------
    def _handle_harm_action(self, character: 'Character', action_args: List[str]) -> str:
        """
        Process a direct harm action, e.g. "harm [target_name]".
        No auto-retaliation; witnesses only adjust friendship (if your Character.witness_violence is defined).
        """
        if not action_args:
            return "You must specify whom to harm, e.g. 'harm Larry'."

        target_name = action_args[0].lower()
        target = self._find_by_name_here_or_party(character, target_name)
        if not target:
            return f"{target_name.capitalize()} is not here."

        if not getattr(character, "is_alive", True):
            return f"{character.name} cannot attack while dead."

        if not getattr(target, "is_alive", True):
            return f"{target.name} is already dead."

        # Damage + apply
        dmg = self._calc_damage(character)
        target.update_health(-dmg)
        killed = not getattr(target, "is_alive", True)

        # Victim becomes hostile to attacker (friendship -> 0); no auto-counter
        try:
            cur = target.friendship_with(character)
            if cur > 0:
                target.update_friendship_with(character, -cur)
        except Exception:
            pass

        # Witness penalties only (no actions triggered)
        wit = ""
        try:
            # Static method version recommended
            wit = type(character).witness_violence(
                perpetrator=character, victim=target, action="harm", damage=dmg, killed=killed
            )
        except TypeError:
            # Fallback for legacy 3-arg signature
            try:
                wit = type(character).witness_violence(character, target, "harm")
            except Exception:
                pass
        except Exception:
            pass

        # Compose message
        msg = f"{character.name} attacks {target.name} dealing {dmg} damage."
        if killed:
            msg += f" {target.name} collapses and dies."
        if wit:
            msg += f" {wit}"

        # After any harm, see if this resolves the fight
        resolve_result = self.resolve_if_needed()
        if resolve_result:
            msg += f"\n{resolve_result}"

        return msg

    # --------------------------------------------------------------------------
    # 'ask_action' during a Fight — "<ally> to harm <enemy>"
    # --------------------------------------------------------------------------
    def _handle_ask_action(self, character: 'Character', action_args: List[str]) -> str:
        if not action_args:
            return "Please specify e.g. 'Zombie1 to harm Larry'."

        full = ' '.join(action_args).lower()
        if " to harm " not in full:
            return "Invalid request format. Use: <characterName> to harm <targetName>."

        left_side, right_side = full.split(" to harm ", 1)
        ally_name = left_side.strip()
        enemy_name = right_side.strip()

        ally = self._find_by_name_here_or_party(character, ally_name)
        if not ally:
            return f"{ally_name.capitalize()} is not here."

        enemy = self._find_by_name_here_or_party(character, enemy_name)
        if not enemy:
            return f"{enemy_name.capitalize()} is not here."

        # Willingness: keep your previous check (ally dislikes enemy => will comply)
        try:
            willing = ally.friendship_with(enemy) <= 1
        except Exception:
            willing = True

        if not willing:
            return f"{ally.name} refuses to harm {enemy.name}."

        # Execute single strike; no auto-retaliation
        dmg = self._calc_damage(ally)
        enemy.update_health(-dmg)
        killed = not getattr(enemy, "is_alive", True)

        # Victim becomes hostile to ally
        try:
            cur = enemy.friendship_with(ally)
            if cur > 0:
                enemy.update_friendship_with(ally, -cur)
        except Exception:
            pass

        # Witness penalties
        wit = ""
        try:
            wit = type(ally).witness_violence(
                perpetrator=ally, victim=enemy, action="harm", damage=dmg, killed=killed
            )
        except TypeError:
            try:
                wit = type(ally).witness_violence(ally, enemy, "harm")
            except Exception:
                pass
        except Exception:
            pass

        msg = f"{ally.name} attacks {enemy.name} at your request, dealing {dmg} damage."
        if killed:
            msg += f" {enemy.name} has died."
        if wit:
            msg += f" {wit}"

        resolve_result = self.resolve_if_needed()
        if resolve_result:
            msg += f"\n{resolve_result}"
        return msg

    # --------------------------------------------------------------------------
    # Attempt to stop the fight
    # --------------------------------------------------------------------------
    def attempt_stop_fight(self, character: 'Character') -> str:
        """
        End the fight outright (no gating by friendship here).
        """
        return self.resolve()

    # --------------------------------------------------------------------------
    # Check if we can safely resolve the fight
    # --------------------------------------------------------------------------
    def resolve_if_needed(self) -> str:
        """
        End the fight if participants died or left the area.
        Returns a message if resolved, else "".
        """
        # Remove dead from participants
        alive_participants = [p for p in self.participants if getattr(p, "is_alive", True)]
        if len(alive_participants) != len(self.participants):
            self.participants = alive_participants

        if len(self.participants) < 2:
            return self.resolve()

        # If participants have split across areas, end it
        areas = {p.current_area for p in self.participants if getattr(p, "is_alive", True)}
        if len(areas) > 1:
            return self.resolve()

        return ""

    # --------------------------------------------------------------------------
    # Final resolution of the fight
    # --------------------------------------------------------------------------
    def resolve(self) -> str:
        """
        Marks the fight as over, removes it from the location's events,
        and returns a short resolution message.
        """
        super().resolve()
        return "The fight has ended."


# in gameEvents.py

class BlockadeEvent(Event):
    """
    A door/connection blockade between two SubAreas.
    Blocks movement in specific (area_a -> area_b) direction pairs until resolved,
    usually by using a particular item (e.g., Fire Axe).
    """

    def __init__(
        self,
        name: str,
        description: str,
        linking_point,  # type: 'LinkingPoint'
        blocked_directions,  # type: list[tuple['SubArea', 'SubArea']]
        required_item: str | None = None,
        resolved_description: str = "",
    ):
        # Anchor the event in one area (area_a) so it lives in the world
        super().__init__(name, description, location=linking_point.area_a)
        self.linking_point = linking_point

        # Store *uids* instead of raw object refs for robust comparison
        self.blocked_pairs: list[tuple[str, str]] = []
        for a, b in blocked_directions:
            uid_a = getattr(a, "uid", None)
            uid_b = getattr(b, "uid", None)
            if uid_a is not None and uid_b is not None:
                self.blocked_pairs.append((uid_a, uid_b))

        self.required_item = required_item
        self.resolved_description = resolved_description or description

        self.is_resolved = False
        self.is_blocking = True
        self.is_active = True  # important: so validate_movement & handle_item_use see it

    # ------------------------------------------------------------------
    # Movement gate
    # ------------------------------------------------------------------
    def is_move_allowed(self, current_area, destination_area) -> bool:
        """
        Return True if movement is allowed past this blockade, False if blocked.
        """
        if not getattr(self, "is_active", True) or self.is_resolved:
            return True

        uid_a = getattr(current_area, "uid", None)
        uid_b = getattr(destination_area, "uid", None)
        if uid_a is None or uid_b is None:
            # If something is weird, fail open rather than soft-locking the player
            return True

        return (uid_a, uid_b) not in self.blocked_pairs

    # ------------------------------------------------------------------
    # Interaction: use_item / examine
    # ------------------------------------------------------------------
    def handle_action(self, action_identifier: str, action_args: list[str], character) -> str:
        """
        React to 'use_item' and 'examine'.
        """
        # EXAMINE just shows the current description
        if action_identifier == "examine":
            return self.description

        if action_identifier != "use_item":
            return ""

        # We only care about the required item
        item_name = (action_args[0] or "").strip().lower() if action_args else ""
        required = (self.required_item or "").strip().lower()

        # If the wrong item is used, give a helpful message
        if required and item_name and item_name != required:
            return f"The {action_args[0]} can’t be used to clear this blockade."

        # If we require an item but nothing matching is used, do nothing
        if required and item_name != required:
            return ""

        # Check that the character actually has the required item
        inv = getattr(character, "inventory", []) or []
        held_item = None
        for it in inv:
            if getattr(it, "name", "").strip().lower() == required:
                held_item = it
                break

        if required and held_item is None:
            # This should almost never happen if validation is correct, but be defensive
            return f"You don’t seem to have a {self.required_item} to use."

        # All conditions met → resolve the blockade
        self.resolve()

        msg_parts = []
        msg_parts.append(
            f"You use the {self.required_item or action_args[0]} to dismantle the blockade."
        )
        if self.resolved_description:
            msg_parts.append(f"It is now {self.resolved_description}")

        # Optionally break the tool if it’s fragile
        if held_item is not None and getattr(held_item, "robustness", 100) <= 20:
            try:
                character.remove_item(held_item)
            except Exception:
                pass
            msg_parts.append(f"The {held_item.name} breaks in the process.")

        return " ".join(msg_parts)

    # ------------------------------------------------------------------
    # Resolution: actually turn the wall off
    # ------------------------------------------------------------------
    def resolve(self) -> None:
        """
        Mark the blockade as resolved and unblock the linking point in both directions.
        """
        if self.is_resolved:
            return

        self.is_resolved = True
        self.is_blocking = False
        self.is_active = False

        # Update description so later 'examine' reflects the open door
        if self.resolved_description:
            self.description = self.resolved_description

        lp = getattr(self, "linking_point", None)

        # Unblock the physical link if it has a flag
        if lp is not None:
            try:
                # Many linking-point implementations use a 'blocked' flag
                setattr(lp, "blocked", False)
            except Exception:
                pass

            # Best-effort: remove this event from both connected areas
            for area in (getattr(lp, "area_a", None), getattr(lp, "area_b", None)):
                try:
                    if area and hasattr(area, "active_events") and self in area.active_events:
                        area.active_events.remove(self)
                except Exception:
                    pass

        # Also remove from the global manager’s registry if present
        try:
            from gameEvents import event_manager  # safe, same module
            if self in getattr(event_manager, "active_events", []):
                event_manager.active_events.remove(self)
        except Exception:
            # Don’t crash the game if import/order is odd
            pass


class ConversationEvent(Event):
    """
    A multi-round conversation where each "round" focuses on exactly one topic.
    Steps per round:
      1) NEED_TOPIC: A participant introduces a topic (player if they have one, else NPC).
      2) WAITING_FOR_PLAYER_RESPONSE: If there's a player, we wait for them to "talk" once.
      3) NPC_RESPONSES: The NPCs all call get_opinion(...) about that topic.
      4) Round ends, we return to NEED_TOPIC for a new round, unless conversation is stopped.
    """

    PHASE_NEED_TOPIC = "need_topic"
    PHASE_WAITING_FOR_PLAYER_RESPONSE = "waiting_for_player_response"
    PHASE_NPC_RESPONSES = "npc_responses"

    def __init__(self, participants: List['Character'], private: bool = False):
        location = participants[0].current_area if participants else None
        super().__init__(
            name="Conversation",
            description="A conversation is taking place.",
            location=location,
            participants=participants
        )
        self.is_blocking = True
        self.private = private

        # Allowed actions in a conversation
        self.allowed_actions = ['talk', 'attack', 'examine', 'stop_event', 'do_nothing', 'ask_action']

        # Current round's topic
        self.current_topic: Optional[str] = None

        # Which "phase" of the conversation round we are in
        self.conversation_phase = self.PHASE_NEED_TOPIC

        # The participant who introduced the current topic
        self.topic_initiator = None

        # NEW: Track who has responded this round (so they can't respond twice)
        self.responded_this_round = set()

        # DEBUG PRINT
        print(f"[DEBUG] ConversationEvent initialized with participants: "
              f"{[p.name for p in participants]}. Private: {self.private}")

    def is_action_allowed(self, action_identifier: str) -> bool:
        # DEBUG PRINT
        print(f"[DEBUG] Checking if action '{action_identifier}' is allowed in conversation.")
        return action_identifier in self.allowed_actions

    # ----------------------------------------------------------------------
    # Master action handler
    # ----------------------------------------------------------------------
    def handle_action(self, action_identifier: str, action_args: List[str], character: 'Character') -> str:
        """
        Accept both 'use' and 'use_item'. Resolve if the required item is either
        in the actor's inventory OR lying in the current area (items/key_items).
        """
        message = ""
        if action_identifier not in ("use_item", "use"):
            if action_identifier == "examine":
                return self.description
            return message  # ignore others

        need = (self.required_item or "").lower()
        used_name = (action_args[0] if action_args else "") or ""
        used_name = used_name.strip().lower()

        # If user said "use Fire Axe", take that; otherwise fall back to required item name
        probe = used_name or need

        # Find candidate item: inventory first, then area pools
        def _find_item():
            for it in getattr(character, "inventory", []) or []:
                if getattr(it, "name", "").lower() in (probe, need):
                    return it
            area = getattr(character, "current_area", None)
            if area:
                for pool in ("items", "key_items"):
                    for it in getattr(area, pool, []) or []:
                        if getattr(it, "name", "").lower() in (probe, need):
                            return it
            return None

        it = _find_item()
        if it is None:
            return "You don't have a usable item for this blockade."

        if getattr(it, "name", "").lower() != need:
            return f"The {getattr(it,'name','item')} can't be used to resolve this blockade."

        # Resolve the blockade
        self.resolve()

        # Optional: fragile tools can break only if carried by character
        if getattr(it, "robustness", 0) <= 20 and it in getattr(character, "inventory", []):
            try:
                character.remove_item(it)
                message += f"{it.name} broke."
            except Exception:
                pass

        return message + f"You use the {self.required_item} to dismantle the blockade. It is now {self.resolved_description}"

    # ----------------------------------------------------------------------
    # The "talk" action logic, depending on phase
    # ----------------------------------------------------------------------
    def handle_talk(self, character: 'Character', line: Optional[str]):
        """
        Called when a participant does "talk".
        We behave differently based on the conversation phase.
        """
        # DEBUG PRINT
        print(f"[DEBUG] handle_talk called by {character.name} with line: {line}. "
              f"Current phase: {self.conversation_phase}")

        # If this character already responded this round, they can't respond again
        if character in self.responded_this_round:
            # DEBUG PRINT
            print(f"[DEBUG] {character.name} has already spoken this round.")
            return f"{character.name} has already spoken during this round."

        if self.conversation_phase == self.PHASE_NEED_TOPIC:
            return self._introduce_topic(character, line)

        elif self.conversation_phase == self.PHASE_WAITING_FOR_PLAYER_RESPONSE:
            return self._player_response(character, line)

        elif self.conversation_phase == self.PHASE_NPC_RESPONSES:
            # If we somehow get a "talk" here, ignore or respond with "Wait"
            return "The NPCs are already responding. Please wait."

        return "Conversation has no defined phase."

    # ----------------------------------------------------------------------
    # PHASE 1: NEED_TOPIC
    # ----------------------------------------------------------------------
    def _introduce_topic(self, character: 'Character', line: Optional[str]) -> str:
        """
        NEED_TOPIC: only a controllable player can set a topic; NPCs never initiate.
        No automatic NPC response is triggered.
        """
        # Start fresh each round
        self.responded_this_round.clear()

        if character.controllable and line:
            # Player sets the topic; no NPC responses follow in this build
            self.current_topic = line
            self.topic_initiator = character
            self.conversation_phase = self.PHASE_NEED_TOPIC
            return f"{character.name} introduced a topic: '{line}'. Others wait for your input."
        else:
            # NPCs do not introduce topics; idle and wait for the player
            self.current_topic = None
            self.topic_initiator = None
            self.conversation_phase = self.PHASE_NEED_TOPIC
            return "They wait for your input."


    def _choose_npc_topic(self):
        npc_candidates = [p for p in self.participants if not p.controllable and p.topics]
        if not npc_candidates:
            return None
        chosen_npc = random.choice(npc_candidates)
        chosen_topic = chosen_npc.topics[0]  # not removed from their list
        # DEBUG PRINT
        print(f"[DEBUG] _choose_npc_topic found {chosen_npc.name} with topic '{chosen_topic}'.")
        return {"npc": chosen_npc, "topic": chosen_topic}

    def _npc_only_no_topic_description(self) -> str:
        if not self.participants:
            return "No one is here to talk."
        names = [p.name for p in self.participants]
        return f"{', '.join(names)} are talking idly with no particular topic."

    # ----------------------------------------------------------------------
    # PHASE 2: WAITING_FOR_PLAYER_RESPONSE
    # ----------------------------------------------------------------------
    def _player_response(self, character: 'Character', line: Optional[str]) -> str:
        """
        We already have a 'current_topic'.
        The next 'talk' from a controllable character is their 'response'.
        Then all NPCs respond as well, completing this round.

        The *player who introduced the topic* won't also respond in the same round
        because we added them to responded_this_round already.

        After the NPC responses, we conclude this round => back to NEED_TOPIC,
        and we clear responded_this_round so a new topic can be introduced.
        """
        # Must be a controllable character
        if not character.controllable:
            return f"{character.name} tries to speak, but we are waiting for a player."

        if not line:
            return "You must 'talk' and provide something to say about the topic."

        # Mark the player as having responded
        self.responded_this_round.add(character)

        msg = f"You respond: '{line}' about '{self.current_topic or 'nothing'}'."

        # Now let the NPCs respond
        self.conversation_phase = self.PHASE_NPC_RESPONSES
        npc_msg = self._npc_round_of_responses()
        msg += "\n" + npc_msg

        # End this round => back to NEED_TOPIC for a new topic
        self.conversation_phase = self.PHASE_NEED_TOPIC
        msg += "\nThe topic is concluded. You can introduce a new topic or leave."

        # >>> Clear responded_this_round so the player (and others) can speak again
        self.responded_this_round.clear()

        return msg

    # ----------------------------------------------------------------------
    # PHASE 3: NPC_RESPONSES
    # ----------------------------------------------------------------------
    def _npc_round_of_responses(self) -> str:
        """
        PHASE 3: NPC_RESPONSES
        All NPCs (except the initiator who already 'spoke') call get_opinion(...)
        That uses their turn for the round (we mark them responded).

        After we're done, if the calling method sets conversation_phase=NEED_TOPIC,
        we can also reset responded_this_round so a new topic can start fresh.
        """
        # DEBUG (optional):
        # print(f"[DEBUG] _npc_round_of_responses with topic={self.current_topic}, initiator={self.topic_initiator}")

        lines = []
        for p in self.participants:
            # If they are NPC and haven't responded yet this round
            if not p.controllable and p not in self.responded_this_round and p.is_alive:
                line = p.get_opinion(self.topic_initiator, self.current_topic)
                lines.append(f"{p.name} says: '{line}'")
                # Mark that they've responded (and we also set has_acted=True in this logic)
                p.has_acted = True
                self.responded_this_round.add(p)

        if not lines:
            return "No NPCs had an opinion to share (or they already responded)."
        else:
            self.responded_this_round.clear()
            #
            # But typically, we let the calling method do that at the end of the round.
            return "\n".join(lines)

    # ----------------------------------------------------------------------
    # STOPPING THE CONVERSATION
    # ----------------------------------------------------------------------
    def attempt_stop_conversation(self, character: 'Character') -> str:
        # DEBUG PRINT
        print(f"[DEBUG] attempt_stop_conversation by {character.name}, private: {self.private}")

        if self.private:
            return "This is a private conversation. You cannot end it prematurely."
        else:
            if character in self.participants:
                self.participants.remove(character)
                leave_msg = f"{character.name} leaves the conversation."
                if len(self.participants) < 2:
                    return leave_msg + "\n" + self.end_conversation("The conversation ends as participants leave.")
                return leave_msg + "\nThe conversation continues among the others."
            else:
                return f"{character.name} is not part of this conversation."

    def _has_player_participant(self) -> bool:
        return any(p.controllable for p in self.participants)

    def end_conversation(self, reason: str = "The conversation has ended."):
        # DEBUG PRINT
        print(f"[DEBUG] end_conversation called. reason: {reason}")
        self.is_blocking = False
        if self in self.location.active_events:
            self.location.active_events.remove(self)
        self.is_active = False
        return reason

# Instantiate the EventManager
event_manager = EventManager()