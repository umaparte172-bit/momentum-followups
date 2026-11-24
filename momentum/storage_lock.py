import os
from pathlib import Path


class StorageLock:
    def __init__(self, directory):
        self.path = Path(directory) / "instance.lock"

    def __enter__(self):
        self.file = self.path.open("a+b")
        try:
            self.file.seek(0, 2)
            if self.file.tell() == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Another Momentum process is using this data directory")
        return self

    def __exit__(self, *args):
        self.file.close()
