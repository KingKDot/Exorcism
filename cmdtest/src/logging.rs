use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom, Write};

use serde_json::{Map, Value};

pub fn log_json_entry(
    event_type: &str,
    command: Option<&str>,
    arguments: Option<&str>,
    command_type: Option<i32>,
    message: Option<&str>,
) {
    log_json_entry_with_extra(event_type, command, arguments, command_type, message, &[]);
}

pub fn log_json_entry_with_extra(
    event_type: &str,
    command: Option<&str>,
    arguments: Option<&str>,
    command_type: Option<i32>,
    message: Option<&str>,
    extra_fields: &[(&str, Value)],
) {
    let mut entry = Map::new();
    entry.insert(
        "event_type".to_string(),
        Value::String(event_type.to_string()),
    );

    if let Some(command) = command {
        entry.insert("command".to_string(), Value::String(command.to_string()));
    }

    if let Some(arguments) = arguments {
        entry.insert(
            "arguments".to_string(),
            Value::String(arguments.to_string()),
        );
    }

    if let Some(command_type) = command_type {
        entry.insert("command_type".to_string(), Value::from(command_type));
    }

    if let Some(message) = message {
        entry.insert("message".to_string(), Value::String(message.to_string()));
    }

    for (name, value) in extra_fields {
        entry.insert((*name).to_string(), value.clone());
    }

    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open("cmd_hook.json")
    {
        let mut entries = read_existing_entries(&mut file);
        let value = Value::Object(entry);
        entries.push(value);

        let _ = file.set_len(0);
        let _ = file.seek(SeekFrom::Start(0));
        let _ = serde_json::to_writer_pretty(&mut file, &entries);
        let _ = file.write_all(b"\n");
        let _ = file.flush();
    }
}

fn read_existing_entries(file: &mut std::fs::File) -> Vec<Value> {
    let mut content = String::new();
    if file.seek(SeekFrom::Start(0)).is_err() || file.read_to_string(&mut content).is_err() {
        return Vec::new();
    }

    let trimmed = content.trim();
    if trimmed.is_empty() {
        return Vec::new();
    }

    if let Ok(entries) = serde_json::from_str::<Vec<Value>>(trimmed) {
        return entries;
    }

    trimmed
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line.trim()).ok())
        .collect()
}
