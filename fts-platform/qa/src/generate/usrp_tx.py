"""
USRP TX for test signal generation.

Uses UHD Python API to transmit test waveforms.
"""

import numpy as np
from typing import Optional


class USRPTransmit:
    """
    USRP TX interface for generating test signals.

    Transmits pre-generated waveforms on USRP N210 with Basic TX.
    """

    def __init__(
        self,
        addr: str = "192.168.10.3",
        sample_rate: float = 10e6,
        freq: float = 0.0,
        gain: float = 0.0,
        channels: Optional[list[int]] = None,
    ):
        """
        Initialize USRP TX.

        Args:
            addr: USRP IP address
            sample_rate: Sample rate in Hz
            freq: Center frequency in Hz
            gain: TX gain in dB
            channels: List of channel indices
        """
        self.addr = addr
        self.sample_rate = sample_rate
        self.freq = freq
        self.gain = gain
        self.channels = channels or [0]

        self._usrp = None
        self._streamer = None

    def _init_usrp(self):
        """Initialize UHD USRP object."""
        if self._usrp is not None:
            return

        try:
            import uhd
        except ImportError:
            raise RuntimeError(
                "UHD Python bindings not available. "
                "Install with: pip install uhd"
            )

        # Create USRP object
        self._usrp = uhd.usrp.MultiUSRP(f"addr={self.addr}")

        # Configure TX
        for chan in self.channels:
            self._usrp.set_tx_rate(self.sample_rate, chan)
            self._usrp.set_tx_freq(uhd.types.TuneRequest(self.freq), chan)
            self._usrp.set_tx_gain(self.gain, chan)

        # Create TX streamer
        stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
        stream_args.channels = self.channels
        self._streamer = self._usrp.get_tx_stream(stream_args)

    def transmit(
        self,
        waveform: np.ndarray,
        repeat: bool = False,
    ) -> None:
        """
        Transmit a waveform.

        Args:
            waveform: Complex64 samples to transmit
            repeat: If True, repeat waveform continuously until stopped
        """
        self._init_usrp()

        import uhd

        # Ensure correct dtype
        if waveform.dtype != np.complex64:
            waveform = waveform.astype(np.complex64)

        metadata = uhd.types.TXMetadata()
        metadata.start_of_burst = True
        metadata.end_of_burst = not repeat
        metadata.has_time_spec = False

        buffer_size = min(10000, len(waveform))

        if repeat:
            # Continuous transmission
            try:
                while True:
                    for i in range(0, len(waveform), buffer_size):
                        chunk = waveform[i:i + buffer_size]
                        if i == 0:
                            metadata.start_of_burst = True
                        else:
                            metadata.start_of_burst = False
                        metadata.end_of_burst = False
                        self._streamer.send(chunk, metadata)
            except KeyboardInterrupt:
                # Send end of burst
                metadata.start_of_burst = False
                metadata.end_of_burst = True
                self._streamer.send(np.zeros(1, dtype=np.complex64), metadata)
        else:
            # Single transmission
            for i in range(0, len(waveform), buffer_size):
                chunk = waveform[i:i + buffer_size]
                is_last = (i + buffer_size >= len(waveform))

                if i > 0:
                    metadata.start_of_burst = False
                metadata.end_of_burst = is_last

                self._streamer.send(chunk, metadata)

    def transmit_continuous(
        self,
        waveform: np.ndarray,
        duration: Optional[float] = None,
    ) -> None:
        """
        Transmit waveform continuously for specified duration.

        Args:
            waveform: Complex64 samples (will be repeated)
            duration: Duration in seconds (None = indefinite)
        """
        self._init_usrp()

        import uhd
        import time

        if waveform.dtype != np.complex64:
            waveform = waveform.astype(np.complex64)

        metadata = uhd.types.TXMetadata()
        metadata.has_time_spec = False

        buffer_size = min(10000, len(waveform))
        start_time = time.time()

        try:
            first = True
            while duration is None or (time.time() - start_time) < duration:
                for i in range(0, len(waveform), buffer_size):
                    chunk = waveform[i:i + buffer_size]
                    metadata.start_of_burst = first and (i == 0)
                    metadata.end_of_burst = False
                    self._streamer.send(chunk, metadata)
                    first = False

                    if duration and (time.time() - start_time) >= duration:
                        break

        finally:
            # Send end of burst
            metadata.start_of_burst = False
            metadata.end_of_burst = True
            self._streamer.send(np.zeros(1, dtype=np.complex64), metadata)

    def close(self):
        """Release USRP resources."""
        self._streamer = None
        self._usrp = None
