from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion

class SlashCommandCompleter(Completer):
    COMMANDS = [
        ('/help', 'Show help'),
        ('/clear', 'Clear screen'),
        ('/quit', 'Exit'),
        ('/memory', 'Agent memory'),
        ('/files', 'Changed files'),
        ('/diff', 'Git diff'),
        ('/branch', 'Git branch'),
        ('/tests', 'Run tests'),
        ('/undo', 'Revert previous write'),
    ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith('/'):
            word = document.get_word_before_cursor(WORD=True)
            for cmd, desc in self.COMMANDS:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text), display_meta=desc)

print("Completer parses successfully.")
