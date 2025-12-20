"""
Hardware configuration for FTS-QA test setup.

Fixed configuration:
- Receiver (RX): 192.168.10.2 with GPSDO (always uses GPSDO clock)
- Transmitter (TX): 192.168.10.3 (can use internal or MIMO clock from RX)
"""

from dataclasses import dataclass
from typing import Literal


# Fixed hardware addresses
RX_ADDR = "192.168.10.2"  # Receiver with GPSDO
TX_ADDR = "192.168.10.3"  # Transmitter

ClockSource = Literal["internal", "gpsdo", "mimo"]


@dataclass
class RxConfig:
    """Configuration for USRP RX (receiver at 192.168.10.2)."""

    sample_rate: float = 10e6  # 10 MSps
    freq: float = 0.0          # Baseband
    gain: float = 0.0          # RX gain
    channels: list[int] = None  # Default: [0]

    # RX always uses GPSDO
    addr: str = RX_ADDR
    clock_source: str = "gpsdo"
    time_source: str = "gpsdo"

    def __post_init__(self):
        if self.channels is None:
            self.channels = [0]

    def to_dict(self) -> dict:
        return {
            'addr': self.addr,
            'sample_rate': self.sample_rate,
            'freq': self.freq,
            'gain': self.gain,
            'channels': self.channels,
            'clock_source': self.clock_source,
            'time_source': self.time_source,
        }


@dataclass
class TxConfig:
    """Configuration for USRP TX (transmitter at 192.168.10.3)."""

    sample_rate: float = 10e6  # 10 MSps
    freq: float = 0.0          # Baseband
    # Note: Basic TX has no variable gain - use waveform amplitude instead

    # TX can use internal clock or MIMO (shared from RX)
    use_mimo_clock: bool = True  # If True, sync to RX via MIMO cable

    addr: str = TX_ADDR

    @property
    def clock_source(self) -> str:
        return "mimo" if self.use_mimo_clock else "internal"

    @property
    def time_source(self) -> str:
        return "mimo" if self.use_mimo_clock else "internal"

    def to_dict(self) -> dict:
        return {
            'addr': self.addr,
            'sample_rate': self.sample_rate,
            'freq': self.freq,
            'clock_source': self.clock_source,
            'time_source': self.time_source,
            'use_mimo_clock': self.use_mimo_clock,
        }
