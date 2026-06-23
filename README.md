# Exorcism - Runtime Windows Batch Deobfuscator

"When there are little demons running around with .bat crypters, you get an exorcism."

![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgreen.svg)
![Language](https://img.shields.io/badge/language-Rust%2FPython-orange.svg)

**Exorcism** is a runtime Windows batch deobfuscator/debugger that uses DLL injection and function hooking to pause, edit, skip, and log batch commands as they are processed by `cmd.exe`.

> [!WARNING]
> **🚨 DEBUGGING IS NOT A SANDBOX! 🚨**
> 
> This tool can pause and skip commands at its current hook point, but the target `cmd.exe` and any analyzed batch file still run on your system. **DO NOT** use this tool on untrusted or malicious batch files unless you are in a completely isolated environment (sandboxed VM, air-gapped system, etc.).
> 
> **Use at your own risk. This tool is intended for security research, malware analysis, and educational purposes only.**

## 🎯 What is Exorcism?

Exorcism hooks into the Windows Command Processor (`cmd.exe`) at runtime to intercept and log batch commands before they are executed. Unlike static analysis tools that can be fooled by obfuscation techniques, Exorcism captures the actual commands as they are processed by the Windows command interpreter.

### Key Features

- **Runtime Analysis**: Captures commands as they are actually processed, bypassing most obfuscation techniques
- **Interactive Debugging**: Pause before execution, continue, skip, edit, or auto-handle repeated commands
- **DLL Injection**: Uses the Rust `detour` crate for reliable function hooking
- **Real-time Monitoring**: Live command logging with JSON output format
- **Safe Memory Access**: Robust pointer validation and memory safety checks
- **Cross-Architecture**: Supports both x86 and x64 processes

## 🏗️ Architecture

The tool consists of two main components:

1. **Hook DLL (`cmdtest.dll`)**: A Rust DLL that hooks the `FindFixAndRun` function in `cmd.exe`
2. **Python Controller (`main.py`)**: A Python script that handles DLL injection and log monitoring

### How It Works

1. The Python controller launches a new `cmd.exe` process
2. The hook DLL is injected into the `cmd.exe` process using DLL injection
3. The DLL hooks the internal `FindFixAndRun` function using the Rust `detour` crate
4. Every command reaches a Python-controlled breakpoint before execution
5. The Python monitor lets you continue, skip, edit, or auto-handle repeated commands
6. Decisions and command activity are logged to `cmd_hook.json`

## 📋 Prerequisites

- Windows 10/11 (x64)
- Rust stable toolchain
- Python 3.7 or higher
- Administrator privileges (required for DLL injection)

## 🚀 Installation

### 1. Clone the Repository

```cmd
git clone https://github.com/YourUsername/Exorcism.git
cd Exorcism
```

### 2. Build the Hook DLL

1. Build and stage the Rust hook DLL:
   ```cmd
   build_cmdtest.bat
   ```
2. The staged DLL will be located at `bin\cmdtest.dll`, which `main.py` uses as its default hook DLL path.

### 3. Install Python Dependencies

```cmd
pip install -r requirements.txt
```

## 📖 Usage

### Basic Usage

1. **Run as Administrator** (required for DLL injection):
   ```cmd
   # Open Command Prompt as Administrator
   python main.py
   ```

2. **Enter the DLL path** when prompted:
   ```
   Enter the full path to the hook DLL [C:\path\to\Exorcism\bin\cmdtest.dll]:
   ```

3. **Execute your batch file** in the monitored cmd.exe window that appears

4. **Choose a debugger action** when each command breaks:
   - `c`: continue once
   - `s`: skip once
   - `e`: edit the command line before it runs
   - `a`: always continue that exact command
   - `k`: always skip that exact command
   - `i`: continue now and silently continue future repeats


### Example Input
```batch
echo Hello World
set VAR=secret_value
if exist file.txt del file.txt
cls
```
### Example Output

The tool logs commands in JSON format to `cmd_hook.json`:

```json
[
  {
    "event_type": "hook_status",
    "message": "FindFixAndRun hook initialized successfully"
  },
  {
    "arguments": " Hello World",
    "break_id": 1,
    "command": "echo",
    "command_type": 0,
    "event_type": "debug_break",
    "message": "Paused before command execution"
  },
  {
    "arguments": " Hello World",
    "break_id": 1,
    "command": "echo",
    "event_type": "debug_decision",
    "message": "continue"
  },
  {
    "arguments": " Hello World",
    "command": "echo",
    "command_type": 0,
    "event_type": "command_execution"
  },
  {
    "arguments": " VAR=secret_value",
    "command": "set",
    "command_type": 0,
    "event_type": "command_execution"
  },
  {
    "command": "cls",
    "command_type": 0,
    "event_type": "command_execution"
  },
  {
    "event_type": "hook_status",
    "message": "FindFixAndRun hook being removed"
  }
]
```

## 🔧 Configuration

### Hook DLL Configuration

`main.py` resolves the `FindFixAndRun` RVA from the matching `cmd.exe` public symbols before launching the monitored shell, then passes it to the hook DLL through `EXORCISM_FIND_FIX_AND_RUN_RVA`. The matching PDB is cached locally under `.symbols/`.

The hook DLL also keeps a fallback RVA:

```rust
const DEFAULT_FIND_FIX_AND_RUN_RVA: usize = 0x116B0;
```

**Note**: This RVA is specific to certain versions of `cmd.exe`. If the hook fails, you may need to:

1. Check that symbol resolution logged a current `FindFixAndRun` RVA when `main.py` started.
2. Use a debugger (x64dbg, IDA Pro) to find the current RVA for `FindFixAndRun` if symbols are unavailable.
3. Set `EXORCISM_FIND_FIX_AND_RUN_RVA=0x...` before running `main.py`, or update the fallback in `cmdtest/src/lib.rs` and rebuild with `build_cmdtest.bat`.

### Python Monitor Configuration

The Python script automatically:
- Cleans up previous log files
- Cleans up previous debugger response files in `.exorcism_debug/`
- Launches `cmd.exe` with DLL injection
- Monitors the JSON log file in real-time
- Writes debugger decisions back to the hook DLL
- Provides a rich terminal interface

## 🛡️ Security Considerations

### For Analysts

- **Always use in isolated environments** when analyzing malicious samples
- Consider using a dedicated analysis VM that can be easily restored
- Monitor network connections and file system changes alongside command logging
- Be aware that some advanced malware may detect the hook and alter behavior

### For Developers

- The current implementation uses hardcoded RVAs which may break with Windows updates
- Consider implementing IAT (Import Address Table) hooking for better compatibility
- Add additional validation for command arguments and redirections
- Implement process monitoring for child processes spawned by batch files

## 🤝 Contributing

Contributions are welcome! Areas for improvement:

- **Better Compatibility**: Implement function name-based hooking instead of RVA (IAT (you can probably just rip it from clink src))
- **Enhanced Logging**: Add support for environment variable expansion logging
- **Process Monitoring**: Track child processes spawned by batch files
- **Network Monitoring**: Integration with network activity monitoring
- **GUI Interface**: Develop a graphical user interface for easier usage

## 📝 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- The Rust `detour` crate for function hooking capabilities
- Windows XP source code leak for cmd.exe internal structure insights
- The security research community for inspiration and guidance

## ⚖️ Legal Disclaimer

This tool is intended for:
- Security research
- Malware analysis in controlled environments  
- Educational purposes
- Legitimate batch file debugging

**Users are solely responsible for compliance with applicable laws and regulations. The authors assume no liability for misuse of this software.**

---

**Remember: The batch file WILL execute! Use appropriate safety measures!**
