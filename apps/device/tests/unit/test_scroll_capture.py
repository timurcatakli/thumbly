"""The scroll-and-join loop against a synthetic tall page with fixed chrome."""

import io
import random

from PIL import Image, ImageDraw

from thumbly.application import scroll_capture
from thumbly.domain.errors import ThumblyError
from thumbly.domain.models import Frame, FrameMetadata

W, H = 360, 780  # frame size
HEADER, FOOTER = 90, 70  # fixed chrome rows
PAGE_HEIGHT = 2600  # scrollable content height


BERRY = "rasp" + "berry"  # Kept: the synthetic layouts below are tuned to this word.
WORDS = f"lorem ipsum dolor sit amet {BERRY} drill battery charger review price delivery stock".split()


def document(seed=7, height=PAGE_HEIGHT):
    """A tall content image of text-like lines (real texture, varied content) so overlaps are
    distinctive, with the blank gaps of a web page between paragraphs."""
    rng = random.Random(seed)
    page = Image.new("RGB", (W, height), "white")
    draw = ImageDraw.Draw(page)
    y = 10
    while y < height - 20:
        for _ in range(rng.randint(1, 4)):
            x = rng.randint(10, 60)
            words = " ".join(rng.choice(WORDS) for _ in range(rng.randint(3, 7)))
            color = tuple(rng.randint(0, 120) for _ in range(3))
            draw.text((x, y), words, fill=color, font_size=rng.choice((11, 13, 16)))
            y += rng.randint(16, 24)
        y += rng.randint(20, 90)
    return page


class ScrollingScreen:
    """A phone whose content scrolls by the flicked distance times ``fling`` (iOS momentum,
    default like an iPhone 13) with ``jitter`` as a +/- fraction, until the end."""

    def __init__(self, page, noise=False, fling=5.5, jitter=0.0, seed=3, dynamic=0.0):
        self.page = page
        self.offset = 0
        self.noise = noise
        self.fling, self.jitter = fling, jitter
        self.rng = random.Random(seed)
        self.captures = 0
        self.swipes = []
        self.moves = []  # content offset after each flick: the truth the join is checked against
        # A block covering this share of the content band changes on every render, like an
        # auto-playing video tile or a rotating carousel.
        self.dynamic = dynamic

    def true_shifts(self):
        """Content moved per flick, first page included as 0."""
        offsets = [0, *self.moves]
        return [b - a for a, b in zip(offsets, offsets[1:])]

    def render(self):
        frame = Image.new("RGB", (W, H), "white")
        band = H - HEADER - FOOTER
        frame.paste(self.page.crop((0, self.offset, W, self.offset + band)), (0, HEADER))
        if self.dynamic:
            # A carousel tile: a strip of other content panned sideways by a random amount.
            rows = int(band * self.dynamic)
            pan = self.rng.randint(0, W - 40)
            tile = self.page.crop((0, 0, W, rows)).rotate(180)
            panned = Image.new("RGB", (W, rows), "#f2f2f2")
            panned.paste(tile, (pan - W // 2, 0))
            frame.paste(panned, (0, HEADER + band // 2 - rows // 2))
        draw = ImageDraw.Draw(frame)
        draw.rectangle((0, 0, W, HEADER - 1), fill="#dde6f0")
        draw.text((20, 30), "Search or ask a question", fill="black")
        draw.rectangle((0, H - FOOTER, W, H), fill="#f4f4f4")
        draw.text((30, H - 45), "Home   You   Cart   Menu", fill="black")
        return frame

    async def probe(self):
        return await self.capture(probing=True)

    async def capture(self, probing=False):
        if not probing:
            self.captures += 1
        image = self.render()
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85 if self.noise else 95)
        metadata = FrameMetadata(
            geometry_epoch="g" * 24,
            width=W,
            height=H,
            native_width=W,
            native_height=H,
            content_rect=(0, 0, W, H),
            received_at=0.0,
            request_seconds=0.0,
            media_type="image/jpeg",
        )
        return Frame(metadata, buffer.getvalue())

    async def swipe(self, metadata, y_from, y_to):
        self.swipes.append((y_from, y_to))
        band = H - HEADER - FOOTER
        factor = self.fling * (1 + self.rng.uniform(-self.jitter, self.jitter))
        distance = round((y_from - y_to) * H * factor)
        self.offset = max(0, min(self.offset + distance, self.page.height - band))
        self.moves.append(self.offset)


async def test_full_page_stitches_until_the_end():
    screen = ScrollingScreen(document(), noise=True)
    result = await scroll_capture.capture_full_page(
        screen.capture, screen.swipe, screen.probe, max_pages=12, settle=0, do_stitch=True, lag_max=0.3
    )
    assert result.stopped == "end"
    assert sum(page.matched for page in result.pages) >= len(result.pages) - 1
    joined_within(result, screen, 15)
    # Chrome found near the truth: blank rows next to it may land on either side.
    assert -50 <= result.header - HEADER <= 150 and -30 <= result.footer - FOOTER <= 120
    # Every drag after the first stays inside the scrolling band.
    for y_from, y_to in screen.swipes[1:]:
        assert HEADER / H < y_to < y_from < (H - FOOTER) / H
    band = H - HEADER - FOOTER
    expected_height = H + (PAGE_HEIGHT - band)
    assert result.stitched is not None
    assert abs(result.stitched.height - expected_height) <= 10  # a px per seam is fine
    # The joined image reproduces the document: compare a strip from the middle.
    middle = result.stitched.crop((0, HEADER + 1000, W, HEADER + 1200)).convert("L")
    truth = screen.page.crop((0, 1000, W, 1200)).convert("L")
    assert scroll_capture.strip_score(middle, truth, 0, 0, 200, trim=0) < 12  # a px of seam drift is fine
    # The current-screen reference is the last capture, and it was not counted twice.
    assert result.current.frame_id != result.pages[-1].metadata.frame_id
    assert len(result.pages) == screen.captures - 1
    assert len(result.lags) == len(result.pages) and max(result.lags[:-1]) < 0.2


async def test_page_budget_and_single_screen():
    screen = ScrollingScreen(document())
    budget = await scroll_capture.capture_full_page(
        screen.capture, screen.swipe, max_pages=3, settle=0, do_stitch=False, lag_max=0.3
    )
    assert budget.stopped == "pages" and len(budget.pages) == 3 and budget.stitched is None
    assert budget.current.frame_id == budget.pages[-1].metadata.frame_id

    static = ScrollingScreen(document().crop((0, 0, W, H - HEADER - FOOTER)))
    single = await scroll_capture.capture_full_page(
        static.capture, static.swipe, max_pages=5, settle=0, do_stitch=True, lag_max=0.3
    )
    assert single.stopped == "end" and len(single.pages) == 1
    assert single.stitched is not None and single.stitched.size == (W, H)
    assert static.swipes == [scroll_capture.FIRST_SWIPE, *scroll_capture.ALT_SWIPES]


def test_match_shift_prefers_the_expected_offset_on_blank_overlap():
    blank = Image.new("L", (W, H), 255)
    match = scroll_capture.match_shift(blank, blank, expected=300)
    assert match.matched and abs(match.shift - 300) <= 1 and match.moved == 0
    different = Image.effect_noise((W, H), 80).convert("L")
    assert not scroll_capture.match_shift(blank, different, expected=300).matched


async def test_drag_cut_short_by_the_worker_is_retried():
    screen = ScrollingScreen(document())
    failures = {"left": 1}
    good_swipe = screen.swipe

    async def flaky_swipe(metadata, y_from, y_to):
        if failures["left"]:
            failures["left"] -= 1
            screen.swipes.append((y_from, y_to))
            raise ThumblyError("INPUT_EXPIRED")
        await good_swipe(metadata, y_from, y_to)

    result = await scroll_capture.capture_full_page(
        screen.capture, flaky_swipe, screen.probe, max_pages=3, settle=0, do_stitch=False, lag_max=0.3
    )
    assert result.stopped == "pages" and len(result.pages) == 3
    assert result.swipes == 3 and result.notes[0] == "drag 1: INPUT_EXPIRED"
    joined_within(result, screen, 15)

    async def dead_swipe(metadata, y_from, y_to):
        raise ThumblyError("INPUT_INTERRUPTED")

    static = ScrollingScreen(document())
    dead = await scroll_capture.capture_full_page(
        static.capture, dead_swipe, static.probe, max_pages=3, settle=0, do_stitch=True, lag_max=0.3
    )
    assert dead.stopped == "input" and len(dead.pages) == 1 and dead.stitched is not None
    assert len(dead.notes) == scroll_capture.INPUT_RETRIES + 2  # each failed drag, plus the duplicate note


async def test_flinging_screen_still_overlaps_and_stitches():
    """The content moves only 1.9x the flick (a sluggish scroll view) with 25 % jitter: the
    learned gain lengthens the jumps, every page keeps overlapping and matches."""
    screen = ScrollingScreen(document(seed=11, height=2400), noise=True, fling=1.9, jitter=0.25)
    result = await scroll_capture.capture_full_page(
        screen.capture, screen.swipe, screen.probe, max_pages=20, settle=0, do_stitch=True, lag_max=0.3
    )
    assert result.stopped == "end"
    misses = [i for i, page in enumerate(result.pages) if not page.matched]
    assert len(misses) <= 1, result.notes
    # A proven join is within a few px; an estimated one (jittery fling) within a third of the band.
    joined_within(result, screen, 220)
    # Jumps grow once the weak fling is known, but never past a quarter of the screen.
    first = screen.swipes[0][0] - screen.swipes[0][1]
    later = [a - b for a, b in screen.swipes[2:-1]]
    assert later and max(later) > first and max(later) <= 0.26
    band = H - HEADER - FOOTER
    assert result.stitched is not None
    assert abs(result.stitched.height - (H + 2400 - band)) <= 40 + (band if misses else 0)


async def test_a_carousel_at_the_bottom_does_not_end_the_capture():
    """Drags that start on the bottom third of the screen scroll nothing (a horizontal carousel
    sits there); the loop retries from the middle and still reaches the end."""
    screen = ScrollingScreen(document())
    real_swipe = screen.swipe

    async def carousel_swipe(metadata, y_from, y_to):
        if 0.5 < y_from < 0.7:  # the default rows land on it; the retry rows do not
            screen.swipes.append((y_from, y_to))
            return
        await real_swipe(metadata, y_from, y_to)

    result = await scroll_capture.capture_full_page(
        screen.capture, carousel_swipe, screen.probe, max_pages=20, settle=0, do_stitch=False, lag_max=0.3
    )
    assert result.stopped == "end" and len(result.pages) >= 5
    joined_within(result, screen, 15)
    assert any("trying another row" in note for note in result.notes)


async def test_moving_tiles_do_not_break_the_join():
    """A video tile covering 15 % of the band changes every frame: pages still match, some
    weakly, the end is still found, and the join stays close to the truth."""
    screen = ScrollingScreen(document(seed=5), noise=True, dynamic=0.15)
    result = await scroll_capture.capture_full_page(
        screen.capture, screen.swipe, screen.probe, max_pages=20, settle=0, do_stitch=True, lag_max=0.3
    )
    assert result.stopped == "end" and len(result.pages) >= 3, result.notes
    joined_within(result, screen, 120)
    band = H - HEADER - FOOTER
    assert result.stitched is not None
    assert abs(result.stitched.height - (H + PAGE_HEIGHT - band)) <= 200


async def test_unproven_overlap_joins_at_the_estimated_shift():
    """When one frame is unrelated content, the page is joined at the gain-predicted shift,
    never at the whole band."""
    screen = ScrollingScreen(document(), noise=True)
    real_render = screen.render
    state = {"garble": False}

    def render():
        frame = real_render()
        if state["garble"]:
            frame = Image.effect_noise((W, H), 120).convert("RGB")
            ImageDraw.Draw(frame).rectangle((0, 0, W, HEADER - 1), fill="#dde6f0")
        return frame

    setattr(screen, "render", render)
    real_swipe = screen.swipe

    async def swipe(metadata, y_from, y_to):
        await real_swipe(metadata, y_from, y_to)
        state["garble"] = len(screen.swipes) == 2  # only the second page is garbled

    result = await scroll_capture.capture_full_page(
        screen.capture, swipe, screen.probe, max_pages=4, settle=0, do_stitch=False, lag_max=0.3
    )
    band = H - HEADER - FOOTER
    # The garbled frame breaks both pairs it takes part in.
    unproven = [page for page in result.pages if not page.matched]
    assert 2 <= len(unproven) <= 3, result.notes
    jump = (screen.swipes[1][0] - screen.swipes[1][1]) * H
    for page in unproven:
        assert 0.5 * jump * 5.5 <= page.shift <= min(band, 1.5 * jump * 5.5)
        assert page.shift < band
    assert any("overlap unproven" in note for note in result.notes)


def joined_within(result, screen, tolerance):
    """Every page's join (matched or estimated) is within ``tolerance`` px of the true scroll.
    Retried flicks do not add pages, so compare against the last move before each capture."""
    shifts = [page.shift for page in result.pages[1:]]
    truth = screen.true_shifts()
    assert len(truth) >= len(shifts), (truth, shifts)
    # Pages map onto the moves that produced them; extra moves at the end belong to the
    # end-detection flicks and to retries that moved nothing.
    nonzero = [t for t in truth if t]
    assert len(nonzero) >= len(shifts), (nonzero, shifts)
    errors = [abs(a - b) for a, b in zip(shifts, nonzero)]
    assert max(errors, default=0) <= tolerance, list(zip(shifts, nonzero))
