# main.py

import sys
import time
import threading

from InputProcessor import get_story, AIconversation

# Idle time thresholds (seconds)
IDLE_FIRST_REMINDER = 100       # first nudge
IDLE_SECOND_REMINDER = 200      # second nudge


def _read_config_intro() -> str:
    """
    Try to print an intro string from config, but be tolerant to naming.
    Supports either a plain string OR a message-dict like {"role":..., "content":...}.
    """
    try:
        import config

        candidates = [
            "intro",
            "INTRO",
            "intro_text",
            "intro_message",
            "INTRO_MESSAGE",
            "startup_message",
            "STARTUP_MESSAGE",
        ]

        for name in candidates:
            val = getattr(config, name, None)
            if not val:
                continue

            if isinstance(val, dict):
                content = val.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()

            if isinstance(val, str) and val.strip():
                return val.strip()

        return ""
    except Exception:
        return ""


def _call_with_dots(fn, *args, label: str = "", **kwargs):
    """
    Run fn(*args, **kwargs) in a worker thread, while showing a small "..."" animation.
    Animation prints to stderr to reduce interference with your debug stdout prints.
    """
    done = threading.Event()
    out = {"value": None, "err": None}

    def worker():
        try:
            out["value"] = fn(*args, **kwargs)
        except Exception as ex:
            out["err"] = ex
        finally:
            done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    frames = ["   ", ".  ", ".. ", "..."]
    i = 0

    animate = bool(getattr(sys.stderr, "isatty", lambda: False)())

    if animate:
        prefix = (label.strip() + " ") if label.strip() else ""
        while not done.is_set():
            sys.stderr.write("\r" + prefix + frames[i % len(frames)])
            sys.stderr.flush()
            i += 1
            time.sleep(0.25)

        sys.stderr.write("\r" + " " * (len(prefix) + 3) + "\r")
        sys.stderr.flush()
    else:
        done.wait()

    if out["err"] is not None:
        raise out["err"]
    return out["value"]


def main():
    intro = _read_config_intro()
    if intro:
        print(intro + "\n")
    else:
        print("AI DnD – CLI mode. Type 'quit' to exit.\n")

    last_input_time = time.time()
    idle_flags = {
        "first_sent": False,
        "second_sent": False,
        "running": True,
    }

    # True while we're waiting on an LLM call (get_story / AIconversation)
    busy_event = threading.Event()

    def idle_watcher():
        nonlocal last_input_time

        while idle_flags["running"]:
            # IMPORTANT: do NOT send idle checkups while waiting on OpenRouter/LLM
            if busy_event.is_set():
                time.sleep(0.5)
                continue

            now = time.time()
            elapsed = now - last_input_time

            if (not idle_flags["first_sent"]) and elapsed >= IDLE_FIRST_REMINDER:
                try:
                    busy_event.set()
                    msg = AIconversation(
                        (
                            "SYSTEM NOTE: The player has been idle for a while and has not typed anything. "
                            "Check in warmly, ask if everything is alright, and gently remind them they can "
                            "type an action or a question about the game."
                        ),
                        precheck_label="idle_30s",
                    )
                except Exception as ex:
                    msg = f"(Idle check error: {ex})"
                finally:
                    busy_event.clear()

                if msg:
                    print(f"\n{msg}\n> ", end="", flush=True)

                idle_flags["first_sent"] = True

            if (not idle_flags["second_sent"]) and elapsed >= IDLE_SECOND_REMINDER:
                try:
                    busy_event.set()
                    msg = AIconversation(
                        (
                            "SYSTEM NOTE: The player has been idle for a longer time. "
                            "Gently check in again and offer a short recap of their situation "
                            "or a couple of concrete suggestions for what they could do next."
                        ),
                        precheck_label="idle_90s",
                    )
                except Exception as ex:
                    msg = f"(Idle check error: {ex})"
                finally:
                    busy_event.clear()

                if msg:
                    print(f"\n{msg}\n> ", end="", flush=True)

                idle_flags["second_sent"] = True

            time.sleep(1.0)

    watcher_thread = threading.Thread(target=idle_watcher, daemon=True)
    watcher_thread.start()

    while True:
        try:
            user = input("> ")
        except (EOFError, KeyboardInterrupt):
            idle_flags["running"] = False
            print("\nBye!")
            break

        last_input_time = time.time()
        idle_flags["first_sent"] = False
        idle_flags["second_sent"] = False

        if user.strip().lower() in {"quit", "exit"}:
            idle_flags["running"] = False
            print("Bye!")
            break

        try:
            busy_event.set()
            story_text, game_flag = _call_with_dots(get_story, user, label="...")
        except Exception as ex:
            story_text, game_flag = (f"(Error while generating response: {ex})", 0)
        finally:
            busy_event.clear()

        if story_text:
            print("\n" + story_text + "\n")

        if game_flag:
            idle_flags["running"] = False
            print("Game over.")
            break


if __name__ == "__main__":
    main()
