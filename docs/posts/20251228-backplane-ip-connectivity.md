---
title: Backplane IP Connectivity
date: 2025-12-28
nav_order: 4
has_toc: true
---
# Backplane IP Connectivity

## Table of Contents
- TOC
{:toc}

## Introduction

This page documents implementation of IP connectivity between the devices and TIG stack, which is used for telemetry.

All ESP32 S3 devices implement USB NCM now. When plugged into a Linux host they act as a USB-connected Ethernet station.

## Linux Bridge Configuration

Linux host acts as a bridge and DHCP server for them:
```
network:
  version: 2
  renderer: networkd

  ethernets:
    # Seeed XIAO S3 Sense 1
    enxb8f862f9f308:
      dhcp4: false
      optional: true

    # Waveshare
    enx1020ba466b28:
      dhcp4: false
      optional: true

    enx1020ba466b98:
      dhcp4: false
      optional: true

  bridges:
    br1:
      interfaces: [enxb8f862f9f308, enx1020ba466b28, enx1020ba466b98]
      dhcp4: false
      addresses: [192.168.7.1/24]
```

```
dnsmasq --interface=br1 --bind-interfaces --dhcp-range=192.168.7.100,192.168.7.110,12h --port=0
```

## Known Issues

For some reason bridging into LAN is not working, might have something to do with:
* the fact docker runs on Linux and somehow screws up the bridging process,
* or the fact both ESP32 and Linux consider MAC address of USB NCM adapter as their own,
* or a bug in ESP32 DHCP client firmware

The last one is the most likely, because if we activate debug there we see DHCP offer makes its way to ESP32 but it ignores it for some reason. So have to plug into a dedicated bridge and run local DHCP server there.

## Workaround for Raspberry Pi

For rPi need to ROUTE/MASQUERADE (TODO) or pass through:
```
socat -v TCP4-LISTEN:1883,reuseaddr,fork TCP4:192.168.129.206:1883
```
