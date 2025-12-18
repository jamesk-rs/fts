on rpi
```
sudo usbip bind -b 3-2
sudo modprobe usbip-host
sudo usbip bind -b 3-2
```

on minishuttle
```
sudo usbip attach -r rpi5b.local -b 3-2
```