# gameRenderer.py

from typing import List, Optional, TYPE_CHECKING, Dict, Set, Tuple
from uuid import uuid4

if TYPE_CHECKING:
    from gameEvents import Event

# ----------------------------
# Ability System (generic)
# ----------------------------
class Ability:
    """
    A generic, attachable ability. It can belong to items and/or characters (and later areas),
    and can implement mechanics later via `is_applicable` / `apply`.

    - name: short label of the ability
    - description: what it does (human readable)
    - attributed_to: mapping of entity kind -> set of entity uids (e.g., {"character": {"Lee_abc123"}})
    - uid: unique id for this ability definition (optional; auto if omitted)
    """
    def __init__(
        self,
        name: str,
        description: str = "",
        attributed_to: Optional[Dict[str, Set[str]]] = None,
        uid: Optional[str] = None
    ):
        self.uid: str = uid or f"Ability_{name}_{uuid4().hex[:6]}"
        self.name = name
        self.description = description
        self.attributed_to: Dict[str, Set[str]] = attributed_to or {}

    # Attachment helpers
    def attach_to(self, entity_kind: str, entity_uid: str) -> None:
        bucket = self.attributed_to.setdefault(entity_kind, set())
        bucket.add(entity_uid)

    def detach_from(self, entity_kind: str, entity_uid: str) -> None:
        if entity_kind in self.attributed_to:
            self.attributed_to[entity_kind].discard(entity_uid)
            if not self.attributed_to[entity_kind]:
                del self.attributed_to[entity_kind]

    def is_attributed_to(self, entity_kind: str, entity_uid: str) -> bool:
        return entity_uid in self.attributed_to.get(entity_kind, set())

    # Mechanics placeholders (extend later)
    def is_applicable(self, context: Dict) -> bool:
        """
        Return True if this ability should do something given the 'context'.
        Context could include: action type, actor uid, target uid, area uid, etc.
        """
        return True

    def apply(self, context: Dict) -> Optional[str]:
        """
        Apply the ability effect. Return an optional message describing the effect.
        Concrete abilities can override this.
        """
        return None

    def __repr__(self) -> str:
        return f"Ability({self.name}, uid={self.uid})"


class Item:
    def __init__(
        self,
        name: str,
        position: Optional['SubArea'] = None,
        holder: Optional['Character'] = None,
        known_by: Optional[List['Character']] = None,
        robustness: int = 0,
        damage: int = 0,
        description: str = "",
        *,
        uid: Optional[str] = None,
        is_equipped: bool = False,
        abilities: Optional[List[Ability]] = None
    ):
        # Identity
        self.uid: str = uid or f"Item_{name}_{uuid4().hex[:6]}"
        self.name = name

        # World placement / ownership
        self.position = position
        self.holder = holder

        # Discovery/knowledge
        self.known_by = known_by if known_by is not None else []

        # Stats
        self.robustness = robustness
        self.damage = damage
        self.description = description

        # Equipment/abilities
        self.is_equipped = is_equipped
        self.abilities: List[Ability] = list(abilities) if abilities else []

    def __repr__(self):
        return f"Item({self.name}, uid={self.uid})"


class LinkingPoint:
    def __init__(self, description: str, area_a: 'SubArea', area_b: 'SubArea', mini_event: Optional['Event'] = None):
        self.description = description
        self.area_a = area_a
        self.area_b = area_b
        self.mini_event = mini_event

    def get_other_area(self, current_area: 'SubArea') -> 'SubArea':
        return self.area_b if current_area == self.area_a else self.area_a

    def __repr__(self):
        return f"LinkingPoint({self.area_a.name} <-> {self.area_b.name})"


class SubArea:
    def __init__(self, name: str, description: str, exit: bool = False, *, uid: Optional[str] = None, known_by: Optional[List['Character']] = None):
        # Identity
        self.uid: str = uid or f"Area_{name}_{uuid4().hex[:6]}"
        self.name = name
        self.description = description

        # Topology
        self.linking_points: List[LinkingPoint] = []
        self.exit = exit

        # Contents
        self.key_items: List[Item] = []
        self.characters: List['Character'] = []
        self.active_events: List['Event'] = []  # type: ignore[name-defined]

        # Knowledge (like items)
        self.known_by: List['Character'] = known_by if known_by is not None else []

    def add_linking_point(self, linking_point: LinkingPoint):
        self.linking_points.append(linking_point)

    def get_linked_areas(self) -> List['SubArea']:
        return [link.get_other_area(self) for link in self.linking_points]

    def get_items(self) -> List[str]:
        """
        Returns a list of summary strings for all items in this sub-area.
        Includes floor items and inventories of characters in the area.
        """
        items: List[Item] = []
        items.extend(self.key_items)
        for character in self.characters:
            items.extend(character.inventory)

        summary_list = []
        for item in items:
            eq = " (equipped)" if getattr(item, "is_equipped", False) else ""
            summary = f"ID: {item.uid}, Name: {item.name}{eq}, Description: {item.description}, Robustness: {item.robustness}"
            summary_list.append(summary)
        return summary_list

    def get_all_characters(self) -> str:
        """
        Returns a summary string of all characters present in this sub-area.
        """
        summary_lines = []
        for char in list(self.characters):
            personality = (f"Openness: {char.openness}, "
                           f"Conscientiousness: {char.conscientiousness}, "
                           f"Extraversion: {char.extraversion}, "
                           f"Agreeableness: {char.agreeableness}, "
                           f"Neuroticism: {char.neuroticism}")
            stats = (f"Strength: {char.strength}, Intelligence: {char.intelligence}, "
                     f"Skill: {char.skill}, Speed: {char.speed}, Endurance: {char.endurance}")
            line = (f"ID: {char.uid}, Name: {char.name}, Health: {char.health}, "
                    f"Location: {char.current_area.name}, Gender: {char.gender}, "
                    f"Personality: ({personality}), Stats: ({stats})")
            summary_lines.append(line)
        return "\n".join(summary_lines)


class Character:
    def __init__(
        self,
        name: str,
        description: str,
        current_area: SubArea,
        health: int = 100,
        controllable: bool = False,  # True => player / controllable
        gender: int = 0,             # 0=female, 1=male, 2=unspecified
        # OCEAN (0..10)
        openness: int = 5,
        conscientiousness: int = 5,
        extraversion: int = 5,
        agreeableness: int = 5,
        neuroticism: int = 5,
        # Combat/skill stats (0..10)
        strength: int = 5,
        intelligence: int = 5,
        skill: int = 5,
        speed: int = 5,
        endurance: int = 5,
        *,
        uid: Optional[str] = None,
        abilities: Optional[List[Ability]] = None
    ):
        # Identity
        self.uid: str = uid or f"Char_{name}_{uuid4().hex[:6]}"
        self.name = name
        self.description = description

        # Placement & relations
        self.current_area = current_area
        self.friendships: Dict['Character', int] = {}
        self.gender = gender
        self.inventory: List[Item] = []
        self.party: List['Character'] = []

        # State
        self.health = health
        self.is_alive = True
        self.has_acted = False
        self.controllable = controllable
        self.topics: List[str] = []

        # Legacy weapon pointer (kept for backward compatibility)
        self.weapon: Optional[Item] = None

        # OCEAN (clamped)
        self.openness = max(0, min(openness, 10))
        self.conscientiousness = max(0, min(conscientiousness, 10))
        self.extraversion = max(0, min(extraversion, 10))
        self.agreeableness = max(0, min(agreeableness, 10))
        self.neuroticism = max(0, min(neuroticism, 10))

        # Combat/skill stats (clamped)
        self.strength = max(0, min(strength, 10))
        self.intelligence = max(0, min(intelligence, 10))
        self.skill = max(0, min(skill, 10))
        self.speed = max(0, min(speed, 10))
        self.endurance = max(0, min(endurance, 10))

        # Abilities
        self.abilities: List[Ability] = list(abilities) if abilities else []

        # Equipment slots (new)
        self.equipment: Dict[str, Optional[Item]] = {
            "head": None,
            "torso": None,
            "legs": None,
            "left_hand": None,
            "right_hand": None,
            "extra": None,  # accessories, rings, trinkets, etc.
        }

        # Last-known snapshots (knowledge can become stale until refreshed)
        # entry = { "entity_type": "item|character|area", "uid": str, "name": str, "reason": str, "snapshot": dict }
        # Last-known snapshots (rich state cache)
        self.knowledge: Dict[str, Dict] = {}

        # NEW: lightweight knowledge indices (UID sets) for gating logic
        self.known_items: Set[str] = set()
        self.known_areas: Set[str] = set()
        self.known_people: Set[str] = set()

    # ---------- Movement ----------
    def move_to(self, target_area: SubArea):
        previous_area = self.current_area

        if self.current_area and self in self.current_area.characters:
            self.current_area.characters.remove(self)

        self.current_area = target_area
        self.nearby_location = target_area  # legacy hint

        target_area.characters.append(self)

        # NEW: when you arrive, you now *know* this area
        try:
            self.learn_area(target_area, reason="visit")
        except Exception:
            pass

        # Move party members
        for member in self.party:
            if member.current_area and member in member.current_area.characters:
                member.current_area.characters.remove(member)
            member.current_area = target_area
            target_area.characters.append(member)
            # Party members also learn the area
            try:
                member.learn_area(target_area, reason="visit_with_party")
            except Exception:
                pass

    # ---------- Combat target selection ----------
    def find_attack_target(self) -> Optional['Character']:
        for character in self.current_area.characters:
            if character != self and character.is_alive:
                if self.friendship_with(character) <= 1:
                    return character
        return None

    # ---------- Inventory / Equipment ----------
    def add_item(self, item: Item):
        self.inventory.append(item)
        item.holder = self
        item.position = None
        # Back-compat: auto-equip weapon to right hand if hands are free
        if item.damage > 0 and self.equipment.get("right_hand") is None and self.equipment.get("left_hand") is None:
            self.equip(item, slot="right_hand")
        # NEW: you now *know* this item
        try:
            self.learn_item(item, reason="possession")
        except Exception:
            # keep at least the snapshot in case learn failed
            self.remember(item, reason="possession")
            self._ensure_known_by(item)


    def remove_item(self, item: Item):
        if item in self.inventory:
            # If equipped in any slot, unequip it first
            self.unequip(item)
            self.inventory.remove(item)
            item.holder = None
            # Legacy weapon pointer
            if self.weapon == item:
                self.weapon = None
            # Keep a 'last seen' memory; we do NOT auto-forget knowledge
            self.remember(item, reason="possession_end")


    def _slot_of(self, item: Item) -> Optional[str]:
        for slot, it in self.equipment.items():
            if it is item:
                return slot
        return None

    def _set_weapon_from_hands(self) -> None:
        """Maintain legacy self.weapon based on strongest hand item, or None."""
        rh = self.equipment.get("right_hand")
        lh = self.equipment.get("left_hand")
        best = None
        if rh and lh:
            best = rh if rh.damage >= lh.damage else lh
        else:
            best = rh or lh
        self.weapon = best

    def equip(self, item: Item, slot: Optional[str] = None, *, hand_preference: str = "right") -> bool:
        """
        Equip an item you hold into a slot. If no slot is given:
        - Damage items prefer hands (right, then left, or replace preferred hand).
        - Non-damage items prefer 'extra'.
        Returns True on success.
        """
        if item not in self.inventory:
            return False

        valid_slots = {"head", "torso", "legs", "left_hand", "right_hand", "extra"}
        if slot is not None and slot not in valid_slots:
            return False

        # If item is already equipped somewhere, moving it to a new slot is allowed.
        prev_slot = self._slot_of(item)
        if slot is None:
            # Auto-select: hands for weapons, else extra
            if item.damage > 0:
                # Try preferred hand, then the other, else replace preferred
                primary = f"{hand_preference}_hand" if hand_preference in ("left", "right") else "right_hand"
                secondary = "left_hand" if primary == "right_hand" else "right_hand"
                if self.equipment.get(primary) is None:
                    slot = primary
                elif self.equipment.get(secondary) is None:
                    slot = secondary
                else:
                    # Replace primary occupant
                    self.unequip_slot(primary)
                    slot = primary
            else:
                # Accessories/armor default to extra if free; otherwise first free armor slot; otherwise extra (replace)
                if self.equipment.get("extra") is None:
                    slot = "extra"
                elif self.equipment.get("torso") is None:
                    slot = "torso"
                elif self.equipment.get("head") is None:
                    slot = "head"
                elif self.equipment.get("legs") is None:
                    slot = "legs"
                else:
                    # Replace 'extra'
                    self.unequip_slot("extra")
                    slot = "extra"

        # Unequip whatever sits in the target slot (if any)
        if self.equipment.get(slot) is not None and self.equipment[slot] is not item:
            self.unequip_slot(slot)

        # Remove from previous slot if moving
        if prev_slot and prev_slot != slot:
            self.unequip_slot(prev_slot)

        # Place item
        self.equipment[slot] = item
        item.is_equipped = True

        # Legacy weapon pointer if in hands
        if slot in ("left_hand", "right_hand"):
            self._set_weapon_from_hands()

        # Knowledge
        self.remember(item, reason=f"equip:{slot}")
        return True

    def equip_item(self, item: Item, slot: Optional[str] = None, *, hand_preference: str = "right") -> bool:
        """Convenience wrapper matching your 'equip_item' action."""
        return self.equip(item, slot=slot, hand_preference=hand_preference)

    def unequip(self, item: Item) -> bool:
        """Unequip a specific item from whichever slot it occupies."""
        slot = self._slot_of(item)
        if not slot:
            return False
        self.unequip_slot(slot)
        return True

    def unequip_slot(self, slot: str) -> Optional[Item]:
        """Unequip whatever sits in a given slot; return that item (or None)."""
        if slot not in self.equipment:
            return None
        it = self.equipment.get(slot)
        if it is None:
            return None
        self.equipment[slot] = None
        it.is_equipped = False

        # Legacy weapon pointer if hands changed
        if slot in ("left_hand", "right_hand"):
            self._set_weapon_from_hands()

        # Knowledge
        self.remember(it, reason=f"unequip:{slot}")
        return it

    def unequip_item(self, item: Item) -> bool:
        """Convenience wrapper matching your 'unequip_item' action."""
        return self.unequip(item)

    def get_equipped_item(self) -> Optional[Item]:
        """
        Backward-compat helper: prefer right hand, then left hand,
        else any equipped item, else None.
        """
        rh = self.equipment.get("right_hand")
        if rh:
            return rh
        lh = self.equipment.get("left_hand")
        if lh:
            return lh
        # fall back to any equipped (useful for old UI that just wants a marker)
        for slot, it in self.equipment.items():
            if it:
                return it
        return None

    def equipment_state(self) -> Dict[str, Optional[str]]:
        """Readable summary: slot -> item name or None."""
        return {slot: (it.name if it else None) for slot, it in self.equipment.items()}

    # ---------- Inventory presentation ----------
    def get_inventory_descriptions(self) -> str:
        """
        Returns a string listing the name and description of each item
        in the character's inventory (marks equipped & slot).
        """
        if not self.inventory:
            return "No items in inventory."
        lines = []
        slot_of = {it: slot for slot, it in self.equipment.items() if it is not None}
        for item in self.inventory:
            slot = slot_of.get(item)
            eq = f" (equipped in {slot})" if slot else ""
            lines.append(f"{item.name}{eq}: {item.description}")
        return "\n".join(lines)

    # ---------- Party helpers ----------
    def add_party_member(self, character: 'Character', reciprocal: bool = True):
        """
        Add `character` to this character's party (and vice versa if reciprocal=True),
        then broadcast introductions so *every* party member knows every other.
        """
        if character in self.party or character is self:
            return

        # Add locally
        self.party.append(character)
        # Maintain bidirectional party link
        if reciprocal:
            character.add_party_member(self, reciprocal=False)

        # Both the leader and the new member should at least know each other
        try:
            self.learn_person(character, reason="party")
        except Exception:
            pass
        try:
            character.learn_person(self, reason="party")
        except Exception:
            pass

        # Broadcast introductions to the whole current party (including self & the newcomer)
        # This ensures: for any a!=b in party∪{self}, a.knows_person(b) == True
        members = list(set(self.party + [self]))
        for a in members:
            for b in members:
                if a is not b:
                    try:
                        a.learn_person(b, reason="party_introduction")
                    except Exception:
                        # Knowledge is best-effort; don't break party formation if a learn fails
                        pass


    def remove_party_member(self, character: 'Character', reciprocal: bool = True):
        if character in self.party:
            self.party.remove(character)
            if reciprocal:
                character.remove_party_member(self, reciprocal=False)
            self.remember(character, reason="party_end")

    # ---------- Health ----------
    def _clamp_health(self) -> None:
        if self.health > 100:
            self.health = 100
        if self.health <= 0:
            self.health = 0
            if self.is_alive:
                self.is_alive = False

    def apply_damage(self, amount: int) -> int:
        if amount < 0:
            amount = 0
        if not self.is_alive:
            return 0
        before = self.health
        self.health -= amount
        self._clamp_health()
        return before - self.health

    def heal(self, amount: int) -> int:
        if amount < 0:
            amount = 0
        if not self.is_alive:
            return 0
        before = self.health
        self.health += amount
        self._clamp_health()
        return self.health - before

    def update_health(self, amount: int):
        if amount >= 0:
            self.heal(amount)
        else:
            self.apply_damage(-amount)

    # ---------- Social ----------
    def update_friendship_with(self, character: 'Character', amount: int):
        current_level = self.friendships.get(character, 5)
        if current_level != 0:   # 0 = immutable hostility
            new_level = current_level + amount
            if new_level <= 1:
                new_level = 1
            self.friendships[character] = max(0, min(new_level, 10))

    def friendship_with(self, character: 'Character') -> int:
        return self.friendships.get(character, 5)

    # ---------- Reactions ----------
    @staticmethod
    def witness_violence(
        self,
        aggressor: 'Character',
        victim: 'Character',
        severity: float = 1.0,
        killed: bool = False,
    ) -> None:
        """
        A witness (self) observes aggressor harming victim.
        This can reduce friendship toward aggressor depending on:
        - severity (0..1+)
        - witness' affinity toward victim (how much they care about the victim)

        Change: the "killed" bonus is now ALSO scaled by affinity.
        So if the witness has 0 affinity to the victim (e.g., zombies), no penalty occurs.
        """
        try:
            if aggressor is None or victim is None:
                return
            if aggressor is self:
                return  # don't judge yourself here
            if self is victim:
                return  # victim doesn't "witness" their own harm
            if not getattr(self, "is_alive", True):
                return

            # How much the witness cares about the victim (0..1)
            try:
                vic_friend = float(self.friendship_with(victim))
            except Exception:
                vic_friend = 5.0
            affinity = max(0.0, min(1.0, vic_friend / 10.0))

            # Base penalty from severity (same structure as before)
            # e.g. severity 1.0 -> base_from_sev = 5
            base_from_sev = 1 + int(round(4 * float(severity)))

            # IMPORTANT CHANGE:
            # Previously: +3 flat if killed, even when affinity=0.
            # Now: kill bonus is scaled by affinity too.
            kill_bonus = 3 if killed else 0

            penalty = int(round(base_from_sev * affinity)) + int(round(kill_bonus * affinity))

            # If witness didn't like the victim, dampen the penalty (keep your old behavior)
            if vic_friend < 2:
                penalty = max(0, penalty - 2)

            if penalty <= 0:
                return

            try:
                cur = int(self.friendship_with(aggressor))
            except Exception:
                cur = 5

            new_val = max(0, min(10, cur - penalty))
            self.set_friendship_with(aggressor, new_val)

        except Exception as ex:
            print("[WITNESS_VIOLENCE][ERROR]", ex)
            return


    # ---------- Damage ----------
    def calculate_damage(self) -> int:
        """
        Prefer equipped hand items (max of left/right). Fall back to legacy self.weapon; else 5.
        (You can later blend stats like strength/skill here.)
        """
        rh = self.equipment.get("right_hand")
        lh = self.equipment.get("left_hand")
        best_hand = 0
        if rh:
            best_hand = max(best_hand, int(getattr(rh, "damage", 0)))
        if lh:
            best_hand = max(best_hand, int(getattr(lh, "damage", 0)))
        if best_hand > 0:
            return best_hand
        if self.weapon:
            return max(0, int(getattr(self.weapon, "damage", 0)))
        return 5  # Default unarmed damage

    # ---------- Dialogue feeling ----------
    def get_opinion(self, speaker: 'Character', topic: str) -> str:
        descriptors = []

        if self.openness >= 7:
            descriptors.append("curious")
        elif self.openness <= 3:
            descriptors.append("reserved")

        if self.conscientiousness >= 7:
            descriptors.append("thoughtful")
        elif self.conscientiousness <= 3:
            descriptors.append("impulsive")

        if self.extraversion >= 7:
            descriptors.append("talkative")
        elif self.extraversion <= 3:
            descriptors.append("quiet")

        if self.agreeableness >= 7:
            descriptors.append("friendly")
        elif self.agreeableness <= 3:
            descriptors.append("grumpy")

        if self.neuroticism >= 7:
            descriptors.append("anxious")
        elif self.neuroticism <= 3:
            descriptors.append("calm")

        friendship_level = self.friendship_with(speaker)
        if friendship_level >= 7:
            descriptors.append("warm")
        elif friendship_level <= 3:
            descriptors.append("agressively")

        if self.health <= 30:
            descriptors.append("dying")
        elif self.health <= 60:
            descriptors.append("tired")

        if not descriptors:
            return f"{self.name} speaks in a neutral, unreadable tone about {topic}."
        else:
            joined = ", ".join(descriptors)
            return f"{self.name} speaks in a {joined} manner about {topic}."

    # ---------- Knowledge (last-known snapshots; no timestamps) ----------
    def remember(self, entity, *, reason: str = "observe") -> Dict:
        """
        Store a last-known snapshot of an entity’s state AND mark it as known
        in the appropriate knowledge set (items/areas/people). Keeps obj.known_by in sync.
        """
        if isinstance(entity, Item):
            self.known_items.add(entity.uid)
            self._ensure_known_by(entity)
            entity_type = "item"
            snap = self._snapshot_item(entity)
            uid = entity.uid
            name = entity.name

        elif isinstance(entity, Character):
            # We only index *other* people in known_people; you may include self if you want.
            self.known_people.add(entity.uid)
            # keep parity with inform (optional for people, but consistent helps)
            self._ensure_known_by(entity)
            entity_type = "character"
            snap = self._snapshot_character(entity)
            uid = entity.uid
            name = entity.name

        elif isinstance(entity, SubArea):
            self.known_areas.add(entity.uid)
            self._ensure_known_by(entity)
            entity_type = "area"
            snap = self._snapshot_area(entity)
            uid = entity.uid
            name = entity.name

        else:
            entity_type = type(entity).__name__
            uid = getattr(entity, "uid", f"unknown_{id(entity)}")
            name = getattr(entity, "name", entity_type)
            snap = {"repr": repr(entity)}

        entry = {
            "entity_type": entity_type,
            "uid": uid,
            "name": name,
            "reason": reason,
            "snapshot": snap,
        }
        self.knowledge[uid] = entry
        return entry


    def _snapshot_item(self, it: Item) -> Dict:
        abilities = [{"uid": ab.uid, "name": ab.name} for ab in getattr(it, "abilities", [])]
        # Also record which slot (if any) currently uses this item.
        slot = self._slot_of(it)
        return {
            "uid": it.uid,
            "name": it.name,
            "holder_uid": getattr(it.holder, "uid", None),
            "holder_name": getattr(it.holder, "name", None),
            "position_uid": getattr(it.position, "uid", None),
            "position_name": getattr(it.position, "name", None),
            "is_equipped": bool(getattr(it, "is_equipped", False)),
            "equipped_slot": slot,
            "damage": int(getattr(it, "damage", 0)),
            "robustness": int(getattr(it, "robustness", 0)),
            "description": getattr(it, "description", ""),
            "abilities": abilities,
        }

    def _snapshot_character(self, ch: 'Character') -> Dict:
        inv = [{"uid": it.uid, "name": it.name, "equipped": bool(it.is_equipped)} for it in getattr(ch, "inventory", [])]
        party = [{"uid": p.uid, "name": p.name} for p in getattr(ch, "party", [])]
        equipped = {
            "head": getattr(ch.equipment.get("head"), "uid", None),
            "torso": getattr(ch.equipment.get("torso"), "uid", None),
            "legs": getattr(ch.equipment.get("legs"), "uid", None),
            "left_hand": getattr(ch.equipment.get("left_hand"), "uid", None),
            "right_hand": getattr(ch.equipment.get("right_hand"), "uid", None),
            "extra": getattr(ch.equipment.get("extra"), "uid", None),
        }
        return {
            "uid": ch.uid,
            "name": ch.name,
            "health": int(getattr(ch, "health", 0)),
            "is_alive": bool(getattr(ch, "is_alive", True)),
            "current_area_uid": getattr(ch.current_area, "uid", None),
            "current_area_name": getattr(ch.current_area, "name", None),
            "equipped": equipped,
            "stats": {
                "strength": ch.strength,
                "intelligence": ch.intelligence,
                "skill": ch.skill,
                "speed": ch.speed,
                "endurance": ch.endurance,
                "openness": ch.openness,
                "conscientiousness": ch.conscientiousness,
                "extraversion": ch.extraversion,
                "agreeableness": ch.agreeableness,
                "neuroticism": ch.neuroticism,
            },
            "inventory": inv,
            "party": party,
        }

    def _snapshot_area(self, area: SubArea) -> Dict:
        chars = [{"uid": c.uid, "name": c.name, "alive": c.is_alive} for c in getattr(area, "characters", [])]
        items = [{"uid": it.uid, "name": it.name} for it in getattr(area, "key_items", [])]
        links = [{"to_uid": a.uid, "to_name": a.name} for a in area.get_linked_areas()]
        return {
            "uid": area.uid,
            "name": area.name,
            "description": area.description,
            "characters": chars,
            "items_on_floor": items,
            "linked_areas": links,
        }

    def get_known(self, uid_or_entity) -> Optional[Dict]:
        uid = uid_or_entity if isinstance(uid_or_entity, str) else getattr(uid_or_entity, "uid", None)
        if not uid:
            return None
        return self.knowledge.get(uid)

    def diff_known_state(self, entity) -> Tuple[bool, Dict]:
        uid = getattr(entity, "uid", None)
        if not uid or uid not in self.knowledge:
            return (True, {"_reason": "unknown_entity"})
        entry = self.knowledge[uid]
        old = entry.get("snapshot", {})
        if isinstance(entity, Item):
            new = self._snapshot_item(entity)
        elif isinstance(entity, Character):
            new = self._snapshot_character(entity)
        elif isinstance(entity, SubArea):
            new = self._snapshot_area(entity)
        else:
            return (True, {"_reason": "unsupported_entity_type"})

        def _diff(a, b):
            changes = {}
            keys = set(a.keys()) | set(b.keys())
            for k in keys:
                va, vb = a.get(k), b.get(k)
                if isinstance(va, dict) and isinstance(vb, dict):
                    sub = _diff(va, vb)
                    if sub:
                        changes[k] = sub
                elif va != vb:
                    changes[k] = {"was": va, "now": vb}
            return changes

        diff = _diff(old, new)
        return (bool(diff), diff)

    def refresh_known_state(self) -> None:
        # Inventory and party are always part of your "known" state.
        for it in list(self.inventory):
            self.remember(it, reason="possession")

        for mate in list(self.party):
            self.remember(mate, reason="party")

        # Current area + everything obviously in it.
        cur = getattr(self, "current_area", None)
        if cur is not None:
            # Area itself
            self.remember(cur, reason="presence")

            # Everyone sharing your area becomes known.
            try:
                for ch in list(getattr(cur, "characters", [])):
                    if ch is self:
                        continue
                    self.remember(ch, reason="co_present")
            except Exception:
                pass

            # Items lying around in your area become known.
            try:
                for it in list(getattr(cur, "key_items", [])):
                    self.remember(it, reason="in_room")
            except Exception:
                pass

    # ---------- Misc ----------
    def __hash__(self):
        return hash(self.uid)
    
    def _ensure_known_by(self, obj) -> None:
        """Ensure obj.known_by exists and contains self (mirrors your actions.inform behavior)."""
        if obj is None:
            return
        if not hasattr(obj, "known_by"):
            setattr(obj, "known_by", [])
        if self not in obj.known_by:
            obj.known_by.append(self)

    @staticmethod
    def _to_uid(x) -> Optional[str]:
        if x is None:
            return None
        if isinstance(x, str):
            return x
        return getattr(x, "uid", None)

    # ---------- Learn ----------
    def learn_item(self, item, *, reason: str = "learn") -> bool:
        uid = self._to_uid(item)
        if not uid:
            return False
        self.known_items.add(uid)
        self._ensure_known_by(item)
        # Keep the rich snapshot too
        try:
            self.remember(item, reason=reason)
        except Exception:
            pass
        return True

    def learn_area(self, area, *, reason: str = "learn") -> bool:
        uid = self._to_uid(area)
        if not uid:
            return False
        self.known_areas.add(uid)
        self._ensure_known_by(area)
        try:
            self.remember(area, reason=reason)
        except Exception:
            pass
        return True

    def learn_person(self, person, *, reason: str = "learn") -> bool:
        uid = self._to_uid(person)
        if not uid:
            return False
        self.known_people.add(uid)
        # We generally do NOT add people’s known_by automatically, but for parity with items/areas
        # (and your inform flow), we will:
        self._ensure_known_by(person)
        try:
            self.remember(person, reason=reason)
        except Exception:
            pass
        return True

    # ---------- Forget ----------
    def forget_item(self, item_or_uid) -> bool:
        uid = self._to_uid(item_or_uid)
        if not uid:
            return False
        self.known_items.discard(uid)
        # Do not mutate obj.known_by here; forgetting is personal.
        return True

    def forget_area(self, area_or_uid) -> bool:
        uid = self._to_uid(area_or_uid)
        if not uid:
            return False
        self.known_areas.discard(uid)
        return True

    def forget_person(self, person_or_uid) -> bool:
        uid = self._to_uid(person_or_uid)
        if not uid:
            return False
        self.known_people.discard(uid)
        return True

    # ---------- Checks ----------
    def knows_item(self, item_or_uid) -> bool:
        uid = self._to_uid(item_or_uid)
        return bool(uid and uid in self.known_items)

    def knows_area(self, area_or_uid) -> bool:
        uid = self._to_uid(area_or_uid)
        return bool(uid and uid in self.known_areas)

    def knows_person(self, person_or_uid) -> bool:
        uid = self._to_uid(person_or_uid)
        return bool(uid and uid in self.known_people)
        
    # ===== Knowledge-gated helpers =====
    def _knows_uid(self, uid: Optional[str]) -> bool:
        """True if this character has any record of a UID in knowledge indices or snapshots."""
        if not uid:
            return False
        if uid in self.known_items or uid in self.known_people or uid in self.known_areas:
            return True
        return uid in getattr(self, "knowledge", {})

    def can_see_area(self, area: 'SubArea') -> bool:
        """Visibility rule for areas (known set, current location, or explicitly known_by)."""
        if area is None:
            return False
        if area is getattr(self, "current_area", None):
            return True
        if self in getattr(area, "known_by", []):
            return True
        return self.knows_area(area) or self._knows_uid(getattr(area, "uid", None))

    def can_see_character(self, c: 'Character') -> bool:
        """Visibility rule for characters (self/party/same room/known)."""
        if c is None:
            return False
        if c is self:
            return True
        if c in getattr(self, "party", []):
            return True
        if getattr(c, "current_area", None) is getattr(self, "current_area", None):
            return True
        return self.knows_person(c) or self._knows_uid(getattr(c, "uid", None))

    def can_see_item(self, it: 'Item') -> bool:
        """Visibility rule for items (in hand/in room/known)."""
        if it is None:
            return False
        if getattr(it, "holder", None) is self:
            return True
        if getattr(it, "position", None) is getattr(self, "current_area", None):
            return True
        if self in getattr(it, "known_by", []):
            return True
        return self.knows_item(it) or self._knows_uid(getattr(it, "uid", None))

    def safe_area_name(self, area: Optional['SubArea']) -> str:
        """Redact unknown area names."""
        if area is None:
            return "Unknown"
        return getattr(area, "name", "Unknown") if self.can_see_area(area) else "Unknown"

    def safe_char_name(self, c: Optional['Character']) -> str:
        """Redact unknown character names."""
        if c is None:
            return "Unknown"
        return getattr(c, "name", "Unknown") if self.can_see_character(c) else "Unknown"

    def known_locations_lines(self, areas: Optional[list] = None) -> str:
        """
        Return lines of known locations:
        'ID: <uid>, Name: <name>'
        Only includes locations visible/known to the player.
        """
        if areas is None:
            try:
                import gameSetup  # local import to avoid cyclic at module import time
                areas = list(getattr(gameSetup.drugstore_world, "sub_areas", []) or [])
            except Exception:
                areas = []
        out = []
        for a in areas:
            if self.can_see_area(a):
                out.append(f"ID: {getattr(a,'uid','')}, Name: {getattr(a,'name','Unknown')}")
        return "\n".join(out) if out else "(none)"

    def known_characters_lines(self, areas: Optional[list] = None) -> str:
        """
        Return lines of known characters:
        'ID: <uid>, Name: <name>, Area: <safe area name>'
        Only includes characters visible/known to the player.
        """
        if areas is None:
            try:
                import gameSetup
                areas = list(getattr(gameSetup.drugstore_world, "sub_areas", []) or [])
            except Exception:
                areas = []
        out = []
        for a in areas:
            for c in getattr(a, "characters", []) or []:
                if self.can_see_character(c):
                    out.append(
                        f"ID: {getattr(c,'uid','')}, Name: {getattr(c,'name','Unknown')}, "
                        f"Area: {self.safe_area_name(getattr(c,'current_area', None))}"
                    )
        return "\n".join(out) if out else "(none)"

    def known_items_lines(self, areas: Optional[list] = None) -> str:
        """
        Return lines of known items:
        'ID: <uid>, Name: <name>, Holder: <safe char name>, Area: <safe area name>'
        Only includes items visible/known to the player.
        """
        if areas is None:
            try:
                import gameSetup
                areas = list(getattr(gameSetup.drugstore_world, "sub_areas", []) or [])
            except Exception:
                areas = []
        out = []
        # Items on the floor
        for a in areas:
            for it in getattr(a, "key_items", []) or []:
                if self.can_see_item(it):
                    holder = getattr(it, "holder", None)
                    pos = getattr(it, "position", None)
                    out.append(
                        f"ID: {getattr(it,'uid','')}, Name: {getattr(it,'name','')}, "
                        f"Holder: {self.safe_char_name(holder) if holder else 'None'}, "
                        f"Area: {self.safe_area_name(pos) if pos else 'None'}"
                    )
            # Items held by characters
            for c in getattr(a, "characters", []) or []:
                for it in getattr(c, "inventory", []) or []:
                    if self.can_see_item(it):
                        pos = getattr(it, "position", None)
                        out.append(
                            f"ID: {getattr(it,'uid','')}, Name: {getattr(it,'name','')}, "
                            f"Holder: {self.safe_char_name(c)}, "
                            f"Area: {self.safe_area_name(pos) if pos else 'None'}"
                        )
        return "\n".join(out) if out else "(none)"


class World:
    def __init__(
        self,
        title: str,
        relation_to_mc: str,
        chaos_state: int = 0,
        current_dilemma: str = "",
        current_goal: str = "",
        *,
        uid: Optional[str] = None,
        map = None
    ):
        self.uid: str = uid or f"World_{title}_{uuid4().hex[:6]}"
        self.title = title
        self.relation_to_mc = relation_to_mc
        self.chaos_state = chaos_state  # Scale of 0 to 10
        self.sub_areas: List[SubArea] = []
        self.current_dilemma = current_dilemma
        self.current_goal = current_goal
        # NEW: persist the grid (list-of-lists of uids/0)
        self.map = map if isinstance(map, list) else []


    def add_sub_area(self, sub_area: SubArea):
        self.sub_areas.append(sub_area)

    def get_sub_area_by_name(self, name: str) -> Optional[SubArea]:
        for area in self.sub_areas:
            if area.name.lower() == name.lower():
                return area
        return None

    def get_sub_area_by_id(self, uid: str) -> Optional[SubArea]:
        for area in self.sub_areas:
            if getattr(area, "uid", None) == uid:
                return area
        return None

    def get_all_characters_summary(self) -> str:
        """
        Returns a summary string for all characters in the world.
        """
        summary_lines = []
        seen = set()
        for sub_area in self.sub_areas:
            for char in sub_area.characters:
                if char in seen:
                    continue
                seen.add(char)
                personality = (f"Openness: {char.openness}, "
                               f"Conscientiousness: {char.conscientiousness}, "
                               f"Extraversion: {char.extraversion}, "
                               f"Agreeableness: {char.agreeableness}, "
                               f"Neuroticism: {char.neuroticism}")
                stats = (f"Strength: {char.strength}, Intelligence: {char.intelligence}, "
                         f"Skill: {char.skill}, Speed: {char.speed}, Endurance: {char.endurance}")
                line = (f"ID: {char.uid}, Name: {char.name}, Health: {char.health}, "
                        f"Location: {char.current_area.name}, Gender: {char.gender}, "
                        f"Personality: ({personality}), Stats: ({stats})")
                summary_lines.append(line)
        return "\n".join(summary_lines)

    def __str__(self) -> str:
        header = (
            f"World Title: {self.title}\n"
            f"Relation to MC: {self.relation_to_mc}\n"
            f"Chaos State: {self.chaos_state}/10\n"
            f"Current Dilemma: {self.current_dilemma}\n"
            f"Current Goal: {self.current_goal}\n"
        )
        sub_area_summary = "Sub-Areas:\n"
        for area in self.sub_areas:
            sub_area_summary += f"  - {area.name} (ID: {area.uid}): {area.description}\n"
        characters_summary = "Characters:\n" + self.get_all_characters_summary()
        return header + "\n" + sub_area_summary + "\n" + characters_summary
