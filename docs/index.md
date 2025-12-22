# Fine Time Sync for ESP32 Library

FTS can be used to build synchronised high-precision timing network using Wi-Fi FTM (Fine Timing Measurement) protocol, supported by some ESP32 chips (S3, etc) out of the box.

FTS delivers two main components:
 * Clock Relationship Model: Slaves build and maintain a (linear regression based) model of relationships. This model can be used to translate between the local and master's clocks.
 * Disciplined Timer: Slaves fine-tunes the period of the its local timer to make it fire in sync with the master.

This repo contains full source code and README.md explaning how to build and test FTS.

Along with the very first release I have published [full technical implementation details](https://github.com/abbbe/fts/blob/main/docs/fts-presa-20251203.pdf) in  and a small [demo on Reddit](https://www.reddit.com/r/embedded/comments/1pbp0az/).