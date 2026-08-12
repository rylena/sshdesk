# Benchmark methodology

`sshdesk-bench` uses a deterministic 1920×1080 synthetic desktop, renders it to
the requested terminal size, computes cell deltas, and measures the exact ANSI
bytes SSHDESK would write to the SSH PTY. It does not include OpenSSH encryption
or network framing overhead.

Choose the same color mode as the deployed terminal when comparing runs:

```bash
sshdesk-bench --duration 60 --columns 100 --rows 30 --color 256
```

The input figure measures terminal escape parsing, not end-to-end X11 or network
latency. For repeatable results keep terminal dimensions, color mode, CPU
governor, Python version, SSH cipher, network path, and desktop workload fixed.
