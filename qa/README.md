# Install

```
sudo apt-get install -y uhd-host python3-uhd
sudo bin/configure-sysctl.sh
```

```
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt
python -m bash_kernel.install
jupyter-lab
```

```

```

# Overview

This folder contains code for testing of FTS (Fine Time Sync library https://github.com/abbbe/fts).

The system under test is has 3 nodes: a master and two slaves, it is identical to demo setup described in the README.md file in FTS repo. The nodes generate 2kHz square pulses.

The goal is to make sure the system behaves as designed and to quantify jitter:
* during the initial alignment,
* ongoing operations,
* in case of master loss.

The latter case is for sanity checking, we expect to see phase error to accumulate quickly.

We will use an SDR will capture these pulses in real time,
  * USRP N210 with Basic RX card
  * Two channels at 100MSps (total, theoretical)
  * 1-250MHz bandwidth
  * GPSDO 0.01ppm
  * MIMO cable (https://kb.ettus.com/Synchronization_and_MIMO_Capability_with_USRP_Devices)
* Python on Linux will capture the data stream
  * The receiver is AC-coupled, will see raising and falling edges as positive / negative pulses
  * Detect peaks, store 5 samples prior to the peak (includig the peak)
  * Another python script will measure the jitter offline

For sanity checking we will have another N210 with Basix TX to generate signals with a known jitter.

# FTS-QA Project Structure

  fts-qa/
  ├── src/
  │   ├── capture/         # SDR capture (UHD Python API)
  │   │   ├── config.py    # CaptureConfig dataclass
  │   │   └── usrp.py      # USRPCapture class (batch + streaming)
  │   │
  │   ├── generate/        # Test signal generator
  │   │   ├── waveforms.py # generate_dual_pulses, simulate_ac_coupled
  │   │   └── usrp_tx.py   # USRPTransmit class
  │   │
  │   ├── detect/          # Edge detection (from parabolic.ipynb)
  │   │   ├── edges.py     # detect_edges() with numba
  │   │   └── interpolate.py # parabolic_refine()
  │   │
  │   ├── analyze/         # Jitter analysis
  │   │   ├── jitter.py    # compute_delays, match_edges
  │   │   ├── stats.py     # JitterStats, compute_stats
  │   │   └── report.py    # CSV, plots, JSON summary
  │   │
  │   └── cli.py           # Command-line interface
  │
  ├── tests/
  │   ├── test_detect.py   # Edge detection tests
  │   └── test_waveforms.py # End-to-end pipeline tests
  │
  ├── pyproject.toml       # Package configuration
  └── .gitignore
