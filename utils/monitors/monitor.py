import time
import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from rich.prompt import Prompt
from utils.ui.ui import console


class CmdLogMonitor:
    DEBUGGER_ACTIONS = (
        ("c", "continue", "Run this command once."),
        ("s", "skip", "Do not run this command; return success to cmd.exe."),
        ("e", "edit", "Replace this command line before it runs."),
        ("a", "auto-continue", "Run this command now and every exact future match."),
        ("k", "auto-skip", "Skip this command now and every exact future match."),
        ("i", "ignore repeats", "Run this command now; silently continue exact repeats."),
        ("r", "ignore root", "Run this command now; silently continue this root command."),
        ("h", "help", "Print this help again without resuming execution."),
    )

    # all built in cmd commands
    # https://blog.brainasoft.com/all-internal-commands-of-cmd/
    BUILTIN_COMMANDS = {
        "assoc",
        "call",
        "cd",
        "cls",
        "color",
        "copy",
        "date",
        "del",
        "dir",
        "echo",
        "endlocal",
        "erase",
        "exit",
        "for",
        "ftype",
        "goto",
        "if",
        "md",
        "mklink",
        "move",
        "path",
        "pause",
        "popd",
        "prompt",
        "pushd",
        "rem",
        "ren",
        "rd",
        "set",
        "setlocal",
        "shift",
        "start",
        "time",
        "title",
        "type",
        "ver",
        "verify",
        "vol",
    }

    def __init__(
        self,
        log_file_path: str,
        debug_dir: Optional[str] = None,
        debugger_enabled: bool = False,
        target_process=None,
    ):
        self.log_file_path = log_file_path
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.debugger_enabled = debugger_enabled
        self.target_process = target_process
        self.last_position = 0
        self.should_stop = False
        self.command_count = 0
        self.hook_status = "Unknown"
        self.seen_commands = {}
        self.auto_continue = {
            "set clink_dummy_capture_env=",
            "echo 0",
        }
        self.auto_skip = set()
        self.root_auto_continue = set()
        self.target_was_terminated = False

    def _read_new_content(self):
        """Read only new content from the log file."""
        if not os.path.exists(self.log_file_path):
            return []

        try:
            with open(self.log_file_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(self.last_position)
                new_content = f.read()
                # update position after reading
                self.last_position = f.tell()

                if new_content:
                    # split into lines and get rid of any empty ones
                    lines = [
                        line.strip()
                        for line in new_content.splitlines()
                        if line.strip()
                    ]
                    return lines
                return []

        except Exception as e:
            logging.error(f"Error reading log file: {e}")
            return []

    def _is_builtin_command(self, command: str) -> bool:
        """Check if a command is a built-in CMD command."""
        if not command:
            return False

        cmd_name = command.lower().strip()

        if cmd_name.startswith("echo") or cmd_name.startswith("rem"):
            return True

        return cmd_name in self.BUILTIN_COMMANDS

    def _colorize_command(self, command: str) -> str:
        if self._is_builtin_command(command):
            return f"[success]{command}[/success]"
        else:
            return f"[danger]{command}[/danger]"

    def _command_key(self, command: str, arguments: str = "") -> str:
        if arguments:
            return f"{command} {arguments.strip()}".strip()
        return command.strip()

    def _root_command(self, command: str) -> str:
        return command.strip().strip('"').lower()

    def _write_debug_response(
        self,
        break_id: int,
        action: str,
        command: str = "",
        arguments: str = "",
    ):
        if self.debug_dir is None:
            return

        self.debug_dir.mkdir(parents=True, exist_ok=True)
        response_path = self.debug_dir / f"response_{break_id}.txt"
        temp_path = self.debug_dir / f"response_{break_id}.tmp"

        lines = [action]
        if action == "edit":
            lines.extend([command, arguments])

        temp_path.write_text("\n".join(lines), encoding="utf-8")
        os.replace(temp_path, response_path)

    def _split_edited_command(self, edited: str):
        edited = edited.strip()
        if not edited:
            return "", ""

        if edited[0] == '"':
            closing_quote = edited.find('"', 1)
            if closing_quote > 0:
                command = edited[: closing_quote + 1]
                arguments = edited[closing_quote + 1 :]
                return command, arguments

        parts = edited.split(maxsplit=1)
        command = parts[0]
        arguments = f" {parts[1]}" if len(parts) > 1 else ""
        return command, arguments

    def _print_debugger_help(self):
        console.print("[bold cyan]Debugger actions[/bold cyan]")
        for key, name, description in self.DEBUGGER_ACTIONS:
            console.print(f"  [bold]{key}[/bold]  {name:<14} {description}")
        console.print()

    def _ask_debugger_action(self) -> str:
        console.print(
            "[dim]Actions: c=continue, s=skip, e=edit, a=auto-continue, "
            "k=auto-skip, i=ignore repeats, r=ignore root, h=help[/dim]"
        )
        while True:
            choice = Prompt.ask(
                "[bold]Action[/bold]",
                choices=["c", "s", "e", "a", "k", "i", "r", "h"],
                default="c",
            )
            if choice != "h":
                return choice

            self._print_debugger_help()

    def _terminate_target_process(self, reason: str):
        if self.target_process is None or self.target_was_terminated:
            return

        if self.target_process.poll() is not None:
            self.target_was_terminated = True
            return

        self.target_was_terminated = True
        console.print(f"[warning]Terminating cmd.exe: {reason}[/warning]")

        try:
            self.target_process.terminate()
            self.target_process.wait(timeout=2)
        except Exception:
            try:
                self.target_process.kill()
                self.target_process.wait(timeout=2)
            except Exception as e:
                logging.warning(f"Could not terminate cmd.exe after unhook: {e}")

    def _handle_debug_break(self, entry: dict, timestamp: str):
        break_id = entry.get("break_id")
        command = entry.get("command", "")
        arguments = entry.get("arguments", "")
        command_type = entry.get("command_type")
        key = self._command_key(command, arguments)
        root_command = self._root_command(command)
        count = self.seen_commands.get(key, 0) + 1
        self.seen_commands[key] = count

        if root_command in self.root_auto_continue:
            console.print(f"[{timestamp}] [dim]Root auto-continue[/dim]: {key}")
            self._write_debug_response(break_id, "continue")
            return

        if key in self.auto_skip:
            console.print(f"[{timestamp}] [warning]Auto-skip[/warning]: {key}")
            self._write_debug_response(break_id, "skip")
            return

        if key in self.auto_continue or count > 1 and key in self.auto_continue:
            console.print(f"[{timestamp}] [dim]Auto-continue[/dim]: {key}")
            self._write_debug_response(break_id, "continue")
            return

        colored_command = self._colorize_command(command)
        args_display = arguments.strip() if arguments else ""
        full_command_display = (
            f"{colored_command} {args_display}" if args_display else colored_command
        )
        console.print()
        console.print(
            f"[{timestamp}] [bold yellow]Breakpoint #{break_id}[/bold yellow]: "
            f"{full_command_display}"
        )
        if command_type is not None:
            console.print(f"[dim]command_type={command_type}, seen={count}[/dim]")

        choice = self._ask_debugger_action()

        if choice == "s":
            self._write_debug_response(break_id, "skip")
            return

        if choice == "e":
            edited = Prompt.ask("[bold]Edited command line[/bold]", default=key)
            edited_command, edited_arguments = self._split_edited_command(edited)
            if edited_command:
                self._write_debug_response(
                    break_id, "edit", edited_command, edited_arguments
                )
            else:
                self._write_debug_response(break_id, "continue")
            return

        if choice == "a":
            self.auto_continue.add(key)
            console.print(f"[dim]Added auto-continue rule for: {key}[/dim]")
            self._write_debug_response(break_id, "continue")
            return

        if choice == "k":
            self.auto_skip.add(key)
            console.print(f"[dim]Added auto-skip rule for: {key}[/dim]")
            self._write_debug_response(break_id, "skip")
            return

        if choice == "i":
            self.auto_continue.add(key)
            console.print(f"[dim]Future repeats will continue silently: {key}[/dim]")

        if choice == "r":
            self.root_auto_continue.add(root_command)
            console.print(
                f"[dim]Future '{root_command}' commands will continue silently.[/dim]"
            )

        self._write_debug_response(break_id, "continue")

    def _format_and_print_entry(self, line: str):
        try:
            entry = json.loads(line)
            timestamp = datetime.now().strftime("%H:%M:%S")
            event_type = entry.get("event_type", "unknown")

            if event_type == "hook_status":
                message = entry.get("message", "")
                if "initialized successfully" in message:
                    self.hook_status = "Active"
                    console.print(
                        f"[{timestamp}] [success]Hook initialized successfully[/success]"
                    )
                elif "being removed" in message:
                    self.hook_status = "Removed"
                    console.print(
                        f"[{timestamp}] [warning]Hook being removed[/warning]"
                    )
                    self._terminate_target_process("hook was removed")
                    self.should_stop = True
                else:
                    console.print(f"[{timestamp}] Hook status: {message}")

            elif event_type == "command_execution":
                command = entry.get("command", "")
                arguments = entry.get("arguments", "")

                args_display = arguments.strip() if arguments else ""

                colored_command = self._colorize_command(command)

                if args_display:
                    full_command_display = f"{colored_command} {args_display}"
                else:
                    full_command_display = colored_command

                self.command_count += 1

                if not self._is_builtin_command(command):
                    indicator = " [highlight]\\[CUSTOM][/highlight]"
                    console.print(
                        f"[{timestamp}] Command: {full_command_display}{indicator}"
                    )
                else:
                    console.print(f"[{timestamp}] Command: {full_command_display}")

            elif event_type == "debug_break":
                if self.debugger_enabled:
                    self._handle_debug_break(entry, timestamp)
                else:
                    console.print(f"[{timestamp}] [warning]Debug break ignored[/warning]")

            elif event_type == "debug_decision":
                message = entry.get("message", "")
                command = entry.get("command", "")
                arguments = entry.get("arguments", "")
                key = self._command_key(command, arguments)
                console.print(f"[{timestamp}] [info]Decision[/info]: {message} -> {key}")

            elif event_type == "command_skipped":
                command = entry.get("command", "")
                arguments = entry.get("arguments", "")
                key = self._command_key(command, arguments)
                console.print(f"[{timestamp}] [warning]Skipped[/warning]: {key}")

            else:
                console.print(f"[{timestamp}] [info]{event_type}[/info]: {str(entry)}")

        except json.JSONDecodeError:
            timestamp = datetime.now().strftime("%H:%M:%S")
            console.print(f"[{timestamp}] [warning]Raw[/warning]: {line}")

    def start_monitoring(self):
        if not os.path.exists(self.log_file_path):
            open(self.log_file_path, "a").close()
            logging.info("Created log file (DLL may not be injected yet)")

        logging.info(f"Monitoring: {self.log_file_path}")
        if self.debugger_enabled:
            print("Debugger attached. Commands pause before execution.")
            self._print_debugger_help()
        else:
            print("Watching for commands... (Press Ctrl+C to stop)")
        console.print(
            "[success]Green[/success] = Built-in commands, [danger]Red[/danger] = Custom/External commands"
        )
        print()

        if os.path.exists(self.log_file_path):
            try:
                with open(
                    self.log_file_path, "r", encoding="utf-8", errors="ignore"
                ) as f:
                    existing_content = f.read()
                    self.last_position = f.tell()

                    if existing_content:
                        existing_lines = [
                            line.strip()
                            for line in existing_content.splitlines()
                            if line.strip()
                        ]
                        if existing_lines:
                            logging.info(
                                f"Found {len(existing_lines)} existing entries:"
                            )
                            for line in existing_lines:
                                self._format_and_print_entry(line)
            except Exception as e:
                logging.error(f"Error reading existing content: {e}")

        try:
            while not self.should_stop:
                new_lines = self._read_new_content()
                for line in new_lines:
                    self._format_and_print_entry(line)
                    if self.should_stop:
                        break

                time.sleep(0.05 if self.debugger_enabled else 0.5)

        except KeyboardInterrupt:
            print()
            logging.info("Monitoring stopped by user")
        except Exception as e:
            logging.error(f"Error during monitoring: {e}")
        finally:
            self.stop_monitoring()

    def stop_monitoring(self):
        """Stop monitoring the log file."""
        self.should_stop = True

        print()
        logging.info("Final Summary:")
        logging.info(f"  Hook Status: {self.hook_status}")
        logging.info(f"  Total Commands: {self.command_count}")
        logging.info(f"  Log File: {self.log_file_path}")
        logging.info("Monitoring stopped.")
