"""Decode the phone's mirroring video and serve its latest frame as a screenshot.

The phone hardware-encodes an H.265 stream for screen mirroring; the same stream also gates
HID input authentication. Draining and decoding it on the Mac turns a screenshot into "grab the
latest decoded frame" (~0.1s) instead of the ~2.8s discrete PNG capture, and one stream serves
both observation and input. All frame handling lives here; the rest of the worker sees an image.

After a change the phone sends 60 frames a second for a while, faster than software HEVC decode
keeps up with while other work runs, so decoding every frame in arrival order falls behind. So
receiving and decoding are separate: frames queue between them and a keyframe drops everything
queued before it. A screenshot that needs the screen as it is now asks the phone for a keyframe
(RTCP PLI), which arrives in tens of milliseconds and skips the decoder past the backlog.
"""

import asyncio
import collections
import contextlib
import logging
import struct
import time

from thumbly.coredevice.state import classify, transport_lost
from thumbly.domain.errors import ThumblyError

logger = logging.getLogger(__name__)

# HEVC NAL unit types that carry a keyframe (IDR/CRA); a fresh one lets the decoder resync.
_KEY_NAL_TYPES = {19, 20, 21}


# The phone answers most keyframe requests within ~0.1 s but ignores some; ask again this often.
# Fewer than a handful per screenshot: a sustained burst of requests can stall its encoder.
KEYFRAME_RETRY_SECONDS = 0.3


def _nal_type(nal: bytes) -> int:
    return (nal[0] >> 1) & 0x3F if nal else -1


def receiver_report(ours: int, theirs: int, highest_seq: int) -> bytes:
    """RTCP Receiver Report plus an empty-CNAME SDES, the compound Xcode's mirror sends once a
    second (the stream config sets RTCPTimeoutEnabled)."""
    report = struct.pack("!BBHII4xIIII", 0x81, 0xC9, 7, ours, theirs, highest_seq & 0xFFFFFFFF, 0, 0, 0)
    return report + struct.pack("!BBHIBBBB", 0x81, 0xCA, 2, ours, 1, 0, 0, 0)


def picture_loss(ours: int, theirs: int) -> bytes:
    """RTCP Picture Loss Indication (RFC 4585): asks the phone for a keyframe."""
    return struct.pack("!BBHII", 0x81, 0xCE, 2, ours, theirs)


class ScreenDecoder:
    """One H.265 mirroring stream, decoded to a single latest frame.

    ``start`` opens the media stream and a receive/decode task. ``grab`` returns the newest frame
    as a PIL image cropped to the device's content size, or ``None`` when no fresh frame exists.
    A transport failure marks the session lost through ``on_lost`` so the manager reconnects.
    """

    def __init__(self, rsd, config, on_lost):
        self.rsd, self.config, self.on_lost = rsd, config, on_lost
        self.display = None
        self.transport = None
        self.task = None
        self.decoder = None
        self.decode_task = None
        self.report_task = None
        self.latest = None
        self.latest_arrival = 0.0  # When the access unit behind ``latest`` arrived.
        # Arrival time of the access unit being decoded right now, or None between decodes.
        self.decoding: float | None = None
        self.started_at = 0.0
        self.content: tuple[int, int] | None = None
        self.lost = False
        # (arrival time, access unit, is keyframe) waiting for the decoder; starts at a keyframe.
        self.pending: collections.deque[tuple[float, bytes, bool]] = collections.deque()
        self.ready = asyncio.Event()
        # RTCP: the phone's report port and both SSRCs (ours, the phone's), from the stream answer.
        self.rtcp_dest: tuple[str, int] | None = None
        self.ssrc = (0, 0)
        self.highest_seq = 0
        # Arrival time of the newest keyframe the decoder has finished.
        self.key_decoded = 0.0
        self.keyed = False  # A keyframe has been queued, so deltas have something to apply to.
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def frame_age(self) -> float:
        """How far the latest frame lags the screen: time since the frame it shows arrived while
        newer ones wait, or 0 once the decoder has caught up. A still screen sends nothing, so
        the last frame stays current however long ago it came."""
        if self.latest is None:
            return float("inf")
        if not self.pending and self.decoding is None:
            return 0.0
        return time.monotonic() - self.latest_arrival

    def set_content(self, width: int, height: int) -> None:
        """The device's native portrait content size; decoded frames are padded larger and cropped
        back to this so touch geometry matches the discrete-capture path exactly."""
        self.content = (width, height)

    async def start(self) -> None:
        from pymobiledevice3.remote.core_device.display_service import DisplayService, is_media_in_use_error
        from pymobiledevice3.remote.core_device.screen_stream import open_media_receiver

        async with self._lock:
            if self.running:
                return
            display, transport = DisplayService(self.rsd), None
            try:
                await asyncio.wait_for(display.connect(), 10)
                transport, receiver_ip = open_media_receiver(display, (4 * 1024 * 1024, 1024 * 1024))
                answer = await asyncio.wait_for(
                    display.start_video_stream(
                        receiver_ip=receiver_ip,
                        receiver_port=transport.port,
                        sender_ip=self.rsd.service.address[0],
                        display_id=1,
                    ),
                    10,
                )
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                await self._teardown(display, transport)
                raise ThumblyError("HID_UNAVAILABLE", "The phone's media stream did not start") from None
            except Exception as exc:
                await self._teardown(display, transport)
                if is_media_in_use_error(exc):
                    raise ThumblyError(
                        "HID_UNAVAILABLE", "Quit the app using the camera or microphone on the phone"
                    ) from None
                raise self._fail(exc) from None
            self.display, self.transport = display, transport
            self.latest, self.latest_arrival, self.lost = None, 0.0, False
            self.pending.clear()
            self.keyed = False
            self.started_at = time.monotonic()
            self._rtcp_config(answer)
            self.task = asyncio.create_task(self._run())
            self.decode_task = asyncio.create_task(self._decode_loop())
            if self.rtcp_dest is not None:
                self.report_task = asyncio.create_task(self._report())

    async def wait_frame(self, deadline: float) -> bool:
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            if self.latest is not None:
                return True
            if not self.running:
                return False
            await asyncio.sleep(0.02)
        return self.latest is not None

    async def catch_up(self, wait: float) -> bool:
        """Make the latest frame show the screen as it is now: done once the decoder has nothing
        queued, or has decoded a keyframe requested by this call. Waits up to ``wait`` seconds;
        False when it did not get there, and ``frame_age`` then says how far behind it is."""
        asked = time.monotonic()
        requested = 0.0
        while self.frame_age() > 0 and self.key_decoded < asked:
            now = time.monotonic()
            if now - asked >= wait or not self.running:
                return False
            if self.rtcp_dest is not None and now - requested >= KEYFRAME_RETRY_SECONDS:
                requested = now
                await self._send_rtcp(picture_loss(*self.ssrc))
            await asyncio.sleep(0.01)
        return True

    async def grab(self, max_size: int | None = None):
        """Latest decoded frame as an RGB image of the content, scaled to fit ``max_size`` (never
        up), with its lag behind the screen when it was picked (``frame_age``) and the native
        content size; None if too stale. The age is taken first, so conversion time does not
        count as lag."""
        frame, age = self.latest, self.frame_age()
        if frame is None or age > self.config.stream_frame_ttl:
            return None
        image, native = await asyncio.get_running_loop().run_in_executor(None, self._render, frame, max_size)
        return image, age, native

    def _render(self, frame, max_size: int | None):
        # Scale in swscale while converting from YUV: ~5x cheaper than converting at
        # full size and resizing in PIL. The decoded frame is padded past the content on the
        # right and bottom, so scale the whole frame by the content's factor and crop.
        width, height = self.content or (frame.width, frame.height)
        width, height = min(width, frame.width), min(height, frame.height)
        scale = min(1.0, max_size / max(width, height)) if max_size else 1.0
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        if scale < 1.0:
            rgb = frame.reformat(
                round(frame.width * scale), round(frame.height * scale), "rgb24", interpolation="BILINEAR"
            )
        else:
            rgb = frame.reformat(format="rgb24")
        return rgb.to_image().crop((0, 0, *size)), (width, height)

    async def stop(self) -> None:
        async with self._lock:
            tasks = [task for task in (self.task, self.decode_task, self.report_task) if task is not None]
            self.task = self.decode_task = self.report_task = None
            display, self.display = self.display, None
            transport, self.transport = self.transport, None
            self.latest = self.decoder = None
            self.pending.clear()
        for task in tasks:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        await self._teardown(display, transport)

    # ----- internals ---------------------------------------------------------------

    def _fail(self, exc: BaseException) -> ThumblyError:
        if transport_lost(exc):
            self.lost = True
            self.on_lost()
        return classify(exc)

    async def _teardown(self, display, transport) -> None:
        if transport is not None:
            with contextlib.suppress(Exception):
                transport.close()
        if display is not None and not self.lost:
            from pymobiledevice3.remote.core_device.display_service import DisplayService

            with contextlib.suppress(Exception):
                await asyncio.wait_for(DisplayService.stop_all_streams(self.rsd), 5)
        if display is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(display.close(), 2)

    def _rtcp_config(self, answer) -> None:
        """Where RTCP goes, from the stream answer; its SSRC names are from the phone's side."""
        connection = answer.get("connection") if isinstance(answer, dict) else None
        config = connection.get("streamConfig") if isinstance(connection, dict) else None
        config = config if isinstance(config, dict) else {}
        port, ours, theirs = (int(config.get(key) or 0) for key in ("SourcePort", "RemoteSSRC", "LocalSSRC"))
        if port and ours and theirs:
            self.rtcp_dest, self.ssrc, self.highest_seq = (
                (self.rsd.service.address[0], port),
                (ours, theirs),
                0,
            )
        else:
            self.rtcp_dest = None
            logger.warning("screen stream answer has no RTCP config; no keyframe requests")

    async def _send_rtcp(self, packet: bytes) -> None:
        transport, dest = self.transport, self.rtcp_dest
        if transport is None or dest is None:
            return
        try:
            await transport.sendto(packet, *dest)
        except OSError as exc:
            logger.debug("rtcp send failed: %s", type(exc).__name__)

    async def _report(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.highest_seq:
                await self._send_rtcp(receiver_report(*self.ssrc, self.highest_seq))

    def _queue(self, access_unit: bytes, key: bool) -> None:
        if key:
            self.pending.clear()  # Everything queued is superseded; decoding resumes here.
            self.keyed = True
        elif not self.keyed:
            return  # No keyframe yet: a delta has nothing to apply to.
        self.pending.append((time.monotonic(), access_unit, key))
        self.ready.set()

    def _decode(self, access_unit: bytes) -> None:
        import av

        if self.decoder is None:
            # Not frame threads: they hold the last frames back until more input arrives, and a
            # still screen sends none.
            self.decoder = av.CodecContext.create("hevc", "r")
            self.decoder.thread_count = 2
        for frame in self.decoder.decode(av.Packet(access_unit)):
            self.latest = frame

    async def _decode_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            if not self.pending:
                self.ready.clear()
                await self.ready.wait()
                continue
            self.decoding, access_unit, key = self.pending.popleft()
            # Decoding is CPU-bound ffmpeg work; keep it off the event loop.
            try:
                await loop.run_in_executor(None, self._decode, access_unit)
            except Exception:
                # A partial or out-of-order access unit; a later keyframe resyncs the decoder.
                logger.debug("hevc decode error; awaiting keyframe", exc_info=True)
            else:
                self.latest_arrival = self.decoding
                if key:
                    self.key_decoded = self.decoding
            finally:
                self.decoding = None

    async def _run(self) -> None:
        from pymobiledevice3.remote.core_device.screen_stream import depacketize_hevc

        transport = self.transport
        if transport is None:
            return
        fu_buffer = bytearray()
        nals: list[bytes] = []
        try:
            while True:
                try:
                    data = await transport.recv(65535)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("screen stream recv ended: %s", type(exc).__name__)
                    self._fail(exc)
                    return
                if len(data) < 12 or 64 <= (data[1] & 0x7F) <= 95:
                    continue  # too short, or RTCP
                marker = (data[1] >> 7) & 1
                # RTCP reports the extended (wrap-counting) highest sequence; late packets don't move it.
                seq = int.from_bytes(data[2:4], "big")
                ahead = (seq - self.highest_seq) & 0xFFFF
                if not self.highest_seq:
                    self.highest_seq = seq
                elif 0 < ahead < 0x8000:
                    self.highest_seq += ahead
                header_len = 12 + (data[0] & 0x0F) * 4
                if data[0] & 0x10:  # RTP extension header
                    ext_words = int.from_bytes(data[header_len + 2 : header_len + 4], "big")
                    header_len += 4 + ext_words * 4
                depacketize_hevc(data[header_len:], fu_buffer, nals)
                if not marker or not nals:
                    continue
                key = any(_nal_type(nal) in _KEY_NAL_TYPES for nal in nals)
                self._queue(b"".join(b"\x00\x00\x00\x01" + nal for nal in nals), key)
                nals = []
        except asyncio.CancelledError:
            pass
