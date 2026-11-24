from momentum.check import run_check
from momentum.settings import DEFAULTS


def test_self_check_passes_in_demo_and_never_sends(tmp_path):
    lines = []
    assert run_check({**DEFAULTS, "data_dir": str(tmp_path)}, out=lines.append)
    assert lines[-1] == "CHECK PASSED: nothing was sent."
    assert any("composer ready" in line for line in lines)


def test_self_check_fails_when_report_fails(tmp_path):
    class Broken:
        def __init__(self, config): self.config = config
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def login(self, stop): return True
        def leads(self): raise RuntimeError("report down")
        def templates(self, leads): return []
        def send(self, message): raise AssertionError("must never send")

    lines = []
    assert not run_check({**DEFAULTS, "data_dir": str(tmp_path)}, factory=Broken, out=lines.append)
    assert any("FAIL  Read report" in line for line in lines)
    assert lines[-1].startswith("CHECK FAILED")
