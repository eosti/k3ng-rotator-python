"""Command and control of the K3NG rotator controller"""

import datetime
import logging
import os
import re
import sys
import time
import socket
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import List, Optional
from abc import ABC, abstractmethod

import requests
import rpyc  # type: ignore
import serial

logger = logging.getLogger(__name__)


def exposify(cls):
    """Decorator to append `exposed_` for all public members of a class"""
    for key in dir(cls):
        val = getattr(cls, key)
        if callable(val) and not key.startswith("_"):
            # pylint: disable-next=consider-using-f-string
            setattr(cls, "exposed_%s" % (key,), val)
    return cls


class K3NGException(Exception):
    """General K3NG exception class"""


class K3NGValueException(Exception):
    """Exception to catch value validation exceptions"""


@dataclass
class TLE:
    """Stores a Three-Line Element"""

    title: str
    line_one: str
    line_two: str

    def __post_init__(self):
        self.title = self.title.strip()
        self.line_one = self.line_one.strip()
        self.line_two = self.line_two.strip()


@dataclass
class Satellite:
    """Class to store info about a satellite"""

    id: int
    tle: TLE

    def __init__(self, sat_id: int, tle: Optional[TLE] = None):
        self.id = sat_id
        if tle is None:
            self.retrieve_tle()
        else:
            self.tle = tle

    def retrieve_tle(self) -> TLE:
        """Retrieve a TLE given a NORAD ID"""

        # Try to retrieve from CelesTrak, or else fall back to SatNogs
        params = {"CATNR": str(self.id)}
        resp_cel = requests.get(
            "https://celestrak.org/NORAD/elements/gp.php", params=params, timeout=5
        ).text
        if "No GP data found" not in resp_cel:
            tle_lines = [line.strip() for line in resp_cel.splitlines()]
            self.tle = TLE(tle_lines[0], tle_lines[1], tle_lines[2])
        else:
            params = {"format": "json", "norad_cat_id": str(self.id)}
            resp_sat = requests.get(
                "https://db.satnogs.org/api/tle/", params=params, timeout=5
            ).json()
            self.tle = TLE(
                resp_sat[0]["tle0"], resp_sat[0]["tle1"], resp_sat[0]["tle2"]
            )

        # Some TLE titles start with "0 " (i.e. "0 ISS") and others don't ("ISS")
        # We opt to be consistent and NOT start with "0 ".
        if self.tle.title[0:2] == "0 ":
            self.tle.title = self.tle.title[2:]

        # K3NG doesn't like special characters or spaces
        self.tle.title = re.sub("[^A-Za-z0-9 ]+", "", self.tle.title)
        self.tle.title = self.tle.title.replace(" ", "")

        logger.info("Retrieved TLE for NORAD ID %s: %s", self.id, self.tle)

        return self.tle


@dataclass
class PassInfo:
    """Class to store info about the stored pass"""

    start_time: datetime.datetime
    start_az: int
    end_time: datetime.datetime
    end_az: int
    max_el: int

    @classmethod
    def from_status(cls, statestr: str):
        """
        Parse a K3NG message with the expected format:
        Next AOS:YYYY-MM-DD HH:MM:SS Az:XX LOS:YYYY-MM-DD HH:MM:SS Az:XX Max El:XX
        """

        splitstr = statestr.split()
        try:
            aos_date = datetime.datetime.strptime(
                splitstr[1][4:] + " " + splitstr[2], "%Y-%m-%d %H:%M:%S"
            )
            los_date = datetime.datetime.strptime(
                splitstr[4][4:] + " " + splitstr[5], "%Y-%m-%d %H:%M:%S"
            )
        except ValueError as e:
            logger.warning("Unable to parse PassInfo: %s", statestr)
            raise e

        aos_az = int(splitstr[3][3:])
        los_az = int(splitstr[6][3:])
        max_el = int(splitstr[8][3:])

        return cls(aos_date, aos_az, los_date, los_az, max_el)


class SignalState(IntEnum):
    """Class to store the state of a pass"""

    LOS = 0
    AOS = 1

    @classmethod
    def from_str(cls, text: str):
        """Converts K3NG AOS/LOS to a Python object"""
        if text.upper() == "LOS":
            return cls.LOS

        if text.upper() == "AOS":
            return cls.AOS

        raise K3NGValueException(f"State {text} is not in [AOS | LOS]")


@dataclass
class TrackingStatus:
    """Class to store the state of K3NG's tracking"""

    # pylint: disable=too-many-instance-attributes
    satname: str
    sat_state: SignalState
    is_tracking: bool
    cur_az: float
    cur_el: float
    cur_lat: float
    cur_long: float
    next_pass: Optional[PassInfo]
    next_event: SignalState
    next_event_mins: int

    @classmethod
    def from_str(cls, statestr: List[str]):
        """
        Parse a K3NG message with the expected format:
        Satellite:XXXXXX
        AZ:XX EL:XX Lat:XX.XX Long:XX.XX [LOS | AOS] TRACKING_[IN | ]ACTIVE
        [see PassInfo.from_status]
        [AOS | LOS] in XdXhXm
        """
        # pylint: disable=too-many-locals

        sat = statestr[0][10:]
        satinfo = statestr[1].split()
        cur_az = int(satinfo[0][3:])
        cur_el = int(satinfo[1][3:])
        cur_lat = float(satinfo[2][4:])
        cur_long = float(satinfo[3][5:])
        sat_state = SignalState.from_str(satinfo[4])
        is_tracking = satinfo[5] == "TRACKING_ACTIVE"

        try:
            next_pass = PassInfo.from_status(statestr[2])
        except ValueError:
            # This is not an exception because sometimes K3NG gets confused.
            # If next_pass matters, you should verify that it's not None.
            logger.warning("Unable to parse next pass info in tracking status")
            logger.info("Consider manually recalculating the satellite tracking.")
            next_pass = None

        next_event_str = statestr[3].split()
        next_event = SignalState.from_str(next_event_str[0])
        timestring = next_event_str[2].replace("~", "")
        mins = cls.dhm_to_mins(timestring)

        return cls(
            satname=sat,
            cur_az=cur_az,
            cur_el=cur_el,
            cur_lat=cur_lat,
            cur_long=cur_long,
            sat_state=sat_state,
            is_tracking=is_tracking,
            next_pass=next_pass,
            next_event=next_event,
            next_event_mins=mins,
        )

    @staticmethod
    def dhm_to_mins(timestr: str) -> int:
        """Converts a 'XdXhXm' time string to minutes"""
        # Convert all seperators to a common seperator and then split
        timestr_clean = timestr.replace("d", "!").replace("h", "!").replace("m", "!")
        splitstr = timestr_clean.split("!")[:-1]  # Last entry is empty

        if len(splitstr) == 1:
            mins = int(splitstr[0])
        elif len(splitstr) == 2:
            mins = int(splitstr[0]) * 60 + int(splitstr[1])
        else:
            mins = int(splitstr[0]) * 60 * 24 + int(splitstr[1]) * 60 + int(splitstr[2])

        return mins


class K3NG(ABC):
    """Class for controlling K3NG over serial"""

    # pylint: disable=too-many-public-methods

    @abstractmethod
    def read(self) -> list[str]:
        """Read all pending lines"""
        pass

    @abstractmethod
    def write(self, cmd: str) -> None:
        """Send a command"""
        pass

    @abstractmethod
    def flush(self) -> None:
        """Flush input buffer"""
        pass

    @abstractmethod
    def query(self, cmd) -> list[str]:
        """Send a command and get the response"""
        pass

    def query_extended(self, cmd) -> str:
        """Send an extended command and parse the response"""
        time.sleep(0.2)
        if len(cmd) < 2 or "\\?" in cmd:
            raise K3NGValueException("Invalid extended command")

        self.write("\\?" + cmd)

        time.sleep(0.2)

        try:
            resp = self.read()[0]
        except IndexError as ex:
            raise K3NGException("No response from rotator") from ex

        status = resp[0:5]
        if "\\!??" in status:
            raise K3NGException(f"Response error: {resp}")

        if "OK" not in status:
            raise K3NGException(f"Invalid response: {resp}")

        return resp[6:]

    #  ╭──────────────────────────────────────────────────────────╮
    #  │                       Basic Config                       │
    #  ╰──────────────────────────────────────────────────────────╯

    def get_version(self) -> str:
        """Get the version of the K3NG firmware"""
        retval = self.query_extended("CV")
        return retval

    def get_time(self) -> datetime.datetime:
        """Get the stored time on the rotator"""
        retval = self.query("\\C")
        return datetime.datetime.fromisoformat(retval[0])

    def set_time(self, in_time: Optional[str] = None) -> None:
        """Set the time on the rotator to the current UTC time"""
        if in_time is None:
            # Determine UTC time now
            current_time = datetime.datetime.now(tz=datetime.timezone.utc)
            in_time = current_time.strftime("%Y%m%d%H%M%S")
            logger.debug("Setting to current UTC time: %s", current_time)

        if len(in_time) != 14:
            raise K3NGValueException("Invalid time length")

        ret = self.query("\\O" + in_time)
        ret_split = " ".join(ret[0].split(" ")[3:5])
        ret_time = datetime.datetime.fromisoformat(ret_split)

        if abs(ret_time - current_time) > datetime.timedelta(seconds=10):
            raise K3NGException("Time did not save!")

        self.check_time()

    def check_time(self):
        """Verify that the stored time is pretty close to the current time"""
        current_time = datetime.datetime.now(tz=datetime.timezone.utc)
        ret_time = self.get_time()

        if abs(ret_time - current_time) > datetime.timedelta(seconds=10):
            logger.warning("Time difference greater than 10 seconds!")

    def get_loc(self) -> str:
        """Get the stored location from the rotator"""
        # TODO: make this be able to return coords or grid
        return self.query_extended("RG")[0]

    def set_loc(self, loc) -> None:
        """Set the location of the rotator in maidenhead coordinates"""
        if len(loc) != 6:
            raise K3NGValueException("Invalid location length")

        self.query("\\G" + loc)

        # TODO: check retval

    def save_to_eeprom(self) -> None:
        """Store the current configuration to EEPROM"""
        self.write("\\Q")
        # This command restarts, so we reprime the buffer
        time.sleep(1)
        self.flush()
        self.query("\\-")

    #  ╭──────────────────────────────────────────────────────────╮
    #  │                         Movement                         │
    #  ╰──────────────────────────────────────────────────────────╯

    def get_elevation(self) -> float:
        """Get the current elevation"""
        ret = self.query_extended("EL")
        # replace is to accomodate for a quirk in reporting at EL=0
        return float(ret.replace("0-0.", "00.").strip("0"))

    def set_elevation(self, el: float) -> None:
        """Command the rotator to a given elevation"""
        self.query_extended(f"GE{el:05.2f}")

    def get_azimuth(self) -> float:
        """Get the current azimuth"""
        ret = self.query_extended("AZ")
        return float(ret.strip("0"))

    def set_azimuth(self, az: float) -> None:
        """Command the rotator to a given azimuth"""
        self.query_extended(f"GA{az:05.2f}")

    def down(self) -> None:
        """Command the rotator to move down"""
        self.query_extended("RD")

    def up(self) -> None:
        """Command the rotator to move up"""
        self.query_extended("RU")

    def left(self) -> None:
        """Command the rotator to move left"""
        self.query_extended("RL")

    ccw = left

    def right(self) -> None:
        """Command the rotator to move right"""
        self.query_extended("RR")

    cw = right

    def stop_azimuth(self) -> None:
        """Command the rotator to stop moving the azimuth axis"""
        self.query_extended("SA")

    def stop_elevation(self) -> None:
        """Command the rotator to stop moving the elevation axis"""
        self.query_extended("SE")

    def stop(self) -> None:
        """Command the rotator to stop moving all axes"""
        self.query_extended("SS")

    #  ╭──────────────────────────────────────────────────────────╮
    #  │                       Calibration                        │
    #  ╰──────────────────────────────────────────────────────────╯

    def cal_full_up(self) -> int:
        """Set the full up calibration location"""
        ret = self.query_extended("EF")
        return int(ret)

    def cal_full_down(self) -> int:
        """Set the full down calibration location"""
        ret = self.query_extended("EO")
        return int(ret)

    def cal_full_cw(self) -> int:
        """Set the full clockwise calibration location"""
        ret = self.query_extended("AF")
        return int(ret)

    def cal_full_ccw(self) -> int:
        """Set the full counterclockwise calibration location"""
        ret = self.query_extended("AO")
        return int(ret)

    #  ╭──────────────────────────────────────────────────────────╮
    #  │                         Features                         │
    #  ╰──────────────────────────────────────────────────────────╯

    def park(self) -> None:
        """Command the rotator to the parked location"""
        ret = self.query("\\P")
        if "Parking" not in ret[0]:
            raise K3NGException("Not parking")

    def set_park_location(
        self, az: Optional[int] = None, el: Optional[int] = None
    ) -> None:
        """
        Set the park location to the given location
        Pass no arguments to set to current location
        """
        if az is None or el is None:
            az = int(self.get_azimuth())
            el = int(self.get_elevation())

        ret = self.query(f"\\PA{az:03}")
        if str(az) not in ret[0]:
            raise K3NGException("Azimuth park not set")

        ret = self.query(f"\\PE{el:03}")
        if str(el) not in ret[0]:
            raise K3NGException("Elevation park not set")

    def get_park_location(self) -> tuple[int, int]:
        """Get the park location az/el"""
        ret = self.query("\\PA")
        ret_split = ret[0].split(" ")
        return (int(ret_split[2]), int(ret_split[4]))

    def get_autopark(self) -> int:
        """Determine if the rotator is in autopark or not"""
        ret = self.query("\\Y")
        if "Autopark is off" in ret[0]:
            return 0

        return int(ret[0].split()[4])

    def set_autopark(self, duration: int) -> None:
        """
        Set the state of the autopark (time after last movement to park)
        Duration is specified in minutes, set to zero to disable

        WARNING: autopark updates itself every few seconds.
        ADC drift may cause the rotator to slightly adjust itself between updates,
            meaning this parked in location (mostly), but not in lack of motion.
        """
        if duration == 0:
            ret = self.query("\\Y0")
            if "off" not in ret[0]:
                raise K3NGException(f"Autopark not set ({ret[0]})")
        else:
            ret = self.query(f"\\Y {duration:04d}")
            if f"{duration} minute" not in ret[0]:
                raise K3NGException(f"Autopark not set ({ret[0]})")

    def load_tle(self, sat: Satellite) -> None:
        """Load a TLE from internet into the K3NG rotator controller"""
        self.write("\\#")
        time.sleep(0.5)
        self.write(sat.tle.title)
        self.write(sat.tle.line_one)
        self.write(sat.tle.line_two)
        self.write("\r")
        time.sleep(0.5)
        ret = self.read()

        if "corrupt" in ret[0]:
            logger.critical("TLE corrupted on write")
            logger.info(ret)
            raise K3NGException("TLE corrupted")
        if "truncated" in ret[0]:
            logger.critical("File was truncated due to lack of EEPROM storage.")
            logger.info(ret)
            raise K3NGException("TLE truncated")
        if sat.tle.title not in ret[1]:
            logger.critical("TLE not loaded")
            logger.info(ret)
            raise K3NGException("TLE not loaded")

    def load_tle_from_file(self, tle_file: str) -> Satellite:
        """Load a TLE from a file into K3NG rotator controller"""
        with open(tle_file, "r", encoding="utf8") as file:
            tle_file_data = file.readlines()

        sat_tle = TLE(tle_file_data[0], tle_file_data[1], tle_file_data[2])
        sat = Satellite(0, sat_tle)
        self.load_tle(sat)

        return sat

    def read_tles(self) -> list[TLE]:
        """Read the stored TLEs in the K3NG"""
        ret = self.query("\\@")

        tles = []

        i = 1
        while ret[i] != "":
            tles.append(TLE(ret[i], ret[i + 1], ret[i + 2]))
            i = i + 3

        return tles

    def clear_tles(self) -> None:
        """Clear the TLEs stored to the K3NG"""
        ret = self.query("\\!")
        if "Erased the TLE file area" not in ret[0]:
            raise K3NGException("Failed to clear TLEs")

    def get_trackable(self) -> list[str]:
        """Get a list of trackable satellites"""
        ret = self.query("\\|")
        for i, _ in enumerate(ret):
            ret[i] = ret[i].replace("\t", "    ")
        return ret

    def get_tracking_status(self) -> TrackingStatus:
        """Get the state of the K3NG tracking"""
        ret = self.query("\\~")
        if len(ret) == 0:
            raise K3NGException("Unable to get state (no state returned)")
        return TrackingStatus.from_str(ret)

    def select_satellite(self, sat: Satellite) -> None:
        """Select a satellite to track"""
        ret = self.query("\\$" + sat.tle.title[0:5])

        if "Loading" not in ret[1]:
            raise K3NGException("Unable to select satellite")

    def get_next_pass(self, sat: Satellite) -> list[str]:
        """Get the next calculated pass"""
        return self.query(f"\\%{sat.tle.title[0:6]}")

    def enable_tracking(self) -> None:
        """Enable tracking of the seelected satellite"""
        ret = self.query("\\^1")
        if ret[0] != "Satellite tracking activated.":
            logger.error(ret)
            raise K3NGException("Tracking not enabled")

    def disable_tracking(self) -> None:
        """Disable tracking of the selected satellite"""
        ret = self.query("\\^0")
        if ret[0] != "Satellite tracking deactivated.":
            logger.error(ret)
            raise K3NGException("Tracking not disabled")

    def recalculate(self) -> None:
        """Recalculate satellite tracking and DISABLES TRACKING"""
        ret = self.query("\\&")
        if ret[0] != "Recalculating all satellites...":
            logger.error(ret)
            raise K3NGException("Was unable to recalculate satellites")

    def load_and_track(self, sat_id: int) -> None:
        """Helper to load and begin tracking a satellite"""
        sat = Satellite(sat_id)
        self.set_time()
        self.load_tle(sat)
        self.check_time()
        self.select_satellite(sat)
        self.enable_tracking()

        # Verify tracking enabled
        status = self.get_tracking_status()
        if not status.is_tracking:
            raise K3NGException("Rotator is not tracking!")

    def get_raw_analog(self, pin: int) -> int:
        """Returns the raw ADC reading of a valid analog pin"""
        if pin < 0 or pin > 5:
            raise K3NGValueException("Invalid pin number")

        retval = self.query_extended(f"AR{pin:02}")

        # Return value is 0{pin}XXXX where XXXX=VAL
        return int(retval[2:])

    def get_raw_voltage(self, pin: int, vref: float = 5.0, numbits: int = 10) -> float:
        """Returns the raw voltage of a valid analog pin"""
        return self.get_raw_analog(pin) * vref / (2**numbits)


class LocalK3NG(K3NG):
    """Control K3NG over a local serial connection"""

    def __init__(
        self, ser_port: str, send_delay: float = 0.03, recv_delay: float = 0.00
    ) -> None:
        self.send_delay = send_delay
        self.recv_delay = recv_delay

        # Ensure we have r/w on device
        self.port = Path(ser_port)
        if not self.port.exists():
            raise FileNotFoundError(self.port)

        if not os.access(
            self.port,
            os.R_OK | os.W_OK,
            effective_ids=(os.access in os.supports_effective_ids),
        ):
            if os.geteuid() != 0:
                logger.critical(
                    "Unable to acquire read/write permissions on %s.\n"
                    + "Please change permissions, or run this script as superuser.",
                    self.port,
                )
                sys.exit(1)

        self.ser = serial.Serial(ser_port, 9600, timeout=1, inter_byte_timeout=0.5)
        self.flush()

        # This is just a dummy command to "prime" the connection
        # IDK why it's needed but the extended commands won't work otherwise
        ret = self.query("\\-")
        if not ret:
            raise K3NGException("Unable to communicate with rotator")

    def read(self) -> list[str]:
        """Read all pending lines in serial buffer"""
        response = []
        line = ""

        while self.ser.in_waiting > 0:
            time.sleep(self.recv_delay)
            ch = self.ser.read()
            ch_decoded = ch.decode("utf-8")
            if ch_decoded in ("\r", "\n"):
                response.append(line)
                line = ""
            else:
                line += ch_decoded

        response = list(filter(None, response))

        logger.debug("RX: %s", str(response))
        return response

    def write(self, cmd: str) -> None:
        """Send a command"""
        logger.debug("TX: %s", cmd)
        for _ in cmd[0]:
            time.sleep(self.send_delay)
            self.ser.write(cmd.encode())
        time.sleep(self.send_delay)
        self.ser.write(("\r").encode())
        time.sleep(0.2)
        self.ser.readline()

    def query(self, cmd) -> list[str]:
        """Send a command and get the response"""
        self.write(cmd)
        time.sleep(0.2)
        return self.read()

    def flush(self) -> None:
        """Flush the input buffer"""
        self.write("\r")
        self.ser.flush()
        self.ser.reset_input_buffer()


class RotctlK3NG(K3NG):
    def __init__(self, host: str, port: int = 4533):
        self.host = host
        self.port = port

    def read(self) -> list[str]:
        response: list[str]
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            socketfile = s.makefile("rbw", buffering=0)
            response = socketfile.read().splitlines()

        logger.debug("RX: %s", str(response))
        return response

    def write(self, cmd: str) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            socketfile = s.makefile("rbw", buffering=0)
            socketfile.write(f"w{cmd}\r".encode())
        logger.debug("TX: %s", cmd)

    def query(self, cmd: str) -> list[str]:
        response: list[str]
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.host, self.port))
            socketfile = s.makefile("rbw", buffering=0)
            socketfile.write(f"w{cmd}\r".encode())
            logger.debug("TX: %s", cmd)
            time.sleep(0.2)
            response = socketfile.read().splitlines()

        logger.debug("RX: %s", str(response))

    def flush(self) -> None:
        pass


@exposify
class ExposedK3NG(K3NG):
    """Exposed K3NG class for RPC"""


class K3NGService(rpyc.Service):
    """K3NG wrapper for a Linux service"""

    DEFAULT_PORT = 18866

    def __init__(self, ser_port: str) -> None:
        self.exposed_k3ng = ExposedK3NG(ser_port)
        self.exposed_k3ng.set_time()
