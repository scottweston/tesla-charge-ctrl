import os
import machine
import time
import json

if os.uname().sysname == "LoPy4":
    import pyco
    HARDWARE='pycom'
elif os.uname().sysname == "esp32":
    import esp32
    HARDWARE='esp32'
else:
    print('unsupported hardware detected')
    raise

f = open('networks.json', 'r')
networks = json.load(f)
f.close()
print("WLAN: known networks: ", networks.keys())

if HARDWARE=='pycom':
    print("PYCOM")
    if machine.reset_cause() != machine.SOFT_RESET:
        from network import WLAN
        wlan = WLAN(mode=WLAN.STA)
        if not wlan.isconnected():
            print("WLAN: scanning for known network")
            nets = wlan.scan()
            for net in nets:
                print('Found network: ', net)
                if net.ssid in networks:
                    print('WLAN: attempting to connect to "%s"' % net.ssid)
                    wlan.connect(net.ssid, auth=(net.sec, networks[net.ssid]), timeout=5000)
                    while not wlan.isconnected():
                        machine.idle() # save power while waiting
                    print("WLAN: connected to "+net.ssid+" with IP address:" + wlan.ifconfig()[0])
                    break
        else:
            print("WLAN: auto-connected")
elif HARDWARE=='esp32':
    print("ESP32")
    import network
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.config(dhcp_hostname='hpwc')
    if not wlan.isconnected():
        print("WLAN: scanning for known network")
        nets = wlan.scan()
        for net in nets:
            print('Found network: ', net)
            ssid = net[0].decode()
            if ssid in networks:
                print('WLAN: attempting to connect to "%s"' % ssid)
                wlan.connect(ssid, networks[ssid])
                while not wlan.isconnected():
                    machine.idle() # save power while waiting
                print("WLAN: connected to " + ssid + " with IP address:" + wlan.ifconfig()[0])
                break
    else:
        print("WLAN: auto-connected")
else:
    print("Please code up how to connect, KTHNXBYE")

