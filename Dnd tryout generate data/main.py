# run_eval.py
"""
Tiny runner so you can keep your existing project files untouched.

Usage:
  python run_eval.py

Edits to make:
  - In InputProcessor_eval.py set INPUT_XLSX_PATH to your file (Windows path is fine)
  - Ensure config.key is set to your OpenRouter/OpenAI key.
"""
from InputProcessor import get_story

if __name__ == "__main__":
    text, _ = get_story("")
    print(text)
