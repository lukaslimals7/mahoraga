"""Queueing: turns must run off the input thread, in order, while the prompt stays live."""
import sys, time, threading, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mahoraga.agent.cli import Turns


def test_submit_while_busy_queues_and_preserves_order():
    seen, changes = [], []
    gate = threading.Event()

    def run(text):
        seen.append(("start", text))
        gate.wait(2.0)
        seen.append(("end", text))

    t = Turns(run, on_change=changes.append)

    assert t.busy() is False
    t.submit("one")
    deadline = time.time() + 2
    while not seen and time.time() < deadline:      # first turn starts on its own
        time.sleep(0.01)
    assert seen == [("start", "one")], seen
    assert t.busy() is True

    # these arrive while the first turn is still running
    n2 = t.submit("two")        # position 1 = next in line
    n3 = t.submit("three")      # position 2
    assert n2 == 1 and n3 == 2, (n2, n3)
    assert t.pending() == 2

    gate.set()                                      # let everything drain
    deadline = time.time() + 3
    while (t.busy() or t.pending()) and time.time() < deadline:
        time.sleep(0.02)

    assert [s[1] for s in seen] == ["one", "one", "two", "two", "three", "three"], seen
    assert changes[-1] == 0


def test_stop_clears_only_the_queue():
    seen = []
    gate = threading.Event()

    def run(text):
        seen.append(text)
        gate.wait(2.0)

    t = Turns(run)
    t.submit("a")
    while not seen:
        time.sleep(0.01)
    t.submit("b"); t.submit("c")
    dropped = t.clear()
    assert dropped == 2, dropped
    gate.set()
    time.sleep(0.3)
    assert seen == ["a"], seen


def test_worker_survives_a_failing_turn():
    calls = []

    def run(text):
        calls.append(text)
        if text == "boom":
            raise RuntimeError("model died")

    t = Turns(run)
    t.submit("boom")
    t.submit("fine")
    deadline = time.time() + 2
    while (t.busy() or t.pending()) and time.time() < deadline:
        time.sleep(0.02)
    assert calls == ["boom", "fine"], calls


if __name__ == "__main__":
    test_submit_while_busy_queues_and_preserves_order()
    test_stop_clears_only_the_queue()
    test_worker_survives_a_failing_turn()
    print("queue tests passed")
