"""Drain owned subprocess output and record uncaught Python failures."""

from __future__ import annotations

import io
import subprocess
import sys
import threading
import traceback
from types import TracebackType
from typing import BinaryIO, TextIO

from .redaction import redact_text
from .store import get_store, request_id


def capture_process_output(
    process: subprocess.Popen[bytes], source: str
) -> tuple[threading.Thread, ...]:
    store = get_store()
    origin = request_id.get()

    def drain(stream: BinaryIO, channel: str) -> None:
        try:
            with stream:
                for line in iter(stream.readline, b""):
                    store.text(
                        "process_output",
                        line.decode("utf-8", errors="replace"),
                        source=source,
                        channel=channel,
                        child_pid=process.pid,
                        request_id=origin,
                    )
            store.emit(
                "process_output_end",
                source=source,
                channel=channel,
                child_pid=process.pid,
                returncode=process.poll(),
                request_id=origin,
            )
        except OSError:
            store.text(
                "process_capture_error",
                traceback.format_exc(),
                source=source,
                child_pid=process.pid,
            )

    threads = []
    for channel, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        if stream is not None:
            thread = threading.Thread(
                target=drain,
                args=(stream, channel),
                daemon=True,
                name=f"diagnostics-{source}-{channel}-{process.pid}",
            )
            thread.start()
            threads.append(thread)
    return tuple(threads)


class ScriptStream(io.TextIOBase):
    """Capture Python script messages while preserving the real terminal for menus."""

    def __init__(self, original: TextIO, source: str, channel: str) -> None:
        self.original = original
        self.source = source
        self.channel = channel
        self.pending = ""

    @property
    # TextIOBase's runtime read-only property is declared as a variable in its stub.
    def encoding(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self.original.encoding

    def fileno(self) -> int:
        return self.original.fileno()

    def isatty(self) -> bool:
        return self.original.isatty()

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self.original.write(redact_text(text))
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            get_store().text(
                "script_output", line, source=self.source, channel=self.channel
            )
        return len(text)

    def flush(self) -> None:
        if self.pending:
            get_store().text(
                "script_output", self.pending, source=self.source, channel=self.channel
            )
            self.pending = ""
        self.original.flush()


def install_exception_logging(source: str) -> None:
    original_hook = sys.excepthook

    def hook(
        kind: type[BaseException], value: BaseException, tb: TracebackType | None
    ) -> None:
        text = "".join(traceback.format_exception(kind, value, tb))
        get_store().text("uncaught_exception", text, source=source)
        original_hook(kind, value, tb)

    sys.excepthook = hook


def install_script_logging(source: str) -> None:
    if not isinstance(sys.stdout, ScriptStream):
        sys.stdout = ScriptStream(sys.stdout, source, "stdout")
    if not isinstance(sys.stderr, ScriptStream):
        sys.stderr = ScriptStream(sys.stderr, source, "stderr")
    install_exception_logging(source)
    get_store().emit("script_start", source=source)
