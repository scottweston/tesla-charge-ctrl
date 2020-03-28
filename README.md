# Control Tesla Charger rate via MQTT

I like to have my home automation be able to control the rate of charge
of my Tesla Model S and this is the tool I use to do it. I wire in some
hardware to my HPWC and then send MQTT instructions to control the charge
rate. This way my homeautomation can ensure my Tesla never exceeds the
excess available solar power being generated from my solar panels. It
also allows me to move excess charge from my Powerwall into my Model S
early in the morning before the sun comes up to recharge the Powerwall.

## HARDWARE

*  ESP32 based board, e.g. one of:
    * [LoPY4](https://pycom.io/product/lopy4/)
    * [Huzzah32](https://www.adafruit.com/product/3405)
    * [TinyPCIO](https://www.tinypico.com/buy)
* [RS485 transceiver](https://www.sparkfun.com/products/10124)

## Wiring

Coming soon

## INSTALL

* Get [ampy](https://learn.adafruit.com/micropython-basics-load-files-and-run-code/install-ampy) working for your environment and talking to your ESP32
* Copy `networks.json.dist` to `networks.json` and then edit to add your wifi network(s) details
* Copy `master_config.json.dist` to `master_config.json` and then edit it to suit your environment, specifically set your MQTT topics and server address
* Now copy the files to the ESP32

```
for i in boot.py main.py networks.json master_config.json; do
  ampy -p /dev/ttyUSB0 -b 115200 put $i /$i
done
ampy reset
```

* Now connect to the USB serial port of your ESP32 and check it is working

## DEBUGING

### Linux and Mac

```
$ screen /dev/ttyUSB0 115200
```

### Windows

I don't know of any terminal based serial tools, I'd probably use the serial
monitor of the arduino IDE to connect to the board.
