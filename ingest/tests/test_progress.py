import io

from recetario_ingest.progress import ProgressPrinter, _duration


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def printer(live):
    clock, out = Clock(), io.StringIO()
    return ProgressPrinter(out, clock, live=live), clock, out


def test_durations_read_naturally():
    assert [_duration(s) for s in (5, 65, 3725)] == ["5s", "1m05s", "1h02m"]


def test_the_time_left_comes_from_the_units_that_really_ran_not_the_cached_ones():
    progress, clock, out = printer(live=True)
    progress.begin("A Book", "extract", 10)
    for _ in range(4):  # cached units take no time
        progress.step("cached")
    for _ in range(2):  # two real ones, 10 s each
        clock.now += 10
        progress.step("ok", cost_usd=0.01, tokens=500)

    last = out.getvalue().split("\r\x1b[K")[-1]
    assert "6/10" in last and "4 cached" in last and "10.0s each" in last and "~40s left" in last
    assert "$0.0200" in last and "1.0k tokens" in last


def test_a_finished_stage_leaves_a_summary_line_and_names_the_book_once():
    progress, clock, out = printer(live=True)
    progress.begin("A Book", "extract", 2)
    clock.now += 30
    progress.step("ok", 0.5)
    progress.step("failed")
    progress.end()
    progress.begin("A Book", "lines", 1)
    progress.step("ok")
    progress.end()

    text = out.getvalue()
    assert text.count("▸ A Book") == 1
    assert "✓ extract  2/2 · 2 new · 0 cached · 1 FAILED · $0.5000 · 30s" in text


def test_without_a_terminal_it_prints_plain_lines_every_few_units():
    progress, clock, out = printer(live=False)
    progress.begin("A Book", "extract", 25)
    for _ in range(25):
        clock.now += 1
        progress.step("ok")
    progress.end()

    assert "\r" not in out.getvalue()
    assert out.getvalue().count("extract  [") == 2  # at 10 and 20; the end line replaces the last one
