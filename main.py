import os
import time
import logging
from pathlib import Path
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt
from rich.progress import track
from rich.table import Table
from utils.handlers.injection_handler import inject_dll, launch_cmd_and_get_pid
from utils.handlers.symbol_resolver import resolve_cmd_symbol_rvas
from utils.monitors.monitor import CmdLogMonitor
from utils.ui.ui import console


def default_hook_dll_path() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "bin", "cmdtest.dll"))


def launch_cmd_and_inject(
    dll_path: str, debugger_enabled: bool = False, debug_dir: str | None = None
):

    logging.info(f"Launching cmd.exe...")

    try:
        cmd_env = os.environ.copy()
        if debugger_enabled:
            cmd_env["EXORCISM_DEBUGGER"] = "1"
            if debug_dir:
                cmd_env["EXORCISM_DEBUG_DIR"] = debug_dir
        resolved_rvas = resolve_cmd_symbol_rvas(
            ["FindFixAndRun", "Dispatch", "ePipe", "eComSep", "eAnd", "eOr"]
        )
        find_fix_and_run_rva = resolved_rvas.get("FindFixAndRun")
        dispatch_rva = resolved_rvas.get("Dispatch")
        epipe_rva = resolved_rvas.get("ePipe")
        ecomsep_rva = resolved_rvas.get("eComSep")
        eand_rva = resolved_rvas.get("eAnd")
        eor_rva = resolved_rvas.get("eOr")
        if find_fix_and_run_rva is not None:
            cmd_env["EXORCISM_FIND_FIX_AND_RUN_RVA"] = f"0x{find_fix_and_run_rva:X}"
            logging.info(f"Resolved FindFixAndRun RVA: 0x{find_fix_and_run_rva:X}")
        else:
            logging.warning("Could not resolve FindFixAndRun RVA; using DLL fallback")
        if dispatch_rva is not None:
            cmd_env["EXORCISM_DISPATCH_RVA"] = f"0x{dispatch_rva:X}"
            logging.info(f"Resolved Dispatch RVA: 0x{dispatch_rva:X}")
        else:
            logging.warning("Could not resolve Dispatch RVA; piped internals may be missed")
        if epipe_rva is not None:
            cmd_env["EXORCISM_EPIPE_RVA"] = f"0x{epipe_rva:X}"
            logging.info(f"Resolved ePipe RVA: 0x{epipe_rva:X}")
        else:
            logging.warning("Could not resolve ePipe RVA; pipe groups may be split")
        if ecomsep_rva is not None:
            cmd_env["EXORCISM_ECOMSEP_RVA"] = f"0x{ecomsep_rva:X}"
            logging.info(f"Resolved eComSep RVA: 0x{ecomsep_rva:X}")
        if eand_rva is not None:
            cmd_env["EXORCISM_EAND_RVA"] = f"0x{eand_rva:X}"
            logging.info(f"Resolved eAnd RVA: 0x{eand_rva:X}")
        if eor_rva is not None:
            cmd_env["EXORCISM_EOR_RVA"] = f"0x{eor_rva:X}"
            logging.info(f"Resolved eOr RVA: 0x{eor_rva:X}")

        process, pid = launch_cmd_and_get_pid(env=cmd_env)

        if process is None or pid is None:
            logging.error("Failed to launch cmd.exe")
            return False, None

        logging.info(f"CMD started with PID: {pid}")

        logging.info("Injecting DLL...")
        if inject_dll(dll_path, process_id=pid):
            logging.info("DLL injection successful!")
            time.sleep(1)
            return True, process
        else:
            logging.error("DLL injection failed!")
            process.terminate()
            return False, None

    except Exception as e:
        logging.error(f"Error launching cmd: {e}")
        return False, None


def main():
    """
    Main application entry point.
    """
    # Header panel
    header_text = Text("CMD Hook & Monitor", style="bold cyan")
    subtitle_text = Text("DLL Injection and Command Monitoring Tool", style="dim")

    console.print()
    console.print(
        Panel(f"{header_text}\n{subtitle_text}", style="cyan", padding=(1, 2))
    )

    # Description
    console.print(
        "\n[dim]This tool will inject a DLL into cmd.exe and monitor command activity.[/dim]"
    )
    console.print(
        "[dim]The DLL should write command logs to 'cmd_hook.json' in the current directory.[/dim]\n"
    )

    # Configuration table
    config_table = Table(show_header=False, box=None, padding=(0, 1))
    config_table.add_column("Icon", style="bold")
    config_table.add_column("Setting", style="cyan")
    config_table.add_column("Value", style="white")

    config_table.add_row("🎯", "Target:", "cmd.exe processes")
    config_table.add_row("📁", "Log File:", os.path.join(os.getcwd(), "cmd_hook.json"))
    config_table.add_row("⚡", "Mode:", "Interactive command debugging")

    console.print(
        Panel(config_table, title="[bold]📋 Configuration[/bold]", style="blue")
    )
    console.print()

    default_dll_path = default_hook_dll_path()

    while True:
        dll_path = Prompt.ask(
            "[bold yellow]Enter the full path to the hook DLL[/bold yellow]",
            default=default_dll_path,
        ).strip('"')

        if not dll_path:
            console.print("[warning]Please enter a valid path.[/warning]")
            continue

        if not os.path.exists(dll_path):
            console.print(f"[danger]DLL file not found: {dll_path}[/danger]")
            console.print(
                "[dim]Run build_cmdtest.bat from the repository root, then try again.[/dim]"
            )
            continue

        if not dll_path.lower().endswith(".dll"):
            console.print(f"[danger]File must be a .dll file: {dll_path}[/danger]")
            continue

        # DLL validation info
        dll_table = Table(show_header=False, box=None, padding=(0, 1))
        dll_table.add_column("Label", style="cyan")
        dll_table.add_column("Value", style="white")

        dll_table.add_row("File:", os.path.basename(dll_path))
        dll_table.add_row("Size:", f"{os.path.getsize(dll_path):,} bytes")
        dll_table.add_row("Path:", dll_path)

        console.print(
            Panel(
                dll_table,
                title="[bold green]✅ DLL Validated[/bold green]",
                style="green",
            )
        )
        console.print()
        break

    success = False
    cmd_process = None

    log_file_path = os.path.join(os.getcwd(), "cmd_hook.json")
    debugger_enabled = True
    debug_dir = os.path.join(os.getcwd(), ".exorcism_debug")
    try:
        if os.path.exists(log_file_path):
            os.remove(log_file_path)
            logging.info("Cleaned up previous log file")
        Path(debug_dir).mkdir(parents=True, exist_ok=True)
        for response_file in Path(debug_dir).glob("response_*.txt"):
            response_file.unlink()
        for response_file in Path(debug_dir).glob("response_*.tmp"):
            response_file.unlink()
    except Exception as e:
        logging.warning(f"Could not clean up previous debug state: {e}")

    console.print()
    success, cmd_process = launch_cmd_and_inject(dll_path, debugger_enabled, debug_dir)

    if success:
        console.print()
        logging.info("Starting log monitor...")
        logging.info(f"   Log file: {log_file_path}")
        logging.info(f"   Debug IPC: {debug_dir}")
        console.print("[dim]   Press Ctrl+C to stop debugging...[/dim]")
        console.print()

        monitor = CmdLogMonitor(
            log_file_path,
            debug_dir=debug_dir,
            debugger_enabled=debugger_enabled,
            target_process=cmd_process,
        )
        try:
            monitor.start_monitoring()
        except KeyboardInterrupt:
            console.print()
            logging.info("Monitoring stopped by user")
            monitor.stop_monitoring()
    else:
        console.print()
        logging.error("Failed to launch and inject DLL")
        console.print("[danger]Please check the DLL file and try again.[/danger]")


if __name__ == "__main__":
    main()
