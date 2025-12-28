"""
USRP RX/TX using UHD Python API.

Hardware setup:
- RX (192.168.10.2): Receiver with GPSDO, always uses GPSDO clock
- TX (192.168.10.3): Transmitter, can use internal or MIMO clock from RX
"""

import json
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional
from .config import RxConfig, TxConfig


class USRPCapture:
    """
    USRP RX capture from 192.168.10.2 (with GPSDO).

    Uses UHD Python API directly.
    """

    def __init__(
        self,
        sample_rate: float = 10e6,
        freq: float = 0.0,
        gain: float = 0.0,
        channels: Optional[list[int]] = None,
        addr: Optional[str] = None,
        wait_for_gps: bool = True,
    ):
        """
        Initialize USRP RX capture.

        Args:
            sample_rate: Sample rate in Hz (default: 10 MSps)
            freq: Center frequency in Hz (default: 0 for baseband)
            gain: RX gain in dB (default: 0)
            channels: Channel indices (default: [0])
            addr: USRP address (default: from USRP_RX_ADDR env or 192.168.10.2)
            wait_for_gps: Wait for GPS and ref lock before streaming (default: True)
        """
        config_kwargs = dict(
            sample_rate=sample_rate,
            freq=freq,
            gain=gain,
            channels=channels,
        )
        if addr is not None:
            config_kwargs['addr'] = addr
        self.config = RxConfig(**config_kwargs)
        self._usrp = None
        self._streamer = None
        self._overflow_count = 0
        self._wait_for_gps = wait_for_gps

    def _wait_for_lock(self) -> None:
        """
        Wait for GPS and reference clock lock.

        Blocks indefinitely until both GPS and REF are locked.
        GPSDO lock is mandatory for correct timing.
        """
        import time

        print("Waiting for GPSDO lock...")

        while True:
            # Check GPS lock
            try:
                gps_locked = self._usrp.get_mboard_sensor("gps_locked").to_bool()
            except RuntimeError:
                gps_locked = False

            # Check ref lock (10MHz reference from GPSDO)
            try:
                ref_locked = self._usrp.get_mboard_sensor("ref_locked").to_bool()
            except RuntimeError:
                ref_locked = False

            if gps_locked and ref_locked:
                print("  GPS locked, ref locked")
                return

            status = []
            status.append("GPS:locked" if gps_locked else "GPS:waiting")
            status.append("REF:locked" if ref_locked else "REF:waiting")
            print(f"  {' '.join(status)}...", end='\r')
            time.sleep(1.0)

    def get_usrp_time(self) -> float:
        """Get current USRP time in seconds (from GPSDO)."""
        if self._usrp is None:
            return 0.0
        return self._usrp.get_time_now().get_real_secs()

    def _init_usrp(self):
        """Initialize UHD USRP object."""
        if self._usrp is not None:
            return

        import uhd

        print(f"Connecting to RX USRP at {self.config.addr}...")
        self._usrp = uhd.usrp.MultiUSRP(f"addr={self.config.addr}")

        # Set clock/time source (GPSDO)
        print(f"  Clock source: {self.config.clock_source}")
        print(f"  Time source: {self.config.time_source}")
        self._usrp.set_clock_source(self.config.clock_source)
        self._usrp.set_time_source(self.config.time_source)

        # Wait for GPS and ref lock if requested
        if self._wait_for_gps:
            self._wait_for_lock()
            # Align USRP device time to GPS time at next PPS
            import time
            gps_time = self._usrp.get_mboard_sensor("gps_time").to_int()
            self._usrp.set_time_next_pps(uhd.types.TimeSpec(gps_time + 1))
            time.sleep(1.1)  # Wait for PPS edge
            print(f"  USRP time aligned to GPS: {gps_time + 1}")

        # Configure RX
        self._usrp.set_rx_rate(self.config.sample_rate)
        self._usrp.set_rx_freq(uhd.types.TuneRequest(self.config.freq))
        self._usrp.set_rx_gain(self.config.gain)

        print(f"  Sample rate: {self._usrp.get_rx_rate()/1e6:.1f} MSps")
        print(f"  Frequency: {self._usrp.get_rx_freq()/1e6:.1f} MHz")
        print(f"  Gain: {self._usrp.get_rx_gain():.1f} dB")

        # Create RX streamer
        stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
        stream_args.channels = self.config.channels or [0]
        self._streamer = self._usrp.get_rx_stream(stream_args)

    def capture(
        self,
        n_samples: int,
        output_path: str | Path,
        save_metadata: bool = True,
    ) -> None:
        """
        Capture samples to file.

        Args:
            n_samples: Number of samples to capture
            output_path: Path to output .cfile
            save_metadata: Save JSON metadata sidecar
        """
        import uhd

        self._init_usrp()

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"Capturing {n_samples:,} samples...")

        # Allocate buffer
        buffer_size = min(100000, n_samples)
        recv_buffer = np.zeros(buffer_size, dtype=np.complex64)

        # Schedule streaming to start at next whole second (avoids USB latency ambiguity)
        current_time = self._usrp.get_time_now().get_real_secs()
        start_time = int(current_time) + 1  # Next whole second
        stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
        stream_cmd.num_samps = n_samples
        stream_cmd.stream_now = False
        stream_cmd.time_spec = uhd.types.TimeSpec(start_time)
        self._streamer.issue_stream_cmd(stream_cmd)
        print(f"Capture scheduled to start at USRP time {start_time}")

        # Receive and write directly to file
        metadata = uhd.types.RXMetadata()
        samples_received = 0

        with open(output_path, 'wb') as f:
            while samples_received < n_samples:
                to_receive = min(buffer_size, n_samples - samples_received)
                n = self._streamer.recv(recv_buffer[:to_receive], metadata, 3.0)

                if metadata.error_code == uhd.types.RXMetadataErrorCode.timeout:
                    print("Warning: RX timeout")
                    break
                elif metadata.error_code != uhd.types.RXMetadataErrorCode.none:
                    print(f"Warning: RX error: {metadata.error_code}")

                recv_buffer[:n].tofile(f)
                samples_received += n

                # Progress
                if samples_received % 10000000 == 0:
                    pct = 100 * samples_received / n_samples
                    print(f"  {samples_received:,} / {n_samples:,} ({pct:.0f}%)")

        print(f"Saved {samples_received:,} samples to {output_path}")

        if save_metadata:
            meta_path = output_path.with_suffix('.json')
            meta = {
                'timestamp': datetime.now().isoformat(),
                'n_samples': samples_received,
                'duration_s': samples_received / self.config.sample_rate,
                **self.config.to_dict(),
            }
            with open(meta_path, 'w') as f:
                json.dump(meta, f, indent=2)

    def stream(
        self,
        callback: Callable[[np.ndarray, float], bool],
        chunk_samples: int = 100000,
        duration: Optional[float] = None,
    ) -> None:
        """
        Stream samples with callback.

        Args:
            callback: Called with (chunk, timestamp). timestamp is GPSDO time
                      of first sample in chunk (seconds). Return False to stop.
            chunk_samples: Samples per chunk
            duration: Max duration in seconds (None = until callback returns False)
        """
        import uhd
        import time

        self._init_usrp()

        max_samples = int(duration * self.config.sample_rate) if duration else None

        # Schedule streaming to start at next whole second (avoids USB latency ambiguity)
        current_time = self._usrp.get_time_now().get_real_secs()
        start_time = int(current_time) + 1  # Next whole second
        stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
        stream_cmd.stream_now = False
        stream_cmd.time_spec = uhd.types.TimeSpec(start_time)
        self._streamer.issue_stream_cmd(stream_cmd)
        print(f"Streaming scheduled to start at USRP time {start_time}")

        metadata = uhd.types.RXMetadata()
        buffer = np.zeros(chunk_samples, dtype=np.complex64)
        samples_received = 0
        start_time = time.time()

        try:
            while True:
                n = self._streamer.recv(buffer, metadata, 3.0)

                if metadata.error_code == uhd.types.RXMetadataErrorCode.timeout:
                    continue
                elif metadata.error_code == uhd.types.RXMetadataErrorCode.overflow:
                    self._overflow_count += 1
                    # Continue processing - don't spam console
                elif metadata.error_code != uhd.types.RXMetadataErrorCode.none:
                    print(f"Warning: RX error: {metadata.error_code}")
                    continue

                samples_received += n

                # Get GPSDO timestamp from metadata
                chunk_time = metadata.time_spec.get_real_secs()

                if not callback(buffer[:n], chunk_time):
                    break

                if max_samples and samples_received >= max_samples:
                    break

                if duration and (time.time() - start_time) >= duration:
                    break

        except KeyboardInterrupt:
            print("\nStopping...")

        finally:
            # Stop streaming
            stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
            self._streamer.issue_stream_cmd(stream_cmd)

            # Report overflow count if any
            if self._overflow_count > 0:
                print(f"Warning: {self._overflow_count} RX overflows occurred during capture")

    def stream_threaded(
        self,
        callback: Callable[[np.ndarray, float], bool],
        chunk_samples: int = 100000,
        duration: Optional[float] = None,
        queue_depth: int = 200,
    ) -> None:
        """
        Stream samples with callback using dedicated receiver thread.

        This decouples sample reception from processing, preventing overflows
        when callback processing occasionally takes longer than the chunk duration.

        Args:
            callback: Called with (chunk, timestamp). timestamp is GPSDO time
                      of first sample in chunk (seconds). Return False to stop.
            chunk_samples: Samples per chunk
            duration: Max duration in seconds (None = until callback returns False)
            queue_depth: Max chunks to buffer (default: 200 = ~2s at 10ms chunks)
        """
        import uhd
        import time
        import threading
        import queue

        self._init_usrp()

        # Schedule streaming to start at next whole second (avoids USB latency ambiguity)
        current_time = self._usrp.get_time_now().get_real_secs()
        scheduled_start = int(current_time) + 1  # Next whole second
        print(f"Streaming scheduled to start at USRP time {scheduled_start}")

        # Shared state - queue now holds (data, timestamp) tuples
        data_queue: queue.Queue = queue.Queue(maxsize=queue_depth)
        stop_event = threading.Event()
        self._overflow_count = 0

        def receiver_thread():
            """Continuously receive samples and enqueue them."""
            buffer = np.zeros(chunk_samples, dtype=np.complex64)
            metadata = uhd.types.RXMetadata()

            # Start continuous streaming at scheduled time
            stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
            stream_cmd.stream_now = False
            stream_cmd.time_spec = uhd.types.TimeSpec(scheduled_start)
            self._streamer.issue_stream_cmd(stream_cmd)

            while not stop_event.is_set():
                n = self._streamer.recv(buffer, metadata, 0.1)

                if metadata.error_code == uhd.types.RXMetadataErrorCode.overflow:
                    self._overflow_count += 1
                elif metadata.error_code == uhd.types.RXMetadataErrorCode.timeout:
                    continue
                elif metadata.error_code != uhd.types.RXMetadataErrorCode.none:
                    continue

                if n > 0:
                    try:
                        # Get timestamp from metadata (GPSDO time of first sample)
                        chunk_time = metadata.time_spec.get_real_secs()
                        # Copy buffer since we reuse it
                        data_queue.put((buffer[:n].copy(), chunk_time), timeout=0.1)
                    except queue.Full:
                        self._overflow_count += 1  # Queue full = soft overflow

            # Stop streaming
            stream_cmd = uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont)
            self._streamer.issue_stream_cmd(stream_cmd)

        # Start receiver thread
        rx_thread = threading.Thread(target=receiver_thread, daemon=True)
        rx_thread.start()

        # Pre-buffer: wait for queue to fill a bit before starting processing
        # With simple trigger detection (no linreg in callback), 10 chunks is sufficient
        prebuffer_chunks = min(10, queue_depth // 10)  # ~100ms at 10ms chunks
        print(f"Pre-buffering {prebuffer_chunks} chunks (~{prebuffer_chunks * 10}ms)...")
        prebuffer_start = time.time()
        while data_queue.qsize() < prebuffer_chunks:
            time.sleep(0.005)
            if time.time() - prebuffer_start > 2.0:
                print(f"  Warning: only got {data_queue.qsize()} chunks after 2s")
                break
        print(f"Pre-buffered {data_queue.qsize()} chunks. Streaming...")

        # Process in main thread
        start_time = time.time()
        samples_received = 0

        try:
            while True:
                try:
                    chunk, chunk_time = data_queue.get(timeout=0.5)
                except queue.Empty:
                    if stop_event.is_set():
                        break
                    continue

                samples_received += len(chunk)

                if not callback(chunk, chunk_time):
                    break

                if duration and (time.time() - start_time) >= duration:
                    break

        except KeyboardInterrupt:
            print("\nStopping...")

        finally:
            stop_event.set()
            rx_thread.join(timeout=1.0)

            # Report overflow count if any
            if self._overflow_count > 0:
                print(f"Warning: {self._overflow_count} overflows occurred during capture")

    def close(self):
        """Release USRP resources."""
        self._streamer = None
        self._usrp = None


class USRPTransmit:
    """
    USRP TX on 192.168.10.3 using UHD Python API.

    Can use internal clock or MIMO clock shared from RX.
    """

    def __init__(
        self,
        sample_rate: float = 10e6,
        freq: float = 0.0,
        use_mimo_clock: bool = True,
    ):
        """
        Initialize USRP TX.

        Args:
            sample_rate: Sample rate in Hz
            freq: Center frequency in Hz
            use_mimo_clock: If True, sync clock from RX via MIMO cable
        """
        self.config = TxConfig(
            sample_rate=sample_rate,
            freq=freq,
            use_mimo_clock=use_mimo_clock,
        )
        self._usrp = None
        self._streamer = None

    def _init_usrp(self):
        """Initialize UHD USRP object."""
        if self._usrp is not None:
            return

        import uhd

        print(f"Connecting to TX USRP at {self.config.addr}...")
        self._usrp = uhd.usrp.MultiUSRP(f"addr={self.config.addr}")

        # Set clock/time source
        if self.config.use_mimo_clock:
            print(f"  Clock source: {self.config.clock_source}")
            print(f"  Time source: {self.config.time_source}")
            self._usrp.set_clock_source(self.config.clock_source)
            self._usrp.set_time_source(self.config.time_source)

        # Configure TX
        self._usrp.set_tx_rate(self.config.sample_rate)
        self._usrp.set_tx_freq(uhd.types.TuneRequest(self.config.freq))

        print(f"  Sample rate: {self._usrp.get_tx_rate()/1e6:.1f} MSps")
        print(f"  Frequency: {self._usrp.get_tx_freq()/1e6:.1f} MHz")

        # Create TX streamer
        stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
        stream_args.channels = [0]
        self._streamer = self._usrp.get_tx_stream(stream_args)

    def transmit_waveform(
        self,
        waveform: np.ndarray,
        duration: Optional[float] = None,
        repeat: bool = False,
    ) -> None:
        """
        Transmit a waveform using UHD Python API.

        Args:
            waveform: Complex64 samples
            duration: Duration in seconds
            repeat: Loop the waveform (requires duration)
        """
        import uhd
        import time

        self._init_usrp()

        # Ensure correct dtype
        if waveform.dtype != np.complex64:
            waveform = waveform.astype(np.complex64)

        metadata = uhd.types.TXMetadata()
        metadata.has_time_spec = False

        start_time = time.time()

        print(f"Transmitting {len(waveform):,} samples...")
        if repeat:
            if duration:
                print(f"  Repeating for {duration} seconds")
            else:
                print("  Repeating until Ctrl+C")

        try:
            metadata.start_of_burst = True
            metadata.end_of_burst = False

            while True:
                # Send entire waveform at once - let UHD handle buffering
                self._streamer.send(waveform, metadata)
                metadata.start_of_burst = False

                # Check if we should stop
                if not repeat:
                    break
                if duration and (time.time() - start_time) >= duration:
                    break

        except KeyboardInterrupt:
            print("\nStopping...")

        finally:
            # Send end of burst
            metadata.start_of_burst = False
            metadata.end_of_burst = True
            self._streamer.send(np.zeros(1, dtype=np.complex64), metadata)
            print("TX complete.")

    def close(self):
        """Release USRP resources."""
        self._streamer = None
        self._usrp = None
