import time
import json
import binascii
import machine
import gc
import network
import errno
import os

from network import WLAN
from machine import UART
from machine import Pin
from machine import WDT

from mqtt import MQTTClient

if os.uname().sysname == "LoPy4":
    import pyco
    HARDWARE='pycom'
    SERIAL=1
    RTS='P8'
    LED='P9'
elif os.uname().sysname == "esp32":
    import esp32
    HARDWARE='esp32'
    SERIAL=2
    RTS=15
    LED=13
else:
    print('unsupported hardware detected')
    raise

# -----------------------------------------------------------------------------

class InvalidMessage(Exception):
    pass

# -----------------------------------------------------------------------------

MSGLEN = 14
DEBUG = 3
PING = time.time() + 30
HEARTBEAT = time.time() + 1
ERRORS = 0

# This code only supports a single slave, if I ever own more than 1 tesla
# I'm going to have to revisit this in my copious amounts of free time
ourSlave = {
    "twcid": None,
    "busid": None,
    "maxamps": 0,
    "charge_rate": None,
    "ampsMax": 0,
    "ampsActual": 0,
    "state": 16
}

state = {
    0: "ready",
    1: "charging",
    2: "error",
    3: "idle",
    4: "scheduled",
    5: "busy",
    8: "starting"
}

def debug(s:str, l:int):
    if (l <= DEBUG):
        print("%05d: %s" %(time.time(), s))

def str2bytes(s:str):
    return bytearray(binascii.unhexlify(s))

def bytes2hex(s:str):
    return ':'.join('{:02x}'.format(ord(x)) for x in s)

def bytes2hex(b:bytearray):
    return ':'.join('{:02x}'.format(x) for x in b)

def escape_msg(msg):
    checksum = 0
    for i in range(1, len(msg)):
        checksum += msg[i]

    msg.append(checksum & 0xff)

    i = 0
    while(i < len(msg)):
        if(msg[i] == 0xc0):
            msg[i:i+1] = b'\xdb\xdc'
            i = i + 1
        elif(msg[i] == 0xdb):
            msg[i:i+1] = b'\xdb\xdd'
            i = i + 1
        i = i + 1

    # Add the bookends
    msg = bytearray(b'\xc0' + msg + b'\xc0')
    return msg

def send_msg(msg):
    if (len(msg) > MSGLEN):
        raise InvalidMessage('too big')

    padding = b'\x00' * (MSGLEN - len(msg) - 1)
    msg = msg + padding

    msg = escape_msg(msg)

    # Send it to the bus
    rts.value(1)
    time.sleep(.05)
    c = hpwc.write(msg)
    time.sleep(.05)
    rts.value(0)
    debug("sent(%d):    %s" % (c, bytes2hex(msg)), 1)

def send_master_linkready1():
    debug("send master linkready1", 2)
    send_msg(bytearray(b'\xfc\xe1') + TWCID + BUSID)

def send_master_linkready2():
    debug("send master linkready2", 2)
    send_msg(bytearray(b'\xfb\xe2') + TWCID + BUSID)

def unescape_msg(msg:bytearray):
    # sometimes messages queue up so grab only the first valid msg
    # and this also nicely drops off the 0xfe terminator
    start = msg.index(b'\xc0')
    end = msg[start+1:].index(b'\xc0')
    msg = msg[start:end+2] # +2 because we're already 1 byte in and
                           # we want to keep the end bookmark

    # count db escape markers
    db = 0
    for i in range(1, len(msg)):
        if (msg[i] == 0xdb):
            db = db + 1

    debug("recv(%d, %d): %s" % (len(msg), db, bytes2hex(msg)), 1)

    if len(msg) != 16 + db:
        debug("message has unusual length: %d (%d, %d)" % (len(msg), start, end), 1)
        raise InvalidMessage('length_incorrect')

    # check the bookends
    if not msg.startswith(b'\xc0') and not msg.endswith(b'\xc0'):
        debug("bookends wrong: 0x{:02x} != 0x{:02x}".format(msg[0], msg[-1]), 1)
        raise InvalidMessage('bookends_mismatch')

    # remove the bookends
    msg = msg[1:-1]
    clean = bytearray()

    # unescape escaped bytes
    i = 0
    while i < len(msg):
        if(msg[i] == 0xdb):
            if(msg[i+1] == 0xdc):
                clean.append(0xc0)
            elif(msg[i+1] == 0xdd):
                clean.append(0xdb)
            else:
                debug("unexpected escaped char: 0x{:02x}".format(msg[i+1]), 1)
                clean.append(0xff)
            i = i + 1
        else:
            clean.append(msg[i])
        i = i + 1

    msg = clean
    debug("unescaped: " + bytes2hex(msg), 2)

    # confirm the message is valid after decoding
    sentChkSum = msg[-1]
    calcChkSum = 0
    for i in range(1, len(msg)-1):
        calcChkSum += msg[i]
    calcChkSum = calcChkSum & 0xff

    if calcChkSum != sentChkSum:
        debug("checksum mismatch: 0x{:02x} != 0x{:02x}".format(sentChkSum, calcChkSum), 1)
        raise InvalidMessage('bad message, invalid checksum')

    return msg

def send_master_heartbeat(cmd="idle", arg=None):
    if (cmd == "idle"):
        masterHeartbeatData = bytearray(b'\x00')
    elif (cmd == "limit" and arg != None):
        limit = int(arg*100)
        lsb = bytes([limit & 0xff])
        msb = bytes([limit >> 8])
        masterHeartbeatData = bytearray(b'\x05'+msb+lsb)
    else:
        masterHeartbeatData = bytearray(b'\x00')

    send_msg(bytearray(b'\xfb\xe0') + TWCID + str2bytes(ourSlave['twcid']) + masterHeartbeatData)

def save_config():
    f = open('config.json', 'w')
    f.write(json.dumps(ourSlave))
    f.close()

def read_config():
    global TWCID, BUSID, DEBUG, TOPIC, TOPIC_MASTER, TOPIC_SLAVE, MQTT_SERVER

    # slave config data
    try:
        f = open('config.json', 'r')
        config = json.load(f)
        f.close()
        ourSlave['twcid'] = config['twcid']
        ourSlave['busid'] = config['busid']
        ourSlave['charge_rate'] = config['charge_rate']
        debug(str(ourSlave), 2)
    except Exception as e:
        debug("read_config failed: %s" % str(e), 0)
        pass

    # master config data
    f = open('master_config.json', 'r')
    config = json.load(f)
    f.close()
    TWCID = str2bytes(config['TWCID'])
    BUSID = str2bytes(config['BUSID'])
    DEBUG = config['DEBUG']
    TOPIC = config['TOPIC']
    TOPIC_MASTER = config['TOPIC_MASTER']
    TOPIC_SLAVE = config['TOPIC_SLAVE']
    MQTT_SERVER = config['MQTT_SERVER']
    debug(str(config), 2)

def sub_cb(topic, msg):
    if (topic == bytearray(TOPIC % 'rate')):
        new_rate = float(msg.decode())
        if (ourSlave['charge_rate'] != new_rate):
            ourSlave['charge_rate'] = new_rate
            debug("new charge rate: %2.2f" % ourSlave['charge_rate'], 1)
            save_config()
        else:
            debug("new charge rate is already applied", 2)
    else:
        debug("unknown topic: %s" % topic, 2)

def feedWdt(a):
    wdt.feed()

def rgbled(c):
    if HARDWARE=='pycom':
        pycom.rgbled(0xFF0000)
    else:
        pass

# -----------------------------------------------------------------------------

if HARDWARE=='pycom':
    pycom.heartbeat(False)
    wlan = WLAN(mode=WLAN.STA)
elif HARDWARE=='esp32':
    wlan = WLAN(network.STA_IF)

actled = Pin(LED, mode=Pin.OUT)
actled.value(1)

activity = 0

rgbled(0x000F00)

# some basic unit tests
test_msg = b'\xc0\xfd\xe0\x02\x2d\x77\x77\x01\x03\x20\x02\xdb\xdd\x00\x00\xfe\xc0'
sanity = unescape_msg(test_msg)
print("unescape test 1: " + bytes2hex(sanity))
test_msg = b'\xfd\xe0\x02\x2d\x77\x77\x01\x03\x20\x02\xdb\x00\x00'
sanity = escape_msg(bytearray(test_msg))
print("unescape test 2: " + bytes2hex(sanity))

print("Safety Dance ", end="")
for _ in range(0, 5):
    print("_", end="")
    time.sleep(1)
print(" good to go")

if HARDWARE=='esp32':
    wdt = WDT()
else:
    wdt = WDT(timeout=10000)
wdt.feed()

rts = Pin(RTS, mode=Pin.OUT)
rts.value(0)

debug("Reading config", 1)
read_config()

debug("Connecting to MQTT", 1)
try:
    mqtt_client = MQTTClient(bytes2hex(machine.unique_id()), MQTT_SERVER, keepalive=30)
    mqtt_client.set_callback(sub_cb)
    mqtt_client.set_last_will(topic=TOPIC_MASTER, msg="offline")
    mqtt_client.connect()
    mqtt_client.subscribe(topic=TOPIC % 'rate')
    mqtt_client.publish(topic=TOPIC_MASTER, msg="online")
except:
    debug('unable to connect to mqtt, reseting to reconnect to wifi', 1)
    machine.reset()

debug("Setting up UART", 1)
hpwc = UART(SERIAL, baudrate=9600)
# Pins: TX, RX, RTS, CTS
# hpwc.init(9600, bits=8, parity=None, stop=1, pins=('P3', 'P4', 'P8', None))
hpwc.init(9600, bits=8, parity=None, stop=1)

# Announce ourselves as the master and give slaves a chance to
# change id before talking to us

for _ in range(0, 5):
    send_master_linkready1()
    time.sleep(.1)

wdt.feed()

for _ in range(0, 5):
    send_master_linkready2()
    time.sleep(.1)

try:
    while True:
        gc.collect()

        if not wlan.isconnected():
            debug("wlan disconnected", 1)
            time.sleep(1)
            machine.reset()
        else:
            wdt.feed()

        try:
            led = int(ourSlave['ampsActual'] * (0xE0/ourSlave['maxamps']))
        except ZeroDivisionError:
            led = 0x00
        led += 0x10
        rgbled((led << 16) & (led << 8) & led)

        try:
            any = hpwc.any()
        except:
            try:
                mqtt_client.publish(topic=TOPIC_MASTER, msg="reset(UART.failure)")
            except:
                pass
            debug("UART failure", 0)
            time.sleep(1)
            machine.reset()

        if any:
            rgbled(0x00003F)
            incomingMsg = hpwc.read()
            try:
                slaveMsg = bytearray()
                umsg = unescape_msg(incomingMsg)
                slaveMsg.extend(umsg)
                # slaveUpdate = b'^\xfd\xe2(..)(.)(..)\x00\x00\x00\x00\x00'
                if ourSlave['twcid'] != None:
                    mqtt_client.publish(topic=TOPIC_SLAVE % (ourSlave['twcid']+"/recv"), msg=bytes2hex(umsg))
                if (slaveMsg[0] == 253 and slaveMsg[1] == 226):
                    debug("recv: slave linkready", 1)
                    if (ourSlave['twcid'] == None):
                        ourSlave['twcid'] = "{:02x}{:02x}".format(slaveMsg[2], slaveMsg[3])
                        ourSlave['busid'] = "{:02x}".format(slaveMsg[4])
                        ourSlave['maxamps'] = ((slaveMsg[5] << 8) + slaveMsg[6]) / 100
                        debug("new slave: id=%s, busid=%s, maxamps=%2.2f" % (ourSlave['twcid'], ourSlave['busid'], ourSlave['maxamps']), 1)
                        send_master_heartbeat()
                        try:
                            mqtt_client.publish(topic=TOPIC_SLAVE % ourSlave['twcid'], msg="online")
                        except:
                            debug("mqtt publish failed (1)", 0)
                            time.sleep(1)
                            machine.reset()
                    elif (str2bytes(ourSlave['twcid']) == bytearray(slaveMsg[2:4])):
                        pass # already know about this slave
                    else:
                        debug("warn: unexpected slave linkready from unknown slave, don't support more than 1", 1)
                elif (slaveMsg[0] == 253 and slaveMsg[1] == 224):
                    ampsMax = ((slaveMsg[7] << 8) + slaveMsg[8]) / 100
                    ampsActual = ((slaveMsg[9] << 8) + slaveMsg[10]) / 100
                    if ((ourSlave['ampsActual'] == 0) and (ampsActual > 0)):
                        try:
                            mqtt_client.publish(topic=TOPIC_SLAVE % ourSlave['twcid'], msg="charging")
                        except:
                            debug("mqtt publish failed (2)", 0)
                            time.sleep(1)
                            machine.reset()
                    elif ((ampsActual == 0) and (ourSlave['ampsActual'] > 0)):
                        try:
                            mqtt_client.publish(topic=TOPIC_SLAVE % ourSlave['twcid'], msg="idle")
                        except:
                            debug("mqtt publish failed (3)", 0)
                            time.sleep(1)
                            machine.reset()
                    else:
                        mqtt_client.publish(topic=TOPIC_SLAVE % (ourSlave['twcid']+"/amps"), msg="%2.2f" % ampsActual)
                    # if not `busy` then we want to track
                    if slaveMsg[6] != 5:
                        if ourSlave['state'] != slaveMsg[6]:
                            try:
                                mqtt_client.publish(topic=TOPIC_SLAVE % (ourSlave['twcid']+"/state"), msg=state[slaveMsg[6]], retain=True)
                            except:
                                mqtt_client.publish(topic=TOPIC_SLAVE % (ourSlave['twcid']+"/state"), msg=str(slaveMsg[6]), retain=True)
                        ourSlave['state'] = slaveMsg[6]
                    ourSlave['ampsMax'] = ampsMax
                    ourSlave['ampsActual'] = ampsActual
                    debug("slave: ampsMax=%2.2f, ampsActual=%2.2f" % (ourSlave['ampsMax'], ourSlave['ampsActual']), 1)
                else:
                    rgbled(0xFF0000)
                    debug("recv: unknown message type %d:%d" % (slaveMsg[0], slaveMsg[1]), 1)
            except InvalidMessage as e:
                rgbled(0xFF0000)
                debug("slave message failed checks: %s" % e, 0)
            except ValueError as e:
                rgbled(0xFF0000)
                debug("recv: garbled message: %s" % e, 0)
                ERRORS += 1

        if (time.time() > PING):
            rgbled(0x0F0F00)
            try:
                mqtt_client.ping()
            except:
                debug("mqtt ping failure")
                time.sleep(1)
                machine.reset()
            PING = time.time() + 30

        # If we have a slave talk to it every second
        if (time.time() >= HEARTBEAT):
            HEARTBEAT = time.time() + 1
            rgbled(0x0F000F)

            if activity == 0:
                activity = 1
            else:
                activity = 0
            actled.value(activity)

            debug("mem_free: %d" % gc.mem_free(), 1)
            if (ourSlave['twcid'] != None):
                send_master_heartbeat("limit", ourSlave['charge_rate'])
                # send_master_heartbeat("limit", 15)

        # check for any new messages
        try:
            mqtt_client.check_msg()
        except Exception as e:
            # any errors here indicate a network disconnection
            # easiest way to deal for now is simply reset
            if e.args[0] not in (errno.ENOENT,):
                raise
                debug("mqtt check_msg failed", 0)
                time.sleep(1)
                machine.reset()
            else:
                debug("no mqtt messages waiting", 2)

        if (ERRORS > 10):
            rgbled(0xFF0000)
            try:
                mqtt_client.publish(topic=TOPIC_MASTER, msg="soft_reset")
            except:
                pass
            debug("detected too many failures", 0)
            time.sleep(1)
            machine.reset()
        else:
            machine.idle()
except KeyboardInterrupt:
    from machine import Timer
    if HARDWARE=='pycom':
        t = Timer.Alarm(feedWdt, 5.0, periodic=True)
    elif HARDWARE=='esp32':
        t = Timer(-1)
        t.init(period=2000, mode=Timer.PERIODIC, callback=feedWdt)
except Exception as e:
    rgbled(0xFF0000)
    debug("uncaught exception: %s" % e, 0)
    time.sleep(1)
    # machine.reset()
    raise
