# WiFi USB Monitor

Real-time WiFi USB adapter monitor for Windows 11. Tracks connection status, signal strength, latency, speed, and disconnection causes.

## Features

- **Connection monitoring** — real-time status of WiFi USB adapter
- **Signal strength** — RSSI tracking with history graph
- **Latency monitoring** — ping to multiple hosts (8.8.8.8, 1.1.1.1, 9.9.9.9)
- **Speed test** — download/upload speed measurement
- **Disconnection analysis** — logs and categorizes disconnect causes
- **Dark industrial theme** — modern UI with real-time charts
- **Event log** — detailed history of all network events

## Requirements

- Windows 11 (or Windows 10)
- Python 3.8+
- WiFi USB adapter

## Usage

```bash
python wifi_monitor.py
```

The monitor starts tracking immediately and displays:
- Adapter status and signal strength
- Latency graph (last 60 measurements)
- Speed history
- Event log with timestamps

### Log File

Events are logged to `wifi_monitor_log.txt` on your Desktop.

## License

MIT License — see [LICENSE](LICENSE) for details.
