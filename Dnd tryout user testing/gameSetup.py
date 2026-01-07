# gameSetup.py
"""
World construction for the Drugstore in Macon scenario.
This module provides the already-instantiated world, areas, items, and characters
as module-level globals to preserve compatibility with existing code that expects
e.g. player, drugstore_world, etc.
"""

from typing import List
from gameRenderer import Item, World, SubArea, LinkingPoint, Character, Ability

# --------------------------
# Small helpers
# --------------------------

def get_default_weapon(uid_suffix: str = "") -> Item:
    """Make a per-holder default 'Hands' weapon (not in inventory)."""
    uid = f"Hands{('_' + uid_suffix) if uid_suffix else ''}"
    return Item(
        name="Hands",
        damage=5,
        robustness=100,
        description="Bare hands - default weapon.",
        uid=uid,
    )

def grant_ability_to_character(char: Character, ability: Ability):
    char.abilities.append(ability)
    # Ability class is generic; we link for bookkeeping
    if hasattr(ability, "attach_to"):
        ability.attach_to("character", char.uid)

def grant_ability_to_item(item: Item, ability: Ability):
    item.abilities.append(ability)
    if hasattr(ability, "attach_to"):
        ability.attach_to("item", item.uid)

# --------------------------
# World & Areas (same layout)
# --------------------------

drugstore_world = World(
    title="Drugstore in Macon",
    relation_to_mc=("Family-owned store, my family... I grew up here, though now I barely recognize this place. "
                    "I wonder if my family is still here? My parents, my brother..."),
    chaos_state=5,
    current_dilemma="Surviving the zombie apocalypse.",
    current_goal="Find medical supplies to save Larry.",
    uid="World_DrugstoreMacon",
    map = [
        [0,0,0],
        ["Area_Pharmacy",0,0],
        ["Area_StorageRoom","Area_MainStore","Area_FrontEntrance"],
        [0,0,0],
        [0,0,0],
    ]
)

main_store = SubArea(
    name="Main Store",
    description=("The main area of the drugstore, with shelves and counters. "
                 "Customers used to come here to buy their basic needs. It's not in great shape now. "),
    exit=False,
    uid="Area_MainStore",
)
front_entrance = SubArea(
    name="Front Entrance",
    description=("The area right outside the drugstore. We just ran away from there escaping the dead, "
                 "they're probably still outside right now."),
    exit=True,
    uid="Area_FrontEntrance",
)
storage_room = SubArea(
    name="Storage Room",
    description=("A back room used for storing inventory. The father left all excess stock there, "
                 "doesn't seem like he ever bothered keeping inventory. The place is kept illuminated by a flickering light."),
    exit=False,
    uid="Area_StorageRoom",
)
pharmacy = SubArea(
    name="Pharmacy",
    description=("A back area in which medical supplies were sold. Lee Everett wasn't allowed inside much as a kid. "
                 "It probably has the medicine that is needed."),
    exit=False,
    uid="Area_Pharmacy",
)
far_away = SubArea(
    name="Far away",
    description="Off the map.",
    exit=True,
    uid="Area_FarAway",
)
# For checkEnd() semantic fallback
setattr(far_away, "is_far_away", True)

# Add areas to world (same four playable areas)
drugstore_world.add_sub_area(main_store)
drugstore_world.add_sub_area(front_entrance)
drugstore_world.add_sub_area(storage_room)
drugstore_world.add_sub_area(pharmacy)

# Links (same topology/descriptions)
door_to_front = LinkingPoint(
    description=("Glass doors leading to the outside. There are posters on it, ones that show the products "
                 "that were on sale. Of course, these all outdated."),
    area_a=main_store, area_b=front_entrance
)
door_to_storage = LinkingPoint(
    description=("A door to the storage room. It has a pool of blood in front of it. "),
    area_a=main_store, area_b=storage_room
)
door_to_pharmacy = LinkingPoint(
    description=("A barricaded door blocking access to the pharmacy. Lee Everett's parents never let him in there as a kid. "
                 "Guess even now, they still want to keep him out."),
    area_a=storage_room, area_b=pharmacy
)

main_store.add_linking_point(door_to_front)
main_store.add_linking_point(door_to_storage)
front_entrance.add_linking_point(door_to_front)
storage_room.add_linking_point(door_to_storage)
storage_room.add_linking_point(door_to_pharmacy)
pharmacy.add_linking_point(door_to_pharmacy)

# Seed area knowledge (Lee grew up here; he knows all store areas)
for area in (main_store, front_entrance, storage_room, pharmacy):
    if not hasattr(area, "known_by"):
        area.known_by = []
    # We'll append the player after we instantiate him below.

# --------------------------
# Characters (with new stats)
# --------------------------

player = Character(
    name="Lee Everett",
    description="Protagonist (the player), recently divorced and sent to prison after he murdered his wife's mister.",
    current_area=main_store,
    health=100,
    controllable=True,
    uid="Char_Lee",
    # OCEAN
    openness=6, conscientiousness=6, extraversion=5, agreeableness=6, neuroticism=4,
    # New stats
    strength=6, intelligence=7, skill=6, speed=5, endurance=6,
)
player.inventory = []
player.weapon = get_default_weapon("Lee")
player.state = 'alert'

# Clementine
clementine = Character(
    name="Clementine",
    current_area=main_store,
    health=100,
    description="Little 12-yr old girl abandonned by her parents, saved from her house in Atlanta.",
    uid="Char_Clementine",
    openness=5, conscientiousness=6, agreeableness=9, extraversion=2, neuroticism=5,
    strength=2, intelligence=7, skill=4, speed=5, endurance=4,
)
clementine.inventory = []
clementine.weapon = get_default_weapon("Clementine")
clementine.topics = ["The location of her parents, they must be out there.", "A little bit hungry"]
clementine.state = 'scared'

# Kenny
kenny = Character(
    name="Kenny",
    current_area=main_store,
    health=100,
    description="Father of Duck and married to Katjaa. A fisherman, not the brightest but a real family man.",
    uid="Char_Kenny",
    openness=6, conscientiousness=4, agreeableness=4, extraversion=8, neuroticism=2,
    strength=6, intelligence=4, skill=6, speed=6, endurance=6,
)
kenny.inventory = []
kenny.weapon = get_default_weapon("Kenny")
kenny.topics = [
    "That time Lee and his family just barely survived a zombie attack at Hershel's farm.",
    "Larry had a heart attack just now because he flipped out at his son for being bitten, which turned out false."
]
kenny.state = 'determined'

# Katjaa
katjaa = Character(
    name="Katjaa",
    current_area=main_store,
    health=100,
    description="Wife of Kenny and mother of Duck. Trained as a veterinarian.",
    uid="Char_Katjaa",
    openness=6, conscientiousness=8, agreeableness=6, extraversion=1, neuroticism=8,
    strength=3, intelligence=7, skill=7, speed=4, endurance=5,
)
katjaa.inventory = []
katjaa.weapon = get_default_weapon("Katjaa")
katjaa.state = 'concerned'

# Duck
duck = Character(
    name="Duck",
    current_area=main_store,
    health=100,
    description="Son of Kenny and Katjaa. Full of energy, but not much brain.",
    uid="Char_Duck",
    openness=10, conscientiousness=2, agreeableness=6, extraversion=8, neuroticism=1,
    strength=3, intelligence=2, skill=3, speed=6, endurance=6,
)
duck.inventory = []
duck.weapon = get_default_weapon("Duck")
duck.state = 'oblivious'

# Carley
handgun = Item(name="Handgun", damage=70, robustness=80, description="Carley's personal handgun.", uid="Item_Handgun")
carley = Character(
    name="Carley",
    current_area=main_store,
    health=100,
    description="A reporter capable of good shooting. Knows about Lee's past murder.",
    uid="Char_Carley",
    openness=3, conscientiousness=8, agreeableness=4, extraversion=5, neuroticism=7,
    strength=5, intelligence=6, skill=7, speed=6, endurance=6,
)
# Put handgun in inventory and equip it (right hand by default)
carley.add_item(handgun)
carley.equip(handgun)
carley.state = 'alert'
carley.topics = ["Lee's past conviction for murder"]

# Doug
tools = Item(name="Tools", damage=5, robustness=80, description="Doug's tools for fixing things.", uid="Item_Tools")
doug = Character(
    name="Doug",
    current_area=main_store,
    health=100,
    description="A nerd, good with tools but bad at socializing and fighting.",
    uid="Char_Doug",
    openness=5, conscientiousness=8, agreeableness=8, extraversion=4, neuroticism=6,
    strength=3, intelligence=8, skill=8, speed=4, endurance=4,
)
doug.add_item(tools)
doug.weapon = get_default_weapon("Doug")  # Unarmed by default; tools are handy but not a great weapon
doug.state = 'focused'

# Lilly
lilly = Character(
    name="Lilly",
    current_area=main_store,
    health=100,
    description="Military employed; works at a navy base close to Macon. Daughter of Larry.",
    uid="Char_Lilly",
    openness=1, conscientiousness=9, agreeableness=2, extraversion=6, neuroticism=6,
    strength=5, intelligence=6, skill=6, speed=6, endurance=6,
)
lilly.inventory = []
lilly.weapon = get_default_weapon("Lilly")
lilly.topics = ["Larry needs heart pills fast, cannot move until then", "This drug store is not a permanent solution"]
lilly.state = 'stressed'

# Larry
larry = Character(
    name="Larry",
    current_area=main_store,
    health=10,
    description="Ex-military, extremely strict and protective father of Lilly.",
    uid="Char_Larry",
    openness=1, conscientiousness=4, agreeableness=2, extraversion=7, neuroticism=4,
    strength=7, intelligence=4, skill=5, speed=4, endurance=3,
)
larry.inventory = []
larry.weapon = get_default_weapon("Larry")
larry.state = 'aggressive'

# Zombies
def create_zombie(zuid: str, display_name: str, area: SubArea) -> Character:
    z = Character(
        name=display_name,
        current_area=area,
        health=50,
        controllable=False,
        description="Undead monster out for blood.",
        uid=zuid,
        openness=0, conscientiousness=0, agreeableness=0, extraversion=0, neuroticism=0,
        strength=6, intelligence=1, skill=2, speed=1, endurance=10,
    )
    # Each zombie has its own bite "weapon"
    bite = Item(name="Bite", damage=10, robustness=50, description="A zombie's infectious bite.", uid=f"Item_Bite_{zuid}")
    z.weapon = bite
    z.state = 'attack'
    z.is_alive = True
    z.has_acted = False
    z.hostile = True
    z.friendships = {}
    return z

zombie1 = create_zombie("Char_Zombie1", "Angry Zombie", front_entrance)
zombie2 = create_zombie("Char_Zombie2", "Ugly Zombie", front_entrance)
zombie3 = create_zombie("Char_Zombie3", "Female Zombie", storage_room)
zombies = [zombie1, zombie2, zombie3]

# Friendships (same intent/levels)
player.friendships = {player: 9, kenny: 6, katjaa: 6, duck: 7, carley: 5, doug: 5, lilly: 4, larry: 3,
    zombie1: 0, zombie2: 0, zombie3: 0}

clementine.friendships = {
    player: 9, kenny: 6, katjaa: 6, duck: 7, carley: 5, doug: 5, lilly: 4, larry: 3,
    zombie1: 0, zombie2: 0, zombie3: 0
}
kenny.friendships = {
    player: 7, katjaa: 8, duck: 9, clementine: 6, carley: 5, doug: 5, lilly: 4, larry: 3,
    zombie1: 0, zombie2: 0, zombie3: 0
}
katjaa.friendships = {
    player: 7, kenny: 8, duck: 9, clementine: 6, carley: 5, doug: 5, lilly: 4, larry: 3,
    zombie1: 0, zombie2: 0, zombie3: 0
}
duck.friendships = {
    player: 6, kenny: 9, katjaa: 9, clementine: 7, carley: 5, doug: 5, lilly: 4, larry: 3,
    zombie1: 0, zombie2: 0, zombie3: 0
}
carley.friendships = {
    player: 6, clementine: 5, kenny: 5, katjaa: 5, duck: 5, doug: 6, lilly: 4, larry: 3,
    zombie1: 0, zombie2: 0, zombie3: 0
}
doug.friendships = {
    player: 5, clementine: 5, kenny: 5, katjaa: 5, duck: 5, carley: 6, lilly: 4, larry: 3,
    zombie1: 0, zombie2: 0, zombie3: 0
}
lilly.friendships = {
    player: 4, clementine: 4, kenny: 4, katjaa: 4, duck: 4, carley: 4, doug: 4, larry: 7,
    zombie1: 0, zombie2: 0, zombie3: 0
}
larry.friendships = {
    player: 2, clementine: 3, kenny: 3, katjaa: 3, duck: 3, carley: 3, doug: 3, lilly: 7,
    zombie1: 0, zombie2: 0, zombie3: 0
}
for z in zombies:
    for human in [player, clementine, kenny, katjaa, duck, carley, doug, lilly, larry]:
        z.friendships[human] = 0
# Zombies are neutral among themselves (5)
zombie1.friendships.update({zombie2: 5, zombie3: 5})
zombie2.friendships.update({zombie1: 5, zombie3: 5})
zombie3.friendships.update({zombie1: 5, zombie2: 5})

# Put characters in areas (unchanged)
main_store.characters.extend([clementine, kenny, katjaa, duck, carley, doug, lilly, larry, player])
front_entrance.characters.extend([zombie1, zombie2])
storage_room.characters.extend([zombie3])

# --------------------------
# Items (same placement)
# --------------------------

first_aid_kit = Item(
    name="First Aid Kit",
    damage=0,
    robustness=0,
    description="A kit containing medical supplies.",
    uid="Item_FirstAidKit",
)
pharmacy.key_items.append(first_aid_kit)

fire_axe = Item(
    name="Fire Axe",
    damage=40,
    robustness=80,
    description="A sharp axe useful for combat.",
    uid="Item_FireAxe",
)
storage_room.key_items.append(fire_axe)

flashlight = Item(
    name="Flashlight",
    damage=3,
    robustness=50,
    description="Requires batteries to work.",
    uid="Item_Flashlight",
)
player.add_item(flashlight)  # In Lee’s inventory (not auto-equipped)

# --------------------------
# Clothing / Armor (new; no special effects, except Clem's Hat)
# --------------------------

# Lee — shirt + jeans
lee_shirt = Item(name="Lee's Shirt", damage=0, robustness=10, description="Worn shirt.", uid="Armor_Lee_Torso")
lee_jeans = Item(name="Lee's Jeans", damage=0, robustness=10, description="Sturdy jeans.", uid="Armor_Lee_Legs")
player.add_item(lee_shirt);  player.equip(lee_shirt, slot="torso")
player.add_item(lee_jeans);  player.equip(lee_jeans, slot="legs")

# Clementine — hat (plot armor) + shirt + pants
clem_hat = Item(name="Clementine's Hat", damage=0, robustness=15, description="Beloved D cap.", uid="Armor_Clem_Head")
clem_shirt = Item(name="Clementine's Shirt", damage=0, robustness=8, description="Child's shirt.", uid="Armor_Clem_Torso")
clem_pants = Item(name="Clementine's Pants", damage=0, robustness=8, description="Child's pants.", uid="Armor_Clem_Legs")
clementine.add_item(clem_hat);   clementine.equip(clem_hat, slot="head")
clementine.add_item(clem_shirt); clementine.equip(clem_shirt, slot="torso")
clementine.add_item(clem_pants); clementine.equip(clem_pants, slot="legs")

# Kenny — jacket + jeans
kenny_jacket = Item(name="Kenny's Jacket", damage=0, robustness=12, description="Fisherman's jacket.", uid="Armor_Kenny_Torso")
kenny_jeans  = Item(name="Kenny's Jeans",  damage=0, robustness=10, description="Faded jeans.", uid="Armor_Kenny_Legs")
kenny.add_item(kenny_jacket); kenny.equip(kenny_jacket, slot="torso")
kenny.add_item(kenny_jeans);  kenny.equip(kenny_jeans,  slot="legs")

# Katjaa — coat + pants
katjaa_coat = Item(name="Katjaa's Coat", damage=0, robustness=12, description="Warm coat.", uid="Armor_Katjaa_Torso")
katjaa_pants = Item(name="Katjaa's Pants", damage=0, robustness=10, description="Comfortable pants.", uid="Armor_Katjaa_Legs")
katjaa.add_item(katjaa_coat); katjaa.equip(katjaa_coat, slot="torso")
katjaa.add_item(katjaa_pants); katjaa.equip(katjaa_pants, slot="legs")

# Duck — t-shirt + shorts
duck_tee    = Item(name="Duck's T-Shirt", damage=0, robustness=6, description="Graphic tee.", uid="Armor_Duck_Torso")
duck_shorts = Item(name="Duck's Shorts",  damage=0, robustness=6, description="Knee-length shorts.", uid="Armor_Duck_Legs")
duck.add_item(duck_tee);    duck.equip(duck_tee, slot="torso")
duck.add_item(duck_shorts); duck.equip(duck_shorts, slot="legs")

# Carley — blouse + jeans
carley_blouse = Item(name="Carley's Blouse", damage=0, robustness=10, description="Casual blouse.", uid="Armor_Carley_Torso")
carley_jeans  = Item(name="Carley's Jeans",  damage=0, robustness=10, description="Dark jeans.", uid="Armor_Carley_Legs")
carley.add_item(carley_blouse); carley.equip(carley_blouse, slot="torso")
carley.add_item(carley_jeans);  carley.equip(carley_jeans,  slot="legs")

# Doug — hoodie + pants
doug_hoodie = Item(name="Doug's Hoodie", damage=0, robustness=10, description="Nerdy hoodie.", uid="Armor_Doug_Torso")
doug_pants  = Item(name="Doug's Pants",  damage=0, robustness=10, description="Loose pants.", uid="Armor_Doug_Legs")
doug.add_item(doug_hoodie); doug.equip(doug_hoodie, slot="torso")
doug.add_item(doug_pants);  doug.equip(doug_pants,  slot="legs")

# Lilly — jacket + pants
lilly_jacket = Item(name="Lilly's Jacket", damage=0, robustness=12, description="Military-style jacket.", uid="Armor_Lilly_Torso")
lilly_pants  = Item(name="Lilly's Pants",  damage=0, robustness=10, description="Tough pants.", uid="Armor_Lilly_Legs")
lilly.add_item(lilly_jacket); lilly.equip(lilly_jacket, slot="torso")
lilly.add_item(lilly_pants);  lilly.equip(lilly_pants,  slot="legs")

# Larry — shirt + slacks
larry_shirt  = Item(name="Larry's Shirt",  damage=0, robustness=10, description="Button-up shirt.", uid="Armor_Larry_Torso")
larry_slacks = Item(name="Larry's Slacks", damage=0, robustness=10, description="Plain slacks.",   uid="Armor_Larry_Legs")
larry.add_item(larry_shirt);  larry.equip(larry_shirt,  slot="torso")
larry.add_item(larry_slacks); larry.equip(larry_slacks, slot="legs")

# --------------------------
# Seed initial knowledge so basic interactions aren’t blocked
# --------------------------
for it in (
    first_aid_kit, fire_axe, flashlight, handgun, tools,
    lee_shirt, lee_jeans,
    clem_hat, clem_shirt, clem_pants,
    kenny_jacket, kenny_jeans,
    katjaa_coat, katjaa_pants,
    duck_tee, duck_shorts,
    carley_blouse, carley_jeans,
    doug_hoodie, doug_pants,
    lilly_jacket, lilly_pants,
    larry_shirt, larry_slacks,
):
    if player not in it.known_by:
        it.known_by.append(player)

# Lee knows these places from childhood
for area in (main_store, front_entrance, storage_room, pharmacy):
    if player not in area.known_by:
        area.known_by.append(player)

# --------------------------
# Abilities (generic, attach to chars/items)
# --------------------------

# Character abilities (unchanged set)
ab_teacher = Ability("Teacher", "Former history professor; better at explaining/informing.")
ab_protective = Ability("Protective", "Tends to defend allies in danger.")
grant_ability_to_character(player, ab_teacher)
grant_ability_to_character(player, ab_protective)

ab_small = Ability("SmallAndSneaky", "Can access tight spaces; less noticeable.")
grant_ability_to_character(clementine, ab_small)

ab_driver = Ability("Driver", "Good with vehicles and quick getaways.")
grant_ability_to_character(kenny, ab_driver)

ab_vet = Ability("Veterinarian", "Can tend to wounds and calm the injured.")
grant_ability_to_character(katjaa, ab_vet)

ab_sharpshooter = Ability("Sharpshooter", "Bonus effectiveness with firearms.")
grant_ability_to_character(carley, ab_sharpshooter)

ab_engineer = Ability("Engineer", "Can repair devices and reinforce barricades.")
grant_ability_to_character(doug, ab_engineer)

ab_leader = Ability("Leadership", "Can coordinate group actions.")
grant_ability_to_character(lilly, ab_leader)

ab_heart = Ability("HeartCondition", "Prone to heart issues; needs medication.")
grant_ability_to_character(larry, ab_heart)

ab_undead = Ability("Undead", "Immune to fear; relentless.")
for z in zombies:
    grant_ability_to_character(z, ab_undead)

# Item abilities (existing)
ab_heal = Ability("Medicate", "Can be used to restore health to a character.")
grant_ability_to_item(first_aid_kit, ab_heal)

ab_breach = Ability("BreachBarricade", "Can dismantle barricades/blocked doors when used appropriately.")
grant_ability_to_item(fire_axe, ab_breach)

ab_light = Ability("Illuminate", "Reveals or clarifies things in dark places.")
grant_ability_to_item(flashlight, ab_light)

ab_ranged = Ability("Ranged", "Effective at a distance; loud.")
grant_ability_to_item(handgun, ab_ranged)

ab_tooluse = Ability("Fixer", "Useful for repairing, prying, or quick fixes.")
grant_ability_to_item(tools, ab_tooluse)

ab_infect = Ability("InfectiousBite", "Wounds risk infection; dangerous grapple.")
# Attach to each zombie's bite weapon if present
for z in zombies:
    if z.weapon and isinstance(z.weapon, Item):
        grant_ability_to_item(z.weapon, ab_infect)

# NEW: Clementine's Hat gets Plot Armor (defense +10)
ab_plot = Ability("PlotArmor", "While worn, grants +10 defense (story-protected).")
# If your Ability supports arbitrary fields, keep a param for later mechanics:
try:
    setattr(ab_plot, "effects", {"defense_bonus": 10})
except Exception:
    pass
grant_ability_to_item(clem_hat, ab_plot)

# --------------------------
# Default fallback weapons for non-armed humans
# --------------------------
for char in [clementine, kenny, katjaa, duck, doug, lilly, larry]:
    if char.weapon is None:
        char.weapon = get_default_weapon(char.uid)


# initializing events
try:
    import gameEvents
    # Guard so reloading gameSetup won’t duplicate the blockade
    if not any(isinstance(e, gameEvents.BlockadeEvent) for e in gameEvents.event_manager.active_events):
        gameEvents.event_manager.initialize_events(drugstore_world)
except Exception as e:
    print("[SETUP][WARN] event init failed:", e)

