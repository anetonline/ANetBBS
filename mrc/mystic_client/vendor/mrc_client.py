#!/usr/bin/python3
# ::::: __________________________________________________________________ :::::
# : ____\ ._ ____ _____ __. ____ ___ _______ .__ ______ .__ _____ .__ _. /____ :
# __\ .___! _\__/__    / _|__   / _/_____  __|  \ gRK __|_ \  __  |_ \ !___. /__
# \   ! ___/  |/  /___/  |   \__\ ._/  __\/  \   \___/  |/  \/  \_./  \___ !   /
# /__  (___   /\____\____|\   ____|   /  /___|\   ______.    ____\|\   ___)  __\
#   /____  \_/ ___________ \_/ __ |__/ _______ \_/ ____ |___/ _____ \_/  ____\
# :     /________________________________________________________________\     :
# :::::       +  p  H  E  N  O  M  p  R  O  D  U  C  T  I  O  N  S  +      :::::
# ==============================================================================
#
# -----------------------------------------
# - modName: mrc_client multiplexer       -
# - majorVersion: 1.3                     -
# - minorVersion: 7                       -
# - author: Stackfault                    -
# - publisher: Phenom Productions         -
# - website: https://www.phenomprod.com   -
# - email: stackfault@bottomlessabyss.net -
# - bbs: bbs.bottomlessabyss.net:2023     -
# -----------------------------------------
#
# Based on previous work from Gryphon of Cyberia BBS
#
# The code have been completely reviewed and improved everywhere I could see
# room for improvement without breaking compatibility.
#
# Major changes:
#
# v1.2.5
#
# - Error trapping on all critical locations
# - Socket routine rewrite and now non-blocking
# - Internal auto-restart, no need for an external restart script
# - New commands added and supported by the new server code
# - Message serialization allowing very fast message rate and proper display order
# - Bonus server stats data allowing an in-bbs applet to show MRC status (See samples)
# - Graceful client shutdown notification to the server
# - New BBS information subsystem allowing BBS to provide connection info details
# - New startup check to allow for smoother installation and configuration
#
# v1.2.7
#
# - Update available/Client too old notifications and validation
# - Client latency reporting
# - Added support for upcoming server activity level stats
# - Increase both frequency and tolerance of stats reporting
# - Other smaller fixes
#
# v1.2.9
# - Improved handling of incomplete and invalid packets
# - Implemented client stats transfer
# - Increased tcp buffer size
# - Improved the stats reporting
# - Rebased the message serialization
#
# v1.3.3
# - Python 3 support (2 different scripts)
# - Server is no longer on command-line and now in mrc_config.py
# - SSL support
# - CAPABILITIES support (in preparation for future use)
# - Duplicate process check via pidfile
#
# v1.3.4
# - Minor bug fixes
#
# Make sure to use the new mrc_config.py so you can take advantage of some new
# features.
#
# v1.3.5
#
# - Fixed minor issues in template for Windows hosts
# - Fixed minor issues while run on a Windows host
#
# v1.3.6
#
# - Single script now supported both by Python 2.7.18 and 3.6+
#
# v1.3.7
#
# - Version bump


import os, sys, fnmatch, glob, re, hashlib
import time, socket, errno, platform, traceback

# Import site config
from mrc_config import *

# Pre-Flight checks
try:
    os.chdir(bbspath)
except:
    print("Please configure BBS Path in mrc_config.py")
    sys.exit(1)

if "Unconfigured BBS" in [
    bbsname,
    info_web,
    info_telnet,
    info_ssh,
    info_sysop,
    info_desc,
]:
    print("Please configure your BBS details in mrc_config.py")
    sys.exit(1)

msleep = lambda x: time.sleep(x / 1000.0)

# Change this info
tempdir = "%s%stemp" % (bbspath, os.sep)
datadir = "%s%sdata" % (bbspath, os.sep)
chatdats = "%s%schat*.dat" % (datadir, os.sep)

# Align this path with the MRC MPL (Default: {mrcdatadir}/mrc)
mrcdir = "%s%smrc" % (datadir, os.sep)

# Platform information
version = "1.3.7"
platform_name = "MYSTIC"
system_name = platform.system()
machine_arch = platform.machine()
debugflag = False
version_string = "%s/%s.%s/%s" % (platform_name, system_name, machine_arch, version)
client_version = "Multi Relay Chat Client v%s 2024-09-01 [sf]" % version
capabilities = "MCI CTCP SSL"

# Global vars
intv = [1, 5, 10, 30, 60, 90, 120, 180, 240, 300]  # Auto-restart intervals
timebase = int(time.time())
registry = {}
mrcstats = ""
mrchash = ""


# Console logger
def logger(loginfo):
    ltime = time.asctime(time.localtime(time.time()))
    print("%s  %s" % (ltime, loginfo.strip()))
    sys.stdout.flush()


# Check if already running
def checkpid(c):
    # Check Pid
    if c == 0:
        try:
            p = open(pidfile, "r")
            pid = p.read()
            p.close()
            os.kill(int(pid), 0)
        except:
            return False
        else:
            return True

    # Create pid file
    if c == 1:
        try:
            p = open(pidfile, "w")
            p.write(str(os.getpid()))
            p.close()
            return
        except:
            logger("Error creating pid file")
            sys.exit()

    # Delete pid file
    if c == 2:
        try:
            os.remove(pidfile)
            return
        except:
            logger("Error deleting pid file")


# Strip MCI color codes
def stripmci(text):
    return re.sub(r"\|[0-9]{2}", "", text)


# User chatlog for DLCHATLOG
def chatlog(data):
    if "CLIENT~" not in data and "SERVER~" not in data:
        ltime = time.asctime(time.localtime(time.time()))
        packet = data.split("~")
        message = stripmci(packet[6])
        clogfile = "%s%smrcchat.log" % (mrcdir, os.sep)
        clog = open(clogfile, "a")
        clog.write("%s %s\n" % (ltime, message))
        clog.close()


# Socket sender to server
def send_server(data):
    global registry
    global mrcserver
    if data:
        try:
            regstp = int(time.time() * 1000)
            regstr = data.strip()
            registry[regstr] = regstp
            mrcserver.send(data.encode())
        except:
            logger("Connection error")
            try:
                mrcserver.shutdown(2)
            except:
                pass
            finally:
                mrcserver.close()


# Temp files cleaning routine
def clean_files():
    mrcfiles = os.listdir(mrcdir)
    for file in mrcfiles:
        if fnmatch.fnmatch(file, "*.mrc"):
            mrcfile = "%s%s%s" % (mrcdir, os.sep, file)
            os.remove(mrcfile)


# Read queued file from MRC, ignoring stale files older than 10s
def send_mrc():
    mrcfiles = os.listdir(mrcdir)
    for file in mrcfiles:
        if fnmatch.fnmatch(file, "*.mrc"):
            mrcfile = "%s%s%s" % (mrcdir, os.sep, file)
            ft = os.path.getmtime(mrcfile)

            # Do not forward packets older than 10s
            if time.time() - ft < 10:
                try:
                    # Avoid reading a file still open by the MPL by
                    # opening it read-write to check for locking
                    f = open(mrcfile, "r+")
                    fl = f.readline()
                    f.close()

                    if fl.count("~") > 5:
                        mline = fl.split("~")
                        fromuser = mline[0]
                        frombbs = mline[1]
                        touser = mline[3]
                        message = mline[6]
                        if message == "VERSION":
                            deliver_mrc(
                                "CLIENT~~~%s~%s~~|07- %s~"
                                % (fromuser, frombbs, client_version)
                            )
                            send_server(fl)

                        elif touser == "CLIENT" and message == "LATENCY":
                            deliver_mrc(
                                "SERVER~~~CLIENT~%s~~LATENCY:%s~" % (frombbs, latency)
                            )

                        elif touser == "CLIENT" and message == "STATS":
                            deliver_mrc(
                                "SERVER~~~CLIENT~%s~~STATS:%s~" % (frombbs, mrcstats)
                            )

                        else:
                            send_server(fl)

                        os.remove(mrcfile)
                    else:
                        if debugflag:
                            logger("File %s contains invalid packet" % mrcfile)

                except IOError:
                    if debugflag:
                        logger("MRC file still busy")
                    pass
                except:
                    if debugflag:
                        logger("Error:" + traceback.print_exc())
            else:
                os.remove(mrcfile)


# Write time serialized file for display in MRC
def deliver_mrc(server_data):
    global registry
    global latency
    global mrcstats
    global timebase

    curtime = int(time.time() * 1000)
    if server_data.strip() in list(registry.keys()):
        pkttime = int(registry[server_data.strip()])
        roundtrip = curtime - pkttime
        if roundtrip > 1:
            latency = roundtrip
            if debugflag:
                logger(
                    "LATENCY: Current: %s - Packet: %s = RoundTrip: %s"
                    % (curtime, pkttime, roundtrip)
                )
        registry.clear()

    # Make up a serialized filename based on time
    fileserial = int((time.time() - timebase) * 1000)

    # Wrap the file serial if longer than 8 chars
    if fileserial > 99999999:
        timebase = int(time.time())
        fileserial = int((time.time() - timebase) * 1000)

    # Zeropad the filename
    filename = "%08d.mrc" % fileserial

    try:
        packet = server_data.split("~")
        fromuser = packet[0]
        fromsite = packet[1]
        fromroom = packet[2]
        touser = packet[3]
        tosite = packet[4]
        toroom = packet[5]
        message = packet[6]
    except:
        logger("Bad packet: %s" % server_data)
        return

    # Manage server PINGs
    if fromuser == "SERVER" and message.lower() == "ping":
        send_im_alive()

    # Manage update available notifications
    elif fromuser == "SERVER" and message.startswith("NEWUPDATE:"):
        logger("Upgrade is available, consider upgrading at your earliest convenience")
        logger("You are using version %s" % version)
        logger("Latest version is %s" % message.split(":")[1])

    # Manage old clients
    elif fromuser == "SERVER" and message.startswith("OLDVERSION:"):
        logger("Your client is too old and can no longer be used.")
        logger("You are using version %s" % version)
        logger("Latest version is %s" % message.split(":")[1])
        raise KeyboardInterrupt

    else:

        # Manage server stats
        if fromuser == "SERVER" and message.startswith("STATS:"):
            statsfile = "%s%smrcstats.dat" % (mrcdir, os.sep)
            try:
                f = open(statsfile, "w")
                f.write(message.split(":")[1])
                f.close()
                mrcstats = message.split(":")[1]
            except:
                logger("Cannot write server stats to %s" % statsfile)

        chatlog(server_data)
        for f in glob.iglob(chatdats):
            if not "chatroom" in f:
                chatfile = "%s%schat" % (datadir, os.sep)
                xy = f.replace(chatfile, tempdir)
                xy = xy[:-4]
                inusefile = "%s%stchat.inuse" % (xy, os.sep)
                if os.path.isfile(inusefile):
                    mrcfile = "%s%s%s" % (xy, os.sep, filename)
                    openfile = open(mrcfile, "a")
                    openfile.write(server_data)
                    openfile.close()
                    msleep(5)


# Respond to server PING
def send_im_alive():
    data = "CLIENT~%s~%s~SERVER~~~IMALIVE:%s~\n" % (bbsname, str(os.getpid()), bbsname)
    send_server(data)


# Send capabilities
def send_capabilities():
    data = "CLIENT~%s~%s~SERVER~~~CAPABILITIES:%s~\n" % (bbsname, mrchash, capabilities)
    send_server(data)


# Send graceful shutdown request to server when exited
def send_shutdown():
    data = "CLIENT~%s~~SERVER~~~SHUTDOWN~\n" % bbsname
    send_server(data)


# Request server stats for applet
def request_stats():
    data = "CLIENT~%s~~SERVER~~~STATS~\n" % bbsname
    send_server(data)


# Send BBS additional info for INFO command
def send_bbsinfo():
    prefix = "CLIENT~%s~~SERVER~ALL~~" % bbsname
    packet = prefix + "INFOWEB:%s~\n" % info_web
    packet += prefix + "INFOTEL:%s~\n" % info_telnet
    packet += prefix + "INFOSSH:%s~\n" % info_ssh
    packet += prefix + "INFOSYS:%s~\n" % info_sysop
    packet += prefix + "INFODSC:%s~\n" % info_desc
    send_server(packet)


# Handle different line separator scenarios
def check_separator(data):
    if data.count("\r\n"):
        return "\r\n"
    elif data.count("\n\r"):
        return "\n\r"
    elif data.count("\r"):
        return "\r"
    else:
        return "\n"


# Main process loop
def mainproc():
    global delay
    global mrcserver
    global latency
    global sock
    global context

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)

    restart = 0
    readbuffer = ""
    tdat = ""
    latency = 0

    # Non-blocking socket loop to improve speed
    try:
        trans = "Plain"
        if usessl:
            mrcserver = context.wrap_socket(sock, server_hostname=host)
            trans = "SSL"
            port = sslport
        else:
            # Use plain socket
            mrcserver = sock
            port = plainport

        logger(
            "Attempting connection to %s on port %d using %s transport\n"
            % (host, port, trans)
        )

        mrcserver.connect((host, port))
        mrcserver.setblocking(0)
        handshake = "%s~%s" % (bbsname, version_string)
        mrcserver.send(handshake.encode())

        logger(
            "Connected (%s) to Multi Relay Chat host %s port %d" % (trans, host, port)
        )

        delay = 0
    except:
        logger("Unable to connect to %s:%d" % (host, port))
        return

    send_bbsinfo()
    send_im_alive()
    send_capabilities()

    loop = 1800
    while True:
        msleep(10)
        send_mrc()

        loop += 1

        # Request stats every 20 seconds
        if loop > 2000:
            request_stats()
            loop = 0

        # Here we need two methods for socket, with and without SSL
        # because of failure with the SSL import potentially
        # If we use SSL, we can rely on ssl.SSLWantReadError
        if usessl:

            try:
                readbuffer = mrcserver.recv(16384).decode()

            # Handle SSL socket error
            except ssl.SSLWantReadError:
                continue

            # Handle socket error
            except socket.error as e:
                err = e.args[0]
                if err == errno.EAGAIN or err == errno.EWOULDBLOCK:
                    continue
                else:
                    restart = 1

        # Without SSL, we only rely on socket
        else:
            try:
                readbuffer = mrcserver.recv(16384).decode()

            # Handle socket error
            except socket.error as e:
                err = e.args[0]
                if err == errno.EAGAIN or err == errno.EWOULDBLOCK:
                    continue
                else:
                    restart = 1

        # If we have buffer, we process
        if readbuffer:
            sep = check_separator(readbuffer)
            tdat = readbuffer.split(sep)
            for data in tdat:
                if data:
                    deliver_mrc(data)

        # Handle socket restarts with socket shutdowns
        if restart:
            logger("Lost connection to server\n")
            try:
                mrcserver.shutdown(2)
            finally:
                mrcserver.close()
            return


# Some validation of config to ensure smoother operation
def check_startup():
    global mrchash
    failed = 0

    if not os.path.exists("%s%susers.dat" % (datadir, os.sep)):
        print("The MRC Multiplexer must be started from your BBS directory")
        sys.exit()

    if not os.path.exists(mrcdir):
        os.makedirs(mrcdir)

    if checkpid(0):
        logger("MRC Client is already running, pid file %s" % pidfile)
        sys.exit()

    if len(stripmci(bbsname)) < 5:
        print("Config: 'bbsname' should be set to something sensible")
        failed = 1

    if len(stripmci(bbsname)) > 40:
        print(
            "Config: 'bbsname' cannot be longer than 40 characters after PIPE codes evaluation"
        )
        failed = 1

    for param in ["info_web" "info_telnet", "info_ssh", "info_sysop", "info_desc"]:
        if len(stripmci(param)) > 64:
            print(
                "Config: '%s' cannot be longer than 64 characters after PIPE codes evaluation"
                % param
            )
            failed = 1

    for param in ["info_web" "info_telnet", "info_ssh", "info_sysop", "info_desc"]:
        if len(param) > 128:
            print(
                "Config: '%s' cannot be longer than 128 characters including PIPE codes"
                % param
            )
            failed = 1

    if failed:
        print("This must be fixed in mrc_config.py")
        sys.exit()

    h = open(__file__, "r")
    mrchash = hashlib.sha256(h.read().encode()).hexdigest()
    h.close()


# Main loop
if __name__ == "__main__":
    logger(client_version)

    # Check if we can use SSL on this system
    if usessl:
        try:
            import ssl

            context = ssl.create_default_context()
            if nocertcheck:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            port = sslport
        except:
            logger("! Unable to use SSL, reverting to plain\n")
            usessl = 0
            port = plainport

    check_startup()
    checkpid(1)
    delay = 0
    while True:
        try:
            mainproc()

            # Incremental auto-restart built-in
            logger("Reconnecting in %d seconds" % intv[delay])
            time.sleep(intv[delay])
            delay += 1
            if delay > 9:
                delay = 0

        except KeyboardInterrupt:
            logger("Shutting down")
            try:
                send_shutdown()
                try:
                    mrcserver.shutdown(2)
                except:
                    pass
            finally:
                mrcserver.close()

            checkpid(2)
            sys.exit()
        except:
            continue
