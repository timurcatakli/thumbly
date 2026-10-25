"""Scroll a screen page by page and join the pages into one tall image.

The caller asks once; Thumbly flicks the content up and lets iOS scroll it with momentum,
waits for the screen to settle, captures, measures how far the content moved by matching the
overlap between consecutive frames, and stops when the content no longer moves or the page
budget is spent.
Fixed chrome (status bar, search bar, sticky tabs, tab bar) is detected per pair of frames as
the rows that stay put while the rest moves, and is excluded from matching so it appears once
in the joined image: the top from the first page, the bottom from the last.

All image work is Pillow on grayscale copies; nothing here touches the phone directly.
"""

import asyncio
import io
import time
from dataclasses import dataclass, field
from typing import cast

from PIL import Image, ImageChops

from thumbly.domain.errors import ThumblyError
from thumbly.domain.models import FrameMetadata

# Trimmed mean gray difference (0..255) over textured rows under which two overlapping strips
# count as the same content. Real matches score 1-6 (JPEG and HEVC noise on text rows); wrong
# shifts score 20 and up on textured rows, so the bar has room.
MATCH_THRESHOLD = 8.0
# Share of the worst-matching rows left out of a strip's score (chrome, ads, spinners); the
# full-resolution pass drops more, since tiles that move between frames cover more rows there.
TRIM, TRIM_FINE = 0.2, 0.3
# A pair whose best score sits above MATCH_THRESHOLD still counts as matched (weakly) when it is
# at most WEAK_THRESHOLD and forms a clear valley: at most WEAK_VALLEY times the median score of
# all candidate shifts. Auto-playing tiles raise every candidate's score, not only the right one.
WEAK_THRESHOLD, WEAK_VALLEY = 12.0, 0.5
# A weak match must also sit near the shift the learned gain predicts (relative slack).
WEAK_SLACK = 0.5
# Candidates within this much of the best score count as ties, settled toward the predicted
# shift; kept small so the prediction never overrides a clearly better overlap.
TIE_COARSE, TIE_FINE = 0.3, 0.2
# Rows of a frame that stay this close between two consecutive frames are fixed chrome.
CHROME_THRESHOLD = 4.0
# Rows with less horizontal gradient than this (measured TEXTURE_WIDTH pixels wide) are blank
# and carry no evidence either way.
TEXTURE_MIN, TEXTURE_WIDTH = 1.5, 384
# A strip is scored on its textured rows alone when at least this many exist.
MIN_TEXTURED_ROWS = 6
# Fixed chrome caps, as fractions of the frame height, so a plain background that happens to
# look identical in both frames cannot swallow the scrolling area.
HEADER_MAX, FOOTER_MAX = 0.3, 0.25
# A run of changed rows up to this fraction of the height inside otherwise fixed chrome is
# still chrome: the status-bar clock, the stream indicator, content behind a translucent bar.
CHROME_GAP = 0.05
# Candidate shifts are searched on a copy this many pixels wide, then refined at full width.
COARSE_WIDTH = 96
# The matched overlap must be at least this fraction of the scrolling band.
MIN_OVERLAP = 0.15
# A measured shift smaller than this fraction of the band means the content stopped moving.
STOP_SHIFT = 0.02
# Fewer changed rows than this fraction after a drag means the screen does not scroll.
MOVED_MIN = 0.03
# When at least this share of the band's textured rows is unchanged in place, the content did
# not scroll even if a video tile or a carousel kept changing elsewhere.
STATIC_END = 0.5
# A probe counts as changed only when this share of rows differs: a clock or a stream
# indicator flips about three percent, a scroll of any size changes most of the band.
CHANGED_MIN = 0.05
# Each page is one flick: contact, one jump, release. iOS turns the jump into a smooth
# momentum scroll of roughly FLICK_GAIN times the jump (measured 5.5-6x on an iPhone 13, within
# +/-7 % between flicks), and the gain is re-learned page by page from the measured shifts, so
# the jump length is always chosen to leave consecutive pages overlapping. FIRST_SWIPE is the
# first jump, ALT_SWIPE the retry from mid-screen when a flick moved nothing (a carousel or a
# button took the touch). Jumps never start below row 0.85 or end above row 0.15.
FIRST_SWIPE = (0.60, 0.52)
ALT_SWIPES = ((0.72, 0.64), (0.35, 0.27))
# Later flicks start this far down the scrolling band: the middle, away from the carousels
# and buttons that sit at the bottom of feeds.
SWIPE_ROW = 0.55
SWIPE_MARGIN = 0.06
SWIPE_LIMITS = (0.85, 0.15)
# Target shift per page as a fraction of the scrolling band; the rest (30 %) is overlap,
# twice what the matcher needs, so flings a bit stronger than learned still join.
TARGET_SHIFT = 0.7
GAIN_START, GAIN_MIN, GAIN_MAX, GAIN_SMOOTHING = 5.5, 1.0, 12.0, 0.5
# The jump stays between these fractions of the band: below ~0.03 iOS reads a tap, above the
# flick overshoots the band even at a low gain.
FRACTION_MIN, FRACTION_MAX = 0.03, 0.25
# After a page with no overlap the next jump is shortened by raising the assumed gain.
MISS_GAIN = 1.5
# Flick timing: a short rest after contact, the jump as one step, no hold (momentum wanted).
SWIPE_RATE, SWIPE_SECONDS, SWIPE_DWELL, SWIPE_HOLD = 30, 0.03, 0.05, 0.0
# After a drag the screen is polled with small probe frames until it changes (input can reach
# the phone seconds after it was sent) and then until it stops changing; no change within
# LAG_MAX seconds means the drag scrolled nothing.
PROBE_SIZE, PROBE_INTERVAL, LAG_MAX, STABLE_MAX = 256, 0.25, 6.0, 6.0
# A drag the worker cut short (its report watchdog fired while the phone was slow to answer)
# is tried again this many times before the capture stops with what it has.
INPUT_RETRIES = 2
INPUT_ERRORS = {"INPUT_EXPIRED", "INPUT_INTERRUPTED"}


def gray(image: Image.Image) -> Image.Image:
    return image.convert("L")


def coarse(image: Image.Image) -> Image.Image:
    """Width-bounded copy for the candidate search; height keeps the aspect ratio."""
    if image.width <= COARSE_WIDTH:
        return image
    height = max(1, round(image.height * COARSE_WIDTH / image.width))
    return image.resize((COARSE_WIDTH, height), Image.Resampling.BOX)


def _rows(image: Image.Image) -> list[int]:
    column = image.resize((1, image.height), Image.Resampling.BOX)
    data = column.get_flattened_data() if hasattr(column, "get_flattened_data") else column.getdata()
    return cast(list[int], list(data))


def strip_score(
    a: Image.Image,
    b: Image.Image,
    shift: int,
    top: int,
    bottom: int,
    trim: float = TRIM,
    texture_a: list[int] | None = None,
    texture_b: list[int] | None = None,
) -> float:
    """Mean difference between two strips over textured rows only (a row counts when either frame has
    texture there), and the mean of the best (1 - trim) share of them. Blank rows agree at any
    shift and would dilute a wrong one; a translucent bar, an ad or a loading spinner costs
    nothing because its rows are trimmed; a wrong shift still scores high because most
    textured rows disagree."""
    width = min(a.width, b.width)
    one = a.crop((0, top + shift, width, bottom))
    two = b.crop((0, top, width, bottom - shift))
    diffs = _rows(ImageChops.difference(one, two))
    if texture_a is not None and texture_b is not None:
        textured = [
            d
            for i, d in enumerate(diffs)
            if texture_a[top + shift + i] >= TEXTURE_MIN or texture_b[top + i] >= TEXTURE_MIN
        ]
        if len(textured) >= MIN_TEXTURED_ROWS:
            diffs = textured
    rows = sorted(diffs)
    keep = max(1, int(len(rows) * (1 - trim)))
    return sum(rows[:keep]) / keep


def row_profile(a: Image.Image, b: Image.Image) -> list[int]:
    """Per-row mean absolute difference between two same-size frames (one C call: a box
    resize to one column averages each row)."""
    return _rows(ImageChops.difference(a, b))


def row_texture(image: Image.Image, rows: int) -> list[int]:
    """Mean horizontal gradient per coarse row, measured at a width where text and icons keep
    their edges: near zero on blank rows, which say nothing about whether content moved."""
    medium = image.resize((min(image.width, TEXTURE_WIDTH), rows), Image.Resampling.BOX)
    return _rows(ImageChops.difference(medium, ImageChops.offset(medium, 1, 0)))


def changed(a: Image.Image, b: Image.Image) -> bool:
    """Whether two probe frames differ the way a scroll does: well over the share of rows a
    status-bar icon or a badge can flip on its own."""
    if a.size != b.size:
        return True
    profile = row_profile(a, b)
    return sum(1 for d in profile if d >= CHROME_THRESHOLD) / max(1, len(profile)) >= CHANGED_MIN


def _fixed_run(profile: list[int], texture: list[int], cap: int, gap: int) -> int:
    """Rows of fixed chrome from the start of ``profile``: the last textured, unchanged row
    within ``cap`` that no run of more than ``gap`` textured changed rows precedes, plus one.
    Blank rows are neutral: they neither extend the chrome nor count toward the gap."""
    end = 0
    since_fixed = 0
    for index in range(min(cap, len(profile))):
        if texture[index] < TEXTURE_MIN:
            continue
        if profile[index] < CHROME_THRESHOLD:
            end, since_fixed = index + 1, 0
        else:
            since_fixed += 1
            if since_fixed > gap:
                break
    return end


@dataclass
class Match:
    shift: int  # pixels the content moved up between the frames; 0 when it did not move
    difference: float
    matched: bool  # False when no overlap scored below MATCH_THRESHOLD
    header: int = 0  # fixed chrome rows at the top, in frame pixels
    footer: int = 0  # fixed chrome rows at the bottom
    moved: float = 0.0  # fraction of rows that changed at all
    confidence: str = "strong"  # "weak" when accepted as a valley above a raised floor
    static: float = 0.0  # share of textured band rows unchanged in place: ~1 when nothing scrolled


def _best_shift(scores: list[tuple[float, int]], anchor: float, tolerance: float) -> tuple[int, float]:
    best = min(d for d, _ in scores)
    near = [shift for d, shift in scores if d <= best + tolerance]
    return min(near, key=lambda s: abs(s - anchor)), best


def match_shift(previous: Image.Image, current: Image.Image, expected: int | None = None) -> Match:
    """How far the content moved up between two grayscale frames of the same size, and the
    chrome that stayed put.

    On coarse copies: rows unchanged at the same position (allowing short runs of changed
    rows, like a clock) bound the chrome generously; the shift is searched inside that band
    and, among near-ties, the one closest to ``expected`` (the drag distance) wins, which
    settles blank regions that match at any offset. The header is then the first row that
    moved (changed in place, matched under the shift), the footer follows the last moved
    row of the previous frame, and the shift is refined at full resolution inside that
    band."""
    small_prev, small_cur = coarse(previous), coarse(current)
    rows = small_prev.height
    height = previous.height
    scale = height / rows
    same = row_profile(small_prev, small_cur)
    texture = row_texture(current, rows)
    texture_prev = row_texture(previous, rows)
    moved = sum(1 for d in same if d >= CHROME_THRESHOLD) / rows
    gap = max(1, int(rows * CHROME_GAP))
    head_cap, foot_cap = int(rows * HEADER_MAX), int(rows * FOOTER_MAX)
    # Generous bounds from unchanged textured rows; blank rows between them are neutral.
    h1 = _fixed_run(same, texture, head_cap, gap)
    f1 = _fixed_run(same[::-1], texture[::-1], foot_cap, gap)
    band = rows - h1 - f1
    textured_band = [
        r for r in range(h1, rows - f1) if texture[r] >= TEXTURE_MIN or texture_prev[r] >= TEXTURE_MIN
    ]
    # Too few textured rows say nothing about movement; then the other signals decide.
    static = (
        sum(1 for r in textured_band if same[r] < CHROME_THRESHOLD) / len(textured_band)
        if len(textured_band) >= MIN_TEXTURED_ROWS
        else 0.0
    )
    if band <= 2:
        return Match(0, 0.0, False, 0, 0, moved, "strong", static)
    min_overlap = max(2, int(band * MIN_OVERLAP))
    # The coarse search scores every row: at this width misaligned text smears onto blank rows
    # and shows up there, which separates wrong shifts better than textured rows alone would.
    scores = [
        (strip_score(small_prev, small_cur, s, h1, rows - f1), s)
        for s in range(0, max(1, band - min_overlap))
    ]
    anchor = expected / scale if expected is not None else 0
    s_coarse, best = _best_shift(scores, anchor, TIE_COARSE)
    floor = sorted(d for d, _ in scores)[len(scores) // 2]
    # Chrome from the shift: a row that changed in place and matches under the shift has
    # moved, so the header ends at the first moved row of the current frame and the footer
    # starts after the last moved row of the previous frame. Blank rows and rows behind a
    # translucent bar (neither unchanged nor matching) stay on the chrome side.
    header, footer = h1, f1
    if s_coarse > 0:
        shifted = row_profile(
            small_prev.crop((0, s_coarse, small_prev.width, rows)),
            small_cur.crop((0, 0, small_cur.width, rows - s_coarse)),
        )
        # Only textured rows can prove movement: a blank row matches anything under any shift.
        header = next(
            (
                r
                for r in range(min(head_cap, rows - s_coarse))
                if texture[r] >= TEXTURE_MIN and same[r] >= CHROME_THRESHOLD and shifted[r] < CHROME_THRESHOLD
            ),
            h1,
        )
        footer = next(
            (
                rows - 1 - r
                for r in range(rows - 1, max(s_coarse, rows - foot_cap) - 1, -1)
                if texture_prev[r] >= TEXTURE_MIN
                and same[r] >= CHROME_THRESHOLD
                and shifted[r - s_coarse] < CHROME_THRESHOLD
            ),
            f1,
        )
    header_px = min(int(header * scale), int(height * HEADER_MAX))
    footer_px = min(int(footer * scale + scale), int(height * FOOTER_MAX)) if footer else 0
    # Refine at full resolution within one coarse row either side, inside the refined band.
    top, bottom = header_px, height - footer_px
    full_band = bottom - top
    radius = int(scale) + 1
    center = int(round(s_coarse * scale))
    max_shift = full_band - max(2, int(full_band * MIN_OVERLAP))
    if max_shift < 0:
        return Match(0, 0.0, False, header_px, footer_px, moved, "strong", static)
    high = min(center + radius, max_shift)
    low = max(0, min(center - radius, high))
    fine_texture_prev = row_texture(previous, height)
    fine_texture_cur = row_texture(current, height)
    fine = [
        (strip_score(previous, current, s, top, bottom, TRIM_FINE, fine_texture_prev, fine_texture_cur), s)
        for s in range(low, high + 1)
    ]
    shift, best_fine = _best_shift(fine, expected if expected is not None else 0, TIE_FINE)
    strong = best_fine <= MATCH_THRESHOLD
    plausible = expected is None or abs(shift - expected) <= WEAK_SLACK * max(expected, 1)
    weak = not strong and best_fine <= WEAK_THRESHOLD and best <= WEAK_VALLEY * floor and plausible
    return Match(
        shift,
        round(best_fine, 2),
        strong or weak,
        header_px,
        footer_px,
        moved,
        "weak" if weak else "strong",
        static,
    )


@dataclass
class Page:
    metadata: FrameMetadata
    image: bytes  # encoded, as captured; decoded again only when joining
    gray: Image.Image
    shift: int = 0  # rows of new content this page added below the previous one
    matched: bool = True
    difference: float = 0.0
    header: int = 0  # fixed chrome measured against the previous page
    footer: int = 0


@dataclass
class Result:
    pages: list[Page]
    stitched: Image.Image | None
    stopped: str  # "end" (content stopped moving), "pages" (budget spent), "input" (drags kept failing), "geometry"
    current: FrameMetadata  # the screen as it is now, for the caller's next touch
    seconds: float = 0.0
    swipes: int = 0
    notes: list[str] = field(default_factory=list)
    lags: list[float] = field(default_factory=list)  # seconds from each drag to its first visible effect
    cadence: list[dict] = field(default_factory=list)  # per drag: reports, median_interval_ms, max_gap_ms

    @property
    def header(self) -> int:
        return max((p.header for p in self.pages), default=0)

    @property
    def footer(self) -> int:
        return self.pages[-1].footer if self.pages else 0


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image.convert("RGB")


def stitch(pages: list[Page]) -> Image.Image:
    """One tall image: the first page down to the footer, each later page's new rows above the
    footer, then the last page's footer. One footer height for every page (the largest found
    in any pair): the bar at the bottom is the same throughout, and cutting every strip at the
    same row keeps consecutive strips continuous, while a pair that missed the bar (a floating
    Safari pill, a translucent tab bar) cannot leak it into the middle of the image. Pages are
    decoded one at a time."""
    first = decode(pages[0].image)
    width, height = first.size
    footer = max(page.footer for page in pages)
    bottom = height - footer
    total = height + sum(p.shift for p in pages[1:])
    canvas = Image.new("RGB", (width, total), "white")
    canvas.paste(first.crop((0, 0, width, bottom)), (0, 0))
    y = bottom
    last = first
    for page in pages[1:]:
        last = decode(page.image)
        if page.shift <= 0:
            continue
        canvas.paste(last.crop((0, bottom - page.shift, width, bottom)), (0, y))
        y += page.shift
    canvas.paste(last.crop((0, bottom, width, height)), (0, y))
    return canvas


def _advanced(match: Match, band: int) -> bool:
    """Whether a pair of frames shows the content moved on: enough rows changed, the textured
    rows did not simply stay put (a video tile alone is not progress), and a proven shift is
    more than a bounce."""
    if match.moved < MOVED_MIN or match.static >= STATIC_END:
        return False
    return not (match.matched and match.shift <= band * STOP_SHIFT)


async def capture_full_page(
    capture, swipe, probe=None, *, max_pages: int, settle: float, do_stitch: bool, lag_max: float = LAG_MAX
) -> Result:
    """Drive the scroll-and-capture loop.

    ``capture()`` returns a ``Frame`` of the current screen in the output format and size;
    ``probe()`` a small one for change detection (``capture`` when omitted).
    ``swipe(metadata, y_from, y_to)`` drags the content up between normalized frame rows and
    may return the gesture's cadence summary. All are awaited in turn; cancellation propagates
    out of whichever is running.
    """
    started = time.monotonic()
    loop = asyncio.get_running_loop()
    probe = probe or capture

    async def page_from(frame) -> Page:
        image = await loop.run_in_executor(None, lambda: gray(decode(frame.image)))
        return Page(frame.metadata, frame.image, image)

    async def probe_gray() -> Image.Image:
        frame = await probe()
        return await loop.run_in_executor(None, lambda: coarse(gray(decode(frame.image))))

    async def wait_for_effect(before: Image.Image) -> tuple[bool, float]:
        """Poll until the screen differs from ``before`` (bounded by ``lag_max``), then until two
        probes ``settle`` seconds apart agree (bounded by STABLE_MAX). Returns (changed, lag)."""
        t0 = time.monotonic()
        last = await probe_gray()
        while not changed(before, last):
            if time.monotonic() - t0 >= lag_max:
                return False, time.monotonic() - t0
            await asyncio.sleep(PROBE_INTERVAL)
            last = await probe_gray()
        lag = time.monotonic() - t0
        stable_from = time.monotonic()
        while time.monotonic() - stable_from < STABLE_MAX:
            await asyncio.sleep(max(settle, PROBE_INTERVAL))
            current = await probe_gray()
            if not changed(last, current):
                break
            last = current
        return True, lag

    pages = [await page_from(await capture())]
    first = pages[0]
    height = first.gray.height
    stopped = "pages"
    swipes = 0
    notes: list[str] = []
    y_from, y_to = FIRST_SWIPE
    lags: list[float] = []
    cadence: list[dict] = []
    gain = GAIN_START
    # The scrolling band as last measured, as normalized rows; starts as the safe zone.
    band_top, band_bottom = SWIPE_LIMITS[1], SWIPE_LIMITS[0]

    def next_drag() -> tuple[float, float]:
        fraction = min(FRACTION_MAX, max(FRACTION_MIN, TARGET_SHIFT / gain))
        start = min(band_top + SWIPE_ROW * (band_bottom - band_top), SWIPE_LIMITS[0])
        end = max(band_top + SWIPE_MARGIN, SWIPE_LIMITS[1], start - fraction * (band_bottom - band_top))
        return start, end

    while len(pages) < max_pages:
        previous = pages[-1]
        # Probes are compared only with probes: the page image went through a different
        # resize and encode path and differs from them by more than noise.
        before = await probe_gray()
        failures, attempts = 0, 0
        page: Page | None = None
        match: Match | None = None
        moved, lag, dragged = False, 0.0, y_from - y_to
        while True:
            failed = False
            try:
                dragged = y_from - y_to
                sent = await swipe(previous.metadata, y_from, y_to)
                if isinstance(sent, dict) and sent:
                    cadence.append(sent)
            except ThumblyError as exc:
                if exc.code not in INPUT_ERRORS:
                    raise
                failures, failed = failures + 1, True
                notes.append(f"drag {swipes + 1}: {exc.code}")
            swipes += 1
            # Even a cut-short flick may have moved the content; look before trying again.
            moved, lag = await wait_for_effect(before)
            if failed and not moved:
                if failures > INPUT_RETRIES:
                    break
                continue
            if moved:
                page = await page_from(await capture())
                if page.gray.size != first.gray.size:
                    break
                drag_px = max(1.0, dragged * height)
                match = await loop.run_in_executor(
                    None, match_shift, previous.gray, page.gray, int(drag_px * gain)
                )
                page.header, page.footer, page.difference = match.header, match.footer, match.difference
                band = height - match.header - match.footer
                if _advanced(match, band):
                    break
            # Nothing advanced from that row: the bottom, a carousel or a button that took the
            # touch, or a late layout change. Try the other rows before calling it the end.
            attempts += 1
            if attempts > len(ALT_SWIPES):
                break
            notes.append(f"page {len(pages) + 1}: no progress from row {y_from:.2f}; trying another row")
            y_from, y_to = ALT_SWIPES[attempts - 1]
            before = await probe_gray()
        lags.append(round(lag, 2))
        if page is not None and page.gray.size != first.gray.size:
            notes.append("frame size changed; stopped")
            stopped = "geometry"
            break
        if match is None or not _advanced(match, height - match.header - match.footer):
            # No row advanced the content: the end of it, a screen that does not scroll, or
            # flicks the worker kept cutting short.
            stopped = "input" if failures > INPUT_RETRIES else "end"
            pages.append(page if page is not None else await page_from(await capture()))
            break
        assert page is not None
        drag_px = max(1.0, dragged * height)
        band = height - match.header - match.footer
        if match.matched:
            # Later flicks stay inside this pair's scrolling band, never in the outer zones
            # where a tab bar or a navigation bar would take the touch instead, and learn how
            # far this screen really moves per flicked pixel.
            band_top, band_bottom = match.header / height, (height - match.footer) / height
            ratio = match.shift / drag_px
            gain = min(GAIN_MAX, max(GAIN_MIN, (1 - GAIN_SMOOTHING) * gain + GAIN_SMOOTHING * ratio))
            page.shift, page.matched = match.shift, True
            if match.confidence == "weak":
                notes.append(f"page {len(pages) + 1}: weak match (score {match.difference})")
        else:
            # Overlap unproven (moving tiles, a layout change, or a fling past the band). Join at
            # the shift the learned gain predicts, clamped to the band, rather than the whole
            # band: a few percent of error instead of a repeated screen. Assume a stronger
            # fling so the next flick is shorter; the chrome of this pair is not trusted.
            estimate = int(min(band, max(band * MIN_OVERLAP, drag_px * gain)))
            gain = min(GAIN_MAX, gain * MISS_GAIN)
            notes.append(
                f"page {len(pages) + 1}: overlap unproven; joined at the estimated shift {estimate} px"
            )
            page.shift, page.matched = estimate, False
        y_from, y_to = next_drag()
        pages.append(page)
    # The capture after an "end" verdict shows the same content as the page before it; it is
    # the current-screen reference, not a page of its own.
    current = pages[-1].metadata
    if stopped in {"end", "input"} and len(pages) > 1 and pages[-1].shift == 0:
        pages.pop()
        notes.append("current screen equals the last page")
    stitched = None
    if do_stitch:
        stitched = await loop.run_in_executor(None, stitch, pages)
    return Result(pages, stitched, stopped, current, time.monotonic() - started, swipes, notes, lags, cadence)
