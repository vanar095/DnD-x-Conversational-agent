# AI DnD (CLI) — Playable Conversational RPG Prototype

This project is a command-line (terminal) role-playing game prototype that combines a small scripted game world with Large Language Models (LLMs) for **intent parsing**, **story narration**, 
and **in-character conversation**. You play by typing natural language commands (e.g., “talk to Clementine”, “search the counter”, “move to the office”). 
The game converts your text into structured actions, applies them to the world state, and then generates a narrated response.

The core loop lives in `main.py`, which calls `get_story()` from `InputProcessor.py`. Internally, a lightweight “precheck” can detect whether your input is a game action, 
a question, an undo request, etc. Depending on that, the system routes your text either through action execution + storytelling, or through conversation mode.

---

## What’s in the repo?

- **`main.py`**: CLI entry point. Prints an intro, accepts player input, and displays game output. Includes idle reminders and a small loading animation while waiting for the model response.
- **`InputProcessor.py`**: The main “brain” of the runtime:
  - Parses your input into actions (possibly multi-step)
  - Validates action sequences
  - Executes actions via `actions.py`
  - Calls the LLM to narrate results (storytelling) or answer questions (conversation)
  - Maintains undo snapshots and correction/confirmation flows
- **`actions.py`**: Implements concrete game mechanics (move, talk, pick up, harm, etc.).
- **`gameSetup.py` / `gameRenderer.py` / `turnHandler.py`**: World setup, character/world objects, and turn handling utilities.
- **`config.py`**: Your model configuration (API keys, base URL, model names, system prompts/messages).

---

## Requirements

- Python **3.10+** recommended
- An LLM provider endpoint (OpenRouter/Azure/etc.) configured in `config.py`
- Packages:
  - `openai` (OpenAI-compatible client)
