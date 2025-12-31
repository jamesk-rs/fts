---
title: Serial Console Setup
date: 2025-12-28
nav_order: 3
has_toc: true
---
# Serial Console Setup

## Table of Contents
- TOC
{:toc}

## Introduction

This page documents configuration and wiring of the serial consoles, from the devices to Raspberry Pi 5.

## Master

* BLUE: GND
* GREEN: TX (from Master towards RPI5)

![](assets/images/20251228-serial-master-wiring.png)

## Slave

* YELLOW: GND
* ORANGE: TX (from Slave to RPI)

![](assets/images/20251228-serial-slave-wiring.png)

## RPI

![](assets/images/20251228-serial-rpi-connections.png)

Slave: /dev/ttyAMA0:
* ORANGE: pin 10 (RX from Slave)
* Unused: pin 8 (TX to Slave)
* YELLOW: pin 6 (GND)

Master /dev/ttyAMA1:
* GREEN: pin 28 (RX from Master)
* Unused: pin 27 (TX to Master)
* BLUE: pin 25 (GND)

Must enable 2nd serial port:
```
abb@raspberrypi:~ $ diff -u /boot/firmware/config.txt- /boot/firmware/config.txt
--- /boot/firmware/config.txt-	2025-12-28 00:27:42.000000000 +0100
+++ /boot/firmware/config.txt	2025-12-28 00:43:18.000000000 +0100
@@ -50,3 +50,5 @@

 [all]
 dtparam=uart0=on
+enable_uart=1
+dtoverlay=uart1
```

This should result in the following pin configuration:
```
abb@raspberrypi:~ $ pinctrl -p | grep -E '(RX|TX)'
 8: a4    pn | hi // GPIO14 = TXD0
10: a4    pu | hi // GPIO15 = RXD0
27: a2    pn | hi // GPIO0 = TXD1
28: a2    pu | hi // GPIO1 = RXD1
```

The pinout of Raspberry Pi 5:

![](assets/images/20251228-rpi5-pinout.png)

To use:
```
sudo minicom -b 115200 -D /dev/ttyAMA0 -C log0
```
