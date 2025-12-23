---
nav_exclude: true
---

on rpi
```
sudo modprobe usbip-host
sudo usbipd -D
sudo usbip bind -b 3-2
```

on minishuttle
```
sudo usbip attach -r rpi5b.local -b 3-2
```