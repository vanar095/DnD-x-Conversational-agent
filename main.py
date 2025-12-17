# main.py

import time
import threading

from InputProcessor import get_story, AIconversation
import gameSetup  # side-effect: builds world, player, events, etc.


# Idle time thresholds (seconds)
IDLE_FIRST_REMINDER = 200      # first nudge
IDLE_SECOND_REMINDER = 500     # second nudge 


def main():
    print("AI DnD – CLI mode. Type 'quit' to exit.\n")

    # shared state between main loop and idle watcher thread
    last_input_time = time.time()
    idle_flags = {
        "first_sent": False,
        "second_sent": False,
        "running": True,
    }

    # --- background thread: watches for idle periods ---
    def idle_watcher():
        nonlocal last_input_time

        while idle_flags["running"]:
            now = time.time()
            elapsed = now - last_input_time

            # First reminder after 30s
            if (not idle_flags["first_sent"]) and elapsed >= IDLE_FIRST_REMINDER:
                try:
                    msg = AIconversation(
                        (
                            "SYSTEM NOTE: The player has been idle for about a minute "
                            "and has not typed anything. Check in warmly, ask if "
                            "everything is alright, and gently remind them they can "
                            "type an action or a question about the game."
                        ),
                        precheck_label="idle_30s",
                    )
                except Exception as ex:
                    msg = f"(Idle check error: {ex})"

                if msg:
                    # print on a new line, then re-show the prompt
                    print(f"\n{msg}\n> ", end="", flush=True)

                idle_flags["first_sent"] = True

            # Second reminder after 90s total
            if (not idle_flags["second_sent"]) and elapsed >= IDLE_SECOND_REMINDER:
                try:
                    msg = AIconversation(
                        (
                            "SYSTEM NOTE: The player has been idle for a longer time "
                            "(around 90 seconds). Gently check in again and offer a "
                            "short recap of their situation or a couple of concrete "
                            "suggestions for what they could do next."
                        ),
                        precheck_label="idle_90s",
                    )
                except Exception as ex:
                    msg = f"(Idle check error: {ex})"

                if msg:
                    print(f"\n{msg}\n> ", end="", flush=True)

                idle_flags["second_sent"] = True

            time.sleep(1.0)

    # start the background watcher
    watcher_thread = threading.Thread(target=idle_watcher, daemon=True)
    watcher_thread.start()

    # --- main input loop ---
    while True:
        user = input("> ")

        # reset idle timers on *any* user input
        last_input_time = time.time()
        idle_flags["first_sent"] = False
        idle_flags["second_sent"] = False

        if user.strip().lower() in {"quit", "exit"}:
            idle_flags["running"] = False
            print("Bye!")
            break

        story_text, game_flag = get_story(user)

        # Just print the story the AI/game produced
        if story_text:
            print("\n" + story_text + "\n")

        # If your get_story uses a 'game over' flag, respect it:
        if game_flag:
            idle_flags["running"] = False
            print("Game over.")
            break


if __name__ == "__main__":
    main()
